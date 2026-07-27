import fs from "node:fs";
import path from "node:path";

export function loadDotEnv(repoRoot) {
  const envPath = path.join(repoRoot, ".env");
  if (!fs.existsSync(envPath)) return;
  for (const line of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const index = trimmed.indexOf("=");
    if (index < 0) continue;
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim();
    if (key && process.env[key] === undefined) process.env[key] = value;
  }
}

export function llmEnabled() {
  return String(process.env.LHTB_LLM_ENABLED ?? "").toLowerCase() === "true";
}

export function buildPlannerPrompt({ instruction, kernelName, history }) {
  return [
    `You are the planning layer for ${kernelName} on Long-Horizon Terminal-Bench.`,
    "The task runs in a Docker container. You may propose shell commands only.",
    "Return strict JSON only, no markdown, no explanation.",
    "Schema:",
    '{"planned_actions":[{"type":"shell","command":"...","timeout_sec":120},{"type":"finish","content":"..."}]}',
    "Rules:",
    "- Work in /app unless the task instruction says otherwise.",
    "- Prefer small inspect commands first, then edits/tests.",
    "- Return at most 6 actions per response.",
    "- Use python scripts for multi-file edits when necessary.",
    "- End with a finish action only after useful work has been attempted.",
    "- Do not include API keys or secrets.",
    "",
    "Previous observations from this trial, if any:",
    formatHistory(history ?? []),
    "",
    "Benchmark instruction:",
    instruction,
  ].join("\n");
}

export function formatHistory(history) {
  if (!Array.isArray(history) || history.length === 0) return "(none)";
  return history.slice(-4).map((turn, turnIndex) => {
    const trajectory = Array.isArray(turn.trajectory) ? turn.trajectory : [];
    const lines = trajectory.slice(-6).map((item) => {
      if (item.type !== "shell") return `${item.type}: ${String(item.content ?? "").slice(0, 300)}`;
      const stdout = String(item.stdout ?? "").slice(-1200);
      const stderr = String(item.stderr ?? "").slice(-800);
      return [
        `$ ${item.command}`,
        `return_code=${item.return_code}`,
        stdout ? `stdout:\n${stdout}` : "",
        stderr ? `stderr:\n${stderr}` : "",
      ].filter(Boolean).join("\n");
    });
    return `Turn ${turnIndex + 1}:\n${lines.join("\n\n")}`;
  }).join("\n\n---\n\n");
}

export function normalizePlan(raw) {
  const parsed = typeof raw === "string" ? JSON.parse(extractJsonObject(raw)) : raw;
  const actions = parsed.planned_actions ?? parsed.plannedActions ?? [];
  if (!Array.isArray(actions)) throw new Error("planned_actions must be an array");
  return {
    planned_actions: actions.map((action) => {
      if (action.type === "finish") {
        return { type: "finish", content: String(action.content ?? "Finished.") };
      }
      return {
        type: "shell",
        command: String(action.command ?? ""),
        timeout_sec: Number(action.timeout_sec ?? 120),
        stop_on_error: action.stop_on_error ?? false,
      };
    }).filter((action) => action.type === "finish" || action.command.trim()),
  };
}

export function extractJsonObject(text) {
  const trimmed = String(text ?? "").trim();
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) return trimmed;
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start >= 0 && end > start) return trimmed.slice(start, end + 1);
  throw new Error(`No JSON object found in planner output: ${trimmed.slice(0, 500)}`);
}

function fallbackPlan({ fallbackActions, reason, model, usage }) {
  const actions = Array.isArray(fallbackActions) && fallbackActions.length > 0
    ? fallbackActions
    : [
        { type: "shell", command: "cd /app && pwd && ls -la", timeout_sec: 30, stop_on_error: false },
        { type: "shell", command: "cd /app && python -m pytest -q 2>&1 | tail -120", timeout_sec: 120, stop_on_error: false },
      ];
  return {
    planned_actions: actions,
    note: `Planner fallback used: ${reason}`,
    mode: "fallback_after_llm_error",
    model,
    usage: usage ?? null,
  };
}

