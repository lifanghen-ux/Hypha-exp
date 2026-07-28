#!/usr/bin/env node
import fs from "node:fs";
import readline from "node:readline";
import { Type } from "../benchmarks/pi-agent/packages/ai/dist/index.js";
import { streamSimple } from "../benchmarks/pi-agent/packages/ai/dist/compat.js";
import { Agent } from "../benchmarks/pi-agent/packages/agent/dist/index.js";
import { loadDotEnv } from "./lhtb-llm-planner.mjs";
import { createToolBudgetController } from "./pi-lhtb-tool-budget.mjs";

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

function toolCallIds(message) {
  if (message?.role !== "assistant" || !Array.isArray(message.content)) return [];
  return message.content
    .filter((block) => block?.type === "toolCall" && block.id)
    .map((block) => String(block.id));
}

function toolResultId(message) {
  return message?.role === "toolResult" && message.toolCallId
    ? String(message.toolCallId)
    : null;
}

function buildCompleteInteractionGroups(messages) {
  const groups = [];
  let droppedOrphanMessages = 0;
  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    if (message?.role === "toolResult") {
      droppedOrphanMessages += 1;
      continue;
    }

    const callIds = toolCallIds(message);
    if (callIds.length === 0) {
      groups.push([message]);
      continue;
    }

    const expected = new Set(callIds);
    const group = [message];
    let cursor = index + 1;
    while (cursor < messages.length && messages[cursor]?.role === "toolResult") {
      const resultId = toolResultId(messages[cursor]);
      if (resultId && expected.has(resultId)) {
        group.push(messages[cursor]);
        expected.delete(resultId);
      } else {
        droppedOrphanMessages += 1;
      }
      cursor += 1;
    }
    index = cursor - 1;
    if (expected.size === 0) {
      groups.push(group);
    } else {
      droppedOrphanMessages += group.length;
    }
  }
  return { groups, droppedOrphanMessages };
}

function selectContextMessages(messages, maxMessages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    return [];
  }
  const limit = Math.max(1, Number(maxMessages) || 1);
  const firstUser = messages[0]?.role === "user" ? messages[0] : null;
  const remainingMessages = firstUser ? messages.slice(1) : messages;
  const { groups } = buildCompleteInteractionGroups(remainingMessages);
  const selected = [];
  let available = limit - (firstUser ? 1 : 0);
  for (let index = groups.length - 1; index >= 0; index -= 1) {
    const group = groups[index];
    if (group.length > available) continue;
    selected.unshift(...group);
    available -= group.length;
    if (available === 0) break;
  }
  return [firstUser, ...selected].filter(Boolean).map(compactMessage);
}

function contextIntegrity(messages) {
  const calls = new Set();
  const results = new Set();
  for (const message of messages) {
    for (const id of toolCallIds(message)) calls.add(id);
    const resultId = toolResultId(message);
    if (resultId) results.add(resultId);
  }
  return {
    toolCallCount: calls.size,
    toolResultCount: results.size,
    orphanToolCallIds: [...calls].filter((id) => !results.has(id)),
    orphanToolResultIds: [...results].filter((id) => !calls.has(id)),
  };
}

function summarizeUsage(messages) {
  const summary = {
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
    model_calls: 0,
  };
  for (const message of messages) {
    if (message?.role !== "assistant" || !message.usage) continue;
    summary.input_tokens += Number(message.usage.input ?? 0);
    summary.output_tokens += Number(message.usage.output ?? 0);
    summary.cache_read_tokens += Number(message.usage.cacheRead ?? 0);
    summary.cache_write_tokens += Number(message.usage.cacheWrite ?? 0);
    summary.reasoning_tokens += Number(message.usage.reasoning ?? 0);
    summary.total_tokens += Number(message.usage.totalTokens ?? 0);
    summary.model_calls += 1;
  }
  return summary;
}

function subtractUsage(total, baseline) {
  return Object.fromEntries(
    Object.keys(total).map((key) => [key, Math.max(0, Number(total[key] ?? 0) - Number(baseline[key] ?? 0))]),
  );
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
  const maxModelCalls = input.max_model_calls == null ? null : Number(input.max_model_calls);
  const maxTotalTokens = input.max_total_tokens == null ? null : Number(input.max_total_tokens);
  const maxContextMessages = Number(input.max_context_messages ?? process.env.LHTB_PI_MAX_CONTEXT_MESSAGES ?? 24);
  const events = [];
  const toolBudget = createToolBudgetController({
    maxToolCalls,
    maxModelCalls,
    maxTotalTokens,
    baseStreamFn: streamSimple,
    allowedToolName: "shell",
  });
  const initialMessages = selectContextMessages(
    Array.isArray(input.messages) ? input.messages : [],
    maxContextMessages,
  );
  const initialUsage = summarizeUsage(initialMessages);

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
    streamFn: toolBudget.streamFn,
    sessionId: input.run_id,
    toolExecution: "sequential",
    getApiKey: () => process.env.OPENAI_API_KEY || process.env.DEEPSEEK_API_KEY,
    transformContext: async (messages) => selectContextMessages(messages, maxContextMessages),
    beforeToolCall: toolBudget.beforeToolCall,
    afterToolCall: toolBudget.afterToolCall,
    initialState: {
      systemPrompt,
      model: createModel(input),
      thinkingLevel: process.env.LHTB_REASONING_EFFORT === "off" ? "off" : "off",
      tools: [shellTool],
      messages: initialMessages,
    },
  });

  agent.subscribe((event) => {
    events.push(summarizeEvent(event));
  });

  await agent.prompt(prompt);
  const finalMessages = agent.state.messages;
  const selectedFinalContext = selectContextMessages(finalMessages, maxContextMessages);
  const finalUsage = summarizeUsage(finalMessages);
  const budgetStats = toolBudget.stats();

  const resultPayload = {
    type: "done",
    status: agent.state.errorMessage ? "error" : "completed",
    errorMessage: agent.state.errorMessage,
    messages: finalMessages,
    events,
    forceStop: budgetStats.forceStop,
    executedToolCalls: budgetStats.executedToolCalls,
    droppedToolCalls: budgetStats.droppedToolCalls,
    budgetExhausted: budgetStats.budgetExhausted,
    stopReason: budgetStats.stopReason,
    maxToolCalls,
    maxModelCalls,
    maxTotalTokens,
    modelCalls: budgetStats.modelCalls,
    observedTotalTokens: budgetStats.totalTokens,
    maxContextMessages,
    usage: {
      phase: subtractUsage(finalUsage, initialUsage),
      all_messages: finalUsage,
    },
    context: {
      stored_message_count: finalMessages.length,
      selected_message_count: selectedFinalContext.length,
      selected_integrity: contextIntegrity(selectedFinalContext),
    },
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
