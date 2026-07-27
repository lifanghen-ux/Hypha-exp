#!/usr/bin/env node
import fs from "node:fs";
import { ReActAgentRunner } from "../benchmarks/Hypha/packages/kernel/dist/index.js";
import { loadDotEnv, planWithOpenAICompatible } from "./lhtb-llm-planner.mjs";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const plannedActions = input.planned_actions ?? input.plannedActions ?? [];
const steps = [];
loadDotEnv(new URL("..", import.meta.url).pathname);
const plannerOutput = await planWithOpenAICompatible({
  instruction: input.instruction ?? "",
  fallbackActions: plannedActions,
  kernelName: "Hypha",
  history: input.history ?? [],
});

const inference = {
  async infer(request) {
    return {
      requestId: `${request.runId}:${request.stepId}:fake-inference`,
      modelAlias: request.modelAlias,
      output: {
        action: "finish",
        output: plannerOutput,
      },
      usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
      metadata: { source: "hypha-exp-lhtb-kernel-plan" },
    };
  },
};

const runner = new ReActAgentRunner({
  inference,
  maxIterations: 1,
  onStep(step) {
    steps.push(step);
  },
});

const result = await runner.run({
  runId: input.run_id ?? `hypha-lhtb-${Date.now()}`,
  stepId: "lhtb-plan",
  agent: {
    id: "hypha-lhtb-agent",
    version: "0.1.0",
    name: "Hypha LHTB Agent",
    modelAlias: input.model_alias ?? "hypha/fake-planner",
    systemInstructions: input.instruction ?? "",
    toolRefs: ["shell"],
  },
  input: { instruction: input.instruction ?? "" },
  messages: [{ role: "user", content: input.instruction ?? "" }],
  metadata: { benchmark: "LHTB", task_id: input.task_id ?? null },
});

const finishStep = [...steps].reverse().find((step) => step.phase === "complete");
const output = finishStep?.output?.output ?? finishStep?.output ?? result.output ?? {};
console.log(JSON.stringify({ status: result.status, output, steps }, null, 2));
