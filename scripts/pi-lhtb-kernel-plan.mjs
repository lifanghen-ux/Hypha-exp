#!/usr/bin/env node
import fs from "node:fs";
import { Agent } from "../benchmarks/pi-agent/packages/agent/dist/index.js";
import { EventStream } from "../benchmarks/pi-agent/packages/ai/dist/index.js";
import { loadDotEnv, planWithOpenAICompatible } from "./lhtb-llm-planner.mjs";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const plannedActions = input.planned_actions ?? input.plannedActions ?? [];
const events = [];
loadDotEnv(new URL("..", import.meta.url).pathname);
const plannerOutput = await planWithOpenAICompatible({
  instruction: input.instruction ?? "",
  fallbackActions: plannedActions,
  kernelName: "Pi",
  history: input.history ?? [],
  phaseIndex: input.phase_index ?? 1,
  verifierFeedback: input.verifier_feedback ?? null,
});

class MockAssistantStream extends EventStream {
  constructor() {
    super(
      (event) => event.type === "done" || event.type === "error",
      (event) => {
        if (event.type === "done") return event.message;
        if (event.type === "error") return event.error;
        throw new Error("Unexpected Pi stream event type");
      },
    );
  }
}

function usage() {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function createAssistantMessage(text) {
  return {
    role: "assistant",
    content: [{ type: "text", text }],
    api: "openai-responses",
    provider: "openai",
    model: input.model_alias ?? "pi/fake-planner",
    usage: usage(),
    stopReason: "stop",
    timestamp: Date.now(),
  };
}

const streamFn = () => {
  const stream = new MockAssistantStream();
  queueMicrotask(() => {
    stream.push({
      type: "done",
      reason: "stop",
      message: createAssistantMessage(
        JSON.stringify(plannerOutput),
      ),
    });
  });
  return stream;
};

const agent = new Agent({
  streamFn,
  initialState: {
    systemPrompt: input.instruction ?? "",
    model: {
      id: input.model_alias ?? "pi/fake-planner",
      name: input.model_alias ?? "pi/fake-planner",
      api: "openai-responses",
      provider: "openai",
      baseUrl: "https://example.invalid",
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 8192,
      maxTokens: 2048,
    },
    tools: [],
    messages: [],
    thinkingLevel: "off",
  },
});

agent.subscribe((event) => {
  events.push(event);
});

await agent.prompt(input.instruction ?? "");
const assistant = [...agent.state.messages].reverse().find((message) => message.role === "assistant");
const text = assistant?.content?.find((part) => part.type === "text")?.text ?? "{}";
const output = JSON.parse(text);

console.log(JSON.stringify({ status: "completed", output, events }, null, 2));