function extractPlannerContent(data) {
  const message = data.choices?.[0]?.message ?? {};
  const content = typeof message.content === "string" ? message.content.trim() : "";
  if (content) return { content, source: "content" };

  const reasoning = typeof message.reasoning_content === "string" ? message.reasoning_content.trim() : "";
  if (!reasoning) return { content: "", source: "empty" };

  try {
    return { content: extractJsonObject(reasoning), source: "reasoning_content" };
  } catch {
    return { content: "", source: "reasoning_content_without_json", reasoning };
  }
}

async function requestPlan({ baseUrl, apiKey, model, messages, maxTokens, temperature }) {
  const body = {
    model,
    messages,
    response_format: { type: "json_object" },
    temperature,
    max_tokens: maxTokens,
  };
  if (process.env.LHTB_REASONING_EFFORT) {
    body.reasoning_effort = process.env.LHTB_REASONING_EFFORT;
  }

  const response = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`LLM returned non-JSON HTTP ${response.status}: ${text.slice(0, 500)}`);
  }
  if (!response.ok) {
    throw new Error(`LLM HTTP ${response.status}: ${JSON.stringify(data).slice(0, 1000)}`);
  }
  return data;
}

export async function planWithOpenAICompatible({ instruction, fallbackActions, kernelName, history }) {
  if (!llmEnabled()) {
    return {
      planned_actions: fallbackActions,
      note: "LHTB_LLM_ENABLED is not true; using configured planned_actions.",
      mode: "fallback",
    };
  }

  const apiKey = process.env.OPENAI_API_KEY || process.env.DEEPSEEK_API_KEY;
  if (!apiKey) throw new Error("OPENAI_API_KEY or DEEPSEEK_API_KEY is required for LLM planning");
  const baseUrl = (process.env.OPENAI_BASE_URL || "https://api.deepseek.com").replace(/\/$/, "");
  const model = process.env.LHTB_MODEL || "deepseek-chat";
  const prompt = buildPlannerPrompt({ instruction, kernelName, history });
  const maxTokens = Number(process.env.LHTB_MAX_TOKENS ?? 8192);
  const temperature = Number(process.env.LHTB_TEMPERATURE ?? 0.1);
  const systemMessage = "You produce strict JSON action plans for terminal benchmark agents. Put the JSON in message.content, not in reasoning.";

  let data;
  try {
    data = await requestPlan({
      baseUrl,
      apiKey,
      model,
      messages: [
        { role: "system", content: systemMessage },
        { role: "user", content: prompt },
      ],
      maxTokens,
      temperature,
    });
  } catch (error) {
    return fallbackPlan({ fallbackActions, reason: error instanceof Error ? error.message : String(error), model });
  }

  let extracted = extractPlannerContent(data);
  if (!extracted.content) {
    try {
      data = await requestPlan({
        baseUrl,
        apiKey,
        model,
        messages: [
          { role: "system", content: "Return only a compact JSON object in message.content. No reasoning text." },
          {
            role: "user",
            content: [
              "Your previous response did not contain JSON in message.content.",
              "Now return at most 3 shell actions as strict JSON.",
              "Schema: {\"planned_actions\":[{\"type\":\"shell\",\"command\":\"...\",\"timeout_sec\":120}]}",
              "",
              "Task instruction:",
              instruction.slice(0, 3000),
              "",
              "Previous reasoning excerpt:",
              String(extracted.reasoning ?? "").slice(0, 1200),
            ].join("\n"),
          },
        ],
        maxTokens,
        temperature: 0,
      });
      extracted = extractPlannerContent(data);
    } catch (error) {
      return fallbackPlan({ fallbackActions, reason: error instanceof Error ? error.message : String(error), model, usage: data?.usage });
    }
  }

  if (!extracted.content) {
    return fallbackPlan({
      fallbackActions,
      reason: `LLM response missing message content (${extracted.source})`,
      model,
      usage: data.usage,
    });
  }

  let plan;
  try {
    plan = normalizePlan(extracted.content);
  } catch (error) {
    return fallbackPlan({
      fallbackActions,
      reason: `Could not parse planner JSON from ${extracted.source}: ${error instanceof Error ? error.message : String(error)}`,
      model,
      usage: data.usage,
    });
  }

  return {
    ...plan,
    note: `Generated by ${kernelName} through OpenAI-compatible planner.`,
    mode: "llm",
    model,
    usage: data.usage ?? null,
    content_source: extracted.source,
  };
}
