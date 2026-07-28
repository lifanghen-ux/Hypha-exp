#!/usr/bin/env node
import fs from "node:fs";
import readline from "node:readline";
import { Type } from "../benchmarks/pi-agent/packages/ai/dist/index.js";
import { streamSimple } from "../benchmarks/pi-agent/packages/ai/dist/compat.js";
import { Agent } from "../benchmarks/pi-agent/packages/agent/dist/index.js";
import { loadDotEnv } from "./lhtb-llm-planner.mjs";

loadDotEnv(new URL("..", import.meta.url).pathname);

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

const pending = new Map();

function write(event) {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

function finish(code = 0) {
  rl.close();
  process.exitCode = code;
  setImmediate(() => process.exit(code));
}

function compact(value, limit = 4000) {
  return String(value ?? "").slice(-limit);
}

function compactTextBlock(block, limit) {
  if (block?.type !== "text") return block;
  const text = String(block.text ?? "");
  if (text.length <= limit) return block;
  return { ...block, text: `${text.slice(0, Math.floor(limit / 2))}\n...[truncated]...\n${text.slice(-Math.floor(limit / 2))}` };
}

function compactMessage(message) {
  if (!message || typeof message !== "object") return message;
  if (!Array.isArray(message.content)) return message;
  const limit = message.role === "toolResult" ? 2500 : 5000;
  return {
    ...message,
    content: message.content.map((block) => compactTextBlock(block, limit)),
  };
}

function selectContextMessages(messages, maxMessages) {
  if (!Array.isArray(messages) || messages.length <= maxMessages) {
    return messages.map(compactMessage);
  }
  const first = messages[0]?.role === "user" ? [compactMessage(messages[0])] : [];
  const recent = messages.slice(-maxMessages).map(compactMessage);
  return [...first, ...recent];
}

async function readInit() {
  return new Promise((resolve, reject) => {
    const onLine = (line) => {
      if (!line.trim()) {
        rl.once("line", onLine);
        return;
      }
      try {
        resolve(JSON.parse(line));
      } catch (error) {
        reject(error);
      }
    };
    rl.once("line", onLine);
  });
}

function handleToolResultLine(line) {
  if (!line.trim()) return;
  let event;
  try {
    event = JSON.parse(line);
  } catch {
    return;
  }
  if (event.type !== "tool_result") return;
  const entry = pending.get(event.id);
  if (!entry) return;
  pending.delete(event.id);
  entry.resolve(event);
}

function requestTool(args) {
  const id = `tool-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  write({ type: "tool_request", id, args });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`Timed out waiting for tool result ${id}`));
    }, Number(process.env.LHTB_PI_TOOL_TIMEOUT_MS ?? 600000));
    pending.set(id, {
      resolve: (event) => {
        clearTimeout(timer);
        resolve(event);
      },
    });
  });
}

function createModel(input) {
  const baseUrl = (process.env.OPENAI_BASE_URL || "https://api.deepseek.com").replace(/\/$/, "");
  const modelId = process.env.LHTB_MODEL || input.model_alias || "deepseek-chat";
  return {
    id: modelId,
    name: modelId,
    api: "openai-completions",
    provider: "deepseek",
    baseUrl,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: Number(process.env.LHTB_CONTEXT_WINDOW ?? 64000),
    maxTokens: Number(process.env.LHTB_MAX_TOKENS ?? 3072),
    compat: {
      supportsDeveloperRole: false,
      supportsUsageInStreaming: true,
      maxTokensField: "max_tokens",
    },
  };
}

function formatVerifierFeedback(feedback) {
  if (!feedback || typeof feedback !== "object") return "(none)";
  return [
    feedback.reward ? `reward:\n${feedback.reward}` : "",
    feedback.verifier_json ? `verifier_json:\n${JSON.stringify(feedback.verifier_json).slice(-5000)}` : "",
    feedback.test_stdout_tail ? `test_stdout_tail:\n${compact(feedback.test_stdout_tail, 4000)}` : "",
    feedback.install_log_tail ? `install_log_tail:\n${compact(feedback.install_log_tail, 2500)}` : "",
  ].filter(Boolean).join("\n\n") || "(none)";
}

const shellParameters = Type.Object({
  command: Type.String({
    description: "Shell command to run inside the benchmark Docker container. Work in /app unless the task says otherwise.",
  }),
  timeout_sec: Type.Optional(Type.Number({
    description: "Optional command timeout in seconds.",
  })),
});

const shellTool = {
  label: "Shell",
  name: "shell",
  description: "Run a shell command inside the benchmark Docker container and receive stdout, stderr, and return code.",
  parameters: shellParameters,
  executionMode: "sequential",
  execute: async (_toolCallId, args) => {
    const result = await requestTool(args);
    const text = [
      `$ ${result.command ?? args.command}`,
      `return_code=${result.return_code}`,
      result.stdout ? `stdout:\n${compact(result.stdout, 6000)}` : "",
      result.stderr ? `stderr:\n${compact(result.stderr, 3000)}` : "",
    ].filter(Boolean).join("\n");
    return {
      content: [{ type: "text", text }],
      details: result,
    };
  },
};

function countToolResults(messages) {
  return messages.filter((message) => message.role === "toolResult").length;
}

function summarizeEvent(event) {
  const summary = { type: event.type };
  if (event.toolName) summary.toolName = event.toolName;
  if (event.toolCallId) summary.toolCallId = event.toolCallId;
  if (event.message?.role) summary.role = event.message.role;
  if (event.message?.stopReason) summary.stopReason = event.message.stopReason;
  if (event.message?.errorMessage) summary.errorMessage = event.message.errorMessage;
  return summary;
}

try {
  const input = await readInit();
  rl.on("line", handleToolResultLine);
  const maxToolCalls = Number(input.max_tool_calls ?? 6);
  const maxContextMessages = Number(input.max_context_messages ?? process.env.LHTB_PI_MAX_CONTEXT_MESSAGES ?? 24);
  let executedToolCalls = 0;
  let forceStop = false;
  const events = [];

  const systemPrompt = [
    "You are Pi running inside Long-Horizon Terminal-Bench.",
    "You own the planning and tool-use loop. Use the shell tool to inspect, modify, test, and generate required artifacts.",
    "Work in /app unless the task says otherwise.",
    "Do not claim completion until you have run relevant checks or produced the requested files.",
    "Use verifier feedback from previous phases as the next target.",
  ].join("\n");

  const prompt = [
    `Current phase: ${Number(input.phase_index ?? 1)}`,
    "",
    "Verifier feedback from previous phase:",
    formatVerifierFeedback(input.verifier_feedback),
    "",
    "Benchmark instruction:",
    input.instruction ?? "",
  ].join("\n");

  const agent = new Agent({
    streamFn: streamSimple,
    sessionId: input.run_id,
    toolExecution: "sequential",
    getApiKey: () => process.env.OPENAI_API_KEY || process.env.DEEPSEEK_API_KEY,
    transformContext: async (messages) => selectContextMessages(messages, maxContextMessages),
    beforeToolCall: ({ toolCall }) => {
      if (executedToolCalls >= maxToolCalls) {
        forceStop = true;
        return {
          block: true,
          reason: `Tool budget for this phase is exhausted (${maxToolCalls}). Stop this phase and wait for verifier feedback.`,
        };
      }
      if (toolCall.name !== "shell") {
        return { block: true, reason: `Only the shell tool is available, got ${toolCall.name}.` };
      }
      executedToolCalls += 1;
      return undefined;
    },
    afterToolCall: ({ result }) => {
      forceStop = executedToolCalls >= maxToolCalls;
      return forceStop ? { ...result, terminate: true } : undefined;
    },
    initialState: {
      systemPrompt,
      model: createModel(input),
      thinkingLevel: process.env.LHTB_REASONING_EFFORT === "off" ? "off" : "off",
      tools: [shellTool],
      messages: selectContextMessages(Array.isArray(input.messages) ? input.messages : [], maxContextMessages),
    },
  });

  agent.subscribe((event) => {
    events.push(summarizeEvent(event));
  });

  await agent.prompt(prompt);

  const resultPayload = {
    type: "done",
    status: agent.state.errorMessage ? "error" : "completed",
    errorMessage: agent.state.errorMessage,
    messages: agent.state.messages,
    events,
    forceStop,
    executedToolCalls,
    maxToolCalls,
    maxContextMessages,
  };
  const resultPath = input.result_path || `/tmp/pi-lhtb-result-${Date.now()}.json`;
  fs.writeFileSync(resultPath, JSON.stringify(resultPayload, null, 2));
  write({ type: "done", result_path: resultPath });
  finish(0);
} catch (error) {
  write({
    type: "error",
    message: error instanceof Error ? error.stack || error.message : String(error),
  });
  finish(1);
}
