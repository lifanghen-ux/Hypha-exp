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

  const response = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      messages: [
        { role: "system", content: "You produce strict JSON action plans for terminal benchmark agents." },
        { role: "user", content: prompt },
      ],
      response_format: { type: "json_object" },
      temperature: Number(process.env.LHTB_TEMPERATURE ?? 0.2),
      max_tokens: Number(process.env.LHTB_MAX_TOKENS ?? 4096),
    }),
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
  const content = data.choices?.[0]?.message?.content;
  if (!content) throw new Error(`LLM response missing message content: ${JSON.stringify(data).slice(0, 1000)}`);
  return {
    ...normalizePlan(content),
    note: `Generated by ${kernelName} through OpenAI-compatible planner.`,
    mode: "llm",
    model,
    usage: data.usage ?? null,
  };
}
