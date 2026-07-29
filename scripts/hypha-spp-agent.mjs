#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ReActAgentRunner } from "../benchmarks/Hypha/packages/kernel/dist/index.js";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");

function loadDotEnv(root) {
  const envPath = path.join(root, ".env");
  if (!fs.existsSync(envPath)) return;
  for (const line of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const separator = trimmed.indexOf("=");
    if (separator < 1) continue;
    const key = trimmed.slice(0, separator).trim();
    let value = trimmed.slice(separator + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function tokenUsage(usage = {}) {
  const promptDetails = usage.prompt_tokens_details ?? usage.input_tokens_details ?? {};
  const completionDetails =
    usage.completion_tokens_details ?? usage.output_tokens_details ?? {};
  const inputTokens = Number(usage.prompt_tokens ?? usage.input_tokens ?? 0);
  const outputTokens = Number(usage.completion_tokens ?? usage.output_tokens ?? 0);
  return {
    inputTokens,
    cachedInputTokens: Number(
      promptDetails.cached_tokens ?? usage.cached_input_tokens ?? 0,
    ),
    outputTokens,
    thinkingTokens: Number(
      completionDetails.reasoning_tokens ?? usage.reasoning_tokens ?? 0,
    ),
    totalTokens: Number(usage.total_tokens ?? inputTokens + outputTokens),
  };
}

function normalizeMessages(request) {
  const requestInput = request.input ?? {};
  const messages = [];
  if (requestInput.instructions) {
    messages.push({ role: "system", content: String(requestInput.instructions) });
  }
  for (const message of requestInput.messages ?? []) {
    if (!["system", "user", "assistant", "tool"].includes(message.role)) continue;
    messages.push({ role: message.role, content: String(message.content ?? "") });
  }
  return messages;
}

class OpenAICompatibleInference {
  constructor(config) {
    this.id = "hypha-spp-openai-compatible";
    this.config = config;
    this.calls = [];
  }

  async infer(request) {
    const body = {
      model: this.config.model,
      messages: normalizeMessages(request),
      temperature: this.config.temperature,
      max_tokens: this.config.maxTokens,
    };
    const startedAt = Date.now();
    let lastError;
    for (let attempt = 0; attempt <= this.config.retries; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.config.timeoutMs);
      try {
        const response = await fetch(`${this.config.baseUrl}/chat/completions`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${this.config.apiKey}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        const responseText = await response.text();
        let data;
        try {
          data = JSON.parse(responseText);
        } catch {
          throw new Error(
            `Model endpoint returned non-JSON HTTP ${response.status}: ${responseText.slice(0, 500)}`,
          );
        }
        if (!response.ok) {
          const error = new Error(
            `Model endpoint returned HTTP ${response.status}: ${JSON.stringify(data).slice(0, 800)}`,
          );
          error.retryable = response.status === 429 || response.status >= 500;
          throw error;
        }
        const message = data.choices?.[0]?.message ?? {};
        const content =
          typeof message.content === "string" ? message.content.trim() : "";
        if (!content) {
          throw new Error("Model response did not contain message.content");
        }
        const usage = tokenUsage(data.usage);
        const call = {
          requestId: data.id ?? null,
          model: data.model ?? this.config.model,
          finishReason: data.choices?.[0]?.finish_reason ?? null,
          elapsedMs: Date.now() - startedAt,
          attempt: attempt + 1,
          usage,
        };
        this.calls.push(call);
        return {
          id: data.id ?? `${request.runId}:${request.stepId}:inference`,
          output: { action: "finish", output: content },
          usage: {
            inputTokens: usage.inputTokens,
            outputTokens: usage.outputTokens,
            totalTokens: usage.totalTokens,
          },
          metadata: {
            provider: this.id,
            finishReason: call.finishReason,
            cachedInputTokens: usage.cachedInputTokens,
            thinkingTokens: usage.thinkingTokens,
          },
        };
      } catch (error) {
        lastError = error;
        const retryable =
          error?.name === "AbortError" ||
          error?.retryable === true ||
          error instanceof TypeError;
        if (!retryable || attempt >= this.config.retries) throw error;
        await sleep(Math.min(1000 * 2 ** attempt, 8000));
      } finally {
        clearTimeout(timer);
      }
    }
    throw lastError;
  }
}

loadDotEnv(repoRoot);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const apiKey =
  process.env.HYPHA_API_KEY ||
  process.env.OPENAI_API_KEY ||
  process.env.DEEPSEEK_API_KEY;
if (!apiKey) {
  throw new Error(
    "HYPHA_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY is required",
  );
}
const baseUrl = (
  process.env.HYPHA_OPENAI_BASE_URL ||
  process.env.OPENAI_BASE_URL ||
  "https://api.deepseek.com"
).replace(/\/$/, "");
const model = input.model || process.env.HYPHA_MODEL;
if (!model) throw new Error("A Hypha model is required");

const inference = new OpenAICompatibleInference({
  apiKey,
  baseUrl,
  model,
  temperature: Number(input.temperature ?? 0),
  maxTokens: Number(input.max_tokens ?? 8192),
  retries: Math.max(0, Number(input.retries ?? 2)),
  timeoutMs: Number(input.request_timeout_ms ?? 120000),
});
const steps = [];
const runner = new ReActAgentRunner({
  inference,
  maxIterations: 1,
  executionBudget: {
    maxIterations: 1,
    maxModelCalls: 1,
    maxToolCalls: 1,
  },
  onStep(step) {
    steps.push({ id: step.id, phase: step.phase });
  },
});
const result = await runner.run({
  runId: input.run_id,
  stepId: "spp-solve",
  sessionId: input.session_id,
  agent: {
    id: "hypha-spp-agent",
    version: "0.1.0",
    name: "Hypha SPP Agent",
    modelAlias: model,
    systemInstructions:
      "Solve the benchmark task faithfully. Follow its output format exactly.",
    toolRefs: [],
  },
  input: { task: input.task },
  messages: [{ role: "user", content: String(input.task ?? "") }],
  metadata: { benchmark: "SPP", adapter: "hypha-spp-v1" },
});
const resultError =
  result.error instanceof Error
    ? result.error.message
    : result.error
      ? String(result.error)
      : null;

const totals = inference.calls.reduce(
  (sum, call) => {
    sum.modelCalls += 1;
    for (const key of [
      "inputTokens",
      "cachedInputTokens",
      "outputTokens",
      "thinkingTokens",
      "totalTokens",
    ]) {
      sum[key] += call.usage[key];
    }
    return sum;
  },
  {
    modelCalls: 0,
    inputTokens: 0,
    cachedInputTokens: 0,
    outputTokens: 0,
    thinkingTokens: 0,
    totalTokens: 0,
  },
);

process.stdout.write(
  JSON.stringify({
    status: result.status,
    runId: input.run_id,
    sessionId: input.session_id,
    output: result.output ?? null,
    error: resultError,
    model,
    provider: inference.id,
    endpoint: baseUrl,
    usage: totals,
    calls: inference.calls,
    hyphaSteps: result.steps.map((step) => ({
      id: step.id,
      phase: step.phase,
      ...(step.phase === "fail" ? { output: step.output ?? null } : {}),
    })),
  }),
);
