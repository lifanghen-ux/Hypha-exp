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

export function buildPlannerPrompt({ instruction, kernelName, history, phaseIndex, verifierFeedback }) {
  return [
    `You are the planning layer for ${kernelName} on Long-Horizon Terminal-Bench.`,
    "The task runs in a Docker container. You may propose shell commands only.",
    "Return strict JSON only, no markdown, no explanation.",
    "Schema:",
    '{"planned_actions":[{"type":"shell","command":"...","timeout_sec":120},{"type":"finish","content":"..."}]}',
    "Rules:",
    "- Work in /app unless the task instruction says otherwise.",
    "- Follow a closed loop: analyze the latest failure, modify files or dependencies, test the change, then generate required artifacts.",
    "- Phase 1 may inspect. From phase 2 onward, do not return only ls/cat/grep/pip-list commands; include a concrete edit, install, test, or replay step.",
    "- If previous output shows pyproject.toml still has langchain==0.0.1, langchain>=1.3, or pydantic<2, your next plan must edit dependency metadata to exactly langchain==1.3.4 and remove pydantic<2 before more inspection.",
    "- If previous output shows forbidden legacy imports or .predict/.run calls, your next plan must edit the corresponding source files before more inspection.",
    "- Never introduce or keep forbidden shortcut snippets/classes: FakeListLLM, langchain_community.llms.fake, ROUTE_KEYWORDS, ROUTE_RULES, ROUTE_SCORES, LegacyAnswerLLM, LegacyRouterLLM, LegacyFAQRetriever, AnswerRunnable, RunnableAnswerChain, RunnableRouteChain.",
    "- If pytest or replay fails, use that exact failure as the next edit target; do not repeat broad directory listings.",
    "- After editing Python files, run python -m py_compile on the changed files before broader tests.",
    "- Return at most 8 actions per response.",
    "- Use python scripts for multi-file edits when necessary.",
    "- If the instruction lists outputs/replay_results.jsonl, always make sure a replay command writes that file before finishing.",
    "- End with a finish action only after useful work has been attempted and the required artifact has been generated or its failure has been diagnosed.",
    "- Do not include API keys or secrets.",
    "",
    `Current phase: ${Number(phaseIndex ?? 1)}`,
    "",
    "Latest verifier feedback from the previous phase:",
    formatVerifierFeedback(verifierFeedback),
    "",
    "Previous observations from this trial, if any:",
    formatHistory(history ?? []),
    "",
    "Benchmark instruction:",
    instruction,
  ].join("\n");
}

export function formatVerifierFeedback(feedback) {
  if (!feedback || typeof feedback !== "object") return "(none)";
  const parts = [];
  if (feedback.reward !== undefined && feedback.reward !== null) {
    parts.push(`reward: ${String(feedback.reward).trim()}`);
  }
  if (feedback.migration_details) {
    parts.push(`migration_details:\n${JSON.stringify(feedback.migration_details).slice(-5000)}`);
  }
  if (feedback.test_stdout_tail) {
    parts.push(`test_stdout_tail:\n${String(feedback.test_stdout_tail).slice(-4000)}`);
  }
  if (feedback.install_log_tail) {
    parts.push(`install_log_tail:\n${String(feedback.install_log_tail).slice(-3000)}`);
  }
  return parts.length ? parts.join("\n\n") : "(none)";
}

export function formatHistory(history) {
  if (!Array.isArray(history) || history.length === 0) return "(none)";
  const oldSummary = history.slice(0, -4).map((turn) => {
    const phase = turn.phase_index ?? "?";
    const trajectory = Array.isArray(turn.trajectory) ? turn.trajectory : [];
    const commands = trajectory
      .filter((item) => item.type === "shell")
      .map((item) => String(item.command ?? "").slice(0, 120));
    return commands.length ? `phase ${phase}: ${commands.join(" ; ")}` : `phase ${phase}: no shell actions`;
  }).join("\n");
  const recent = history.slice(-4).map((turn) => {
    const phase = turn.phase_index ?? "?";
    const trajectory = Array.isArray(turn.trajectory) ? turn.trajectory : [];
    const lines = trajectory.slice(-4).map((item) => {
      if (item.type !== "shell") return `${item.type}: ${String(item.content ?? "").slice(0, 300)}`;
      const stdout = String(item.stdout ?? "").slice(-900);
      const stderr = String(item.stderr ?? "").slice(-700);
      return [
        `$ ${item.command}`,
        `return_code=${item.return_code}`,
        stdout ? `stdout:\n${stdout}` : "",
        stderr ? `stderr:\n${stderr}` : "",
      ].filter(Boolean).join("\n");
    });
    return `Phase ${phase}:\n${lines.join("\n\n")}`;
  }).join("\n\n---\n\n");
  return [
    oldSummary ? `Earlier phase command summary:\n${oldSummary.slice(-4000)}` : "",
    recent,
  ].filter(Boolean).join("\n\n---\n\n");
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

async function requestPlan({ baseUrl, apiKey, model, messages, maxTokens, temperature, timeoutMs }) {
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

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }

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

export async function planWithOpenAICompatible({ instruction, fallbackActions, kernelName, history, phaseIndex, verifierFeedback }) {
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
  const prompt = buildPlannerPrompt({ instruction, kernelName, history, phaseIndex, verifierFeedback });
  const maxTokens = Number(process.env.LHTB_MAX_TOKENS ?? 3072);
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
      timeoutMs: Number(process.env.LHTB_REQUEST_TIMEOUT_MS ?? 90000),
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
        timeoutMs: Number(process.env.LHTB_RETRY_TIMEOUT_MS ?? 60000),
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
