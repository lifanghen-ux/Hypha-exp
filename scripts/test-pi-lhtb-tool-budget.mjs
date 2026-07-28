#!/usr/bin/env node
import assert from "node:assert/strict";
import { Type, createAssistantMessageEventStream } from "../benchmarks/pi-agent/packages/ai/dist/index.js";
import { Agent } from "../benchmarks/pi-agent/packages/agent/dist/index.js";
import { createToolBudgetController } from "./pi-lhtb-tool-budget.mjs";

const model = {
  id: "protocol-test",
  name: "protocol-test",
  api: "test",
  provider: "test",
  baseUrl: "",
  reasoning: false,
  input: ["text"],
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  contextWindow: 1000,
  maxTokens: 100,
};

function usage() {
  return {
    input: 1,
    output: 1,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 2,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function assistantMessage(toolCount) {
  return {
    role: "assistant",
    content: Array.from({ length: toolCount }, (_, index) => ({
      type: "toolCall",
      id: `call-${index + 1}`,
      name: "shell",
      arguments: { command: `echo ${index + 1}` },
    })),
    api: model.api,
    provider: model.provider,
    model: model.id,
    usage: usage(),
    stopReason: toolCount > 0 ? "toolUse" : "stop",
    timestamp: Date.now(),
  };
}

function fakeStreamSequence(toolCounts) {
  let callIndex = 0;
  return () => {
    const stream = createAssistantMessageEventStream();
    const message = assistantMessage(toolCounts[callIndex] ?? 0);
    callIndex += 1;
    queueMicrotask(() => {
      stream.push({ type: "start", partial: { ...message, content: [] } });
      stream.push({
        type: "done",
        reason: message.stopReason === "toolUse" ? "toolUse" : "stop",
        message,
      });
    });
    return stream;
  };
}

const shellTool = {
  label: "Shell",
  name: "shell",
  description: "Protocol test shell.",
  parameters: Type.Object({ command: Type.String() }),
  executionMode: "sequential",
  execute: async (_id, args) => ({
    content: [{ type: "text", text: args.command }],
    details: {},
  }),
};

async function runPhase(toolCounts, maxToolCalls, initialMessages = []) {
  const budget = createToolBudgetController({
    maxToolCalls,
    baseStreamFn: fakeStreamSequence(toolCounts),
    allowedToolName: "shell",
  });
  const agent = new Agent({
    streamFn: budget.streamFn,
    toolExecution: "sequential",
    beforeToolCall: budget.beforeToolCall,
    afterToolCall: budget.afterToolCall,
    initialState: {
      systemPrompt: "Protocol test.",
      model,
      thinkingLevel: "off",
      tools: [shellTool],
      messages: initialMessages,
    },
  });
  await agent.prompt("Run the phase.");
  return { messages: agent.state.messages, stats: budget.stats() };
}

function pairing(messages) {
  const calls = new Set();
  const results = new Set();
  for (const message of messages) {
    if (message.role === "assistant") {
      for (const block of message.content) {
        if (block.type === "toolCall") calls.add(block.id);
      }
    } else if (message.role === "toolResult") {
      results.add(message.toolCallId);
      assert.equal(message.isError, false);
    }
  }
  return {
    orphanCalls: [...calls].filter((id) => !results.has(id)),
    orphanResults: [...results].filter((id) => !calls.has(id)),
  };
}

const firstPhase = await runPhase([6], 4);
assert.deepEqual(firstPhase.stats, {
  maxToolCalls: 4,
  executedToolCalls: 4,
  droppedToolCalls: 2,
  forceStop: true,
  budgetExhausted: true,
});
assert.deepEqual(pairing(firstPhase.messages), { orphanCalls: [], orphanResults: [] });
assert.equal(
  JSON.stringify(firstPhase.messages).includes("Tool budget"),
  false,
  "Budget errors must not enter context.",
);

const secondPhase = await runPhase([1, 0], 4, firstPhase.messages);
assert.equal(secondPhase.stats.executedToolCalls, 1);
assert.equal(secondPhase.stats.droppedToolCalls, 0);
assert.equal(secondPhase.stats.forceStop, false);
assert.deepEqual(pairing(secondPhase.messages), { orphanCalls: [], orphanResults: [] });

console.log(JSON.stringify({
  firstPhase: firstPhase.stats,
  secondPhase: secondPhase.stats,
  budgetErrorsInContext: false,
  orphanMessages: 0,
}));
