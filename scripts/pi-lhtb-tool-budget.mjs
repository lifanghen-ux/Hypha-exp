import { createAssistantMessageEventStream } from "../benchmarks/pi-agent/packages/ai/dist/index.js";

function emptyUsage() {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function errorMessage(model, error) {
  return {
    role: "assistant",
    content: [],
    api: model.api,
    provider: model.provider,
    model: model.id,
    usage: emptyUsage(),
    stopReason: "error",
    errorMessage: error instanceof Error ? error.stack || error.message : String(error),
    timestamp: Date.now(),
  };
}

function truncateToolCalls(message, remaining) {
  if (!message || !Array.isArray(message.content)) {
    return { message, acceptedToolCallIds: [], droppedToolCalls: 0 };
  }
  let accepted = 0;
  let total = 0;
  const acceptedToolCallIds = [];
  const content = message.content.filter((block) => {
    if (block?.type !== "toolCall") return true;
    total += 1;
    if (accepted >= remaining) return false;
    accepted += 1;
    if (block.id) acceptedToolCallIds.push(String(block.id));
    return true;
  });
  const hasToolCalls = acceptedToolCallIds.length > 0;
  return {
    message: {
      ...message,
      content,
      stopReason: hasToolCalls ? message.stopReason : message.stopReason === "toolUse" ? "stop" : message.stopReason,
    },
    acceptedToolCallIds,
    droppedToolCalls: total - accepted,
  };
}

export function createToolBudgetController({
  maxToolCalls,
  maxModelCalls = null,
  maxTotalTokens = null,
  baseStreamFn,
  allowedToolName,
}) {
  const limit = Math.max(0, Number(maxToolCalls) || 0);
  const modelLimit = maxModelCalls == null
    ? Number.POSITIVE_INFINITY
    : Math.max(0, Number(maxModelCalls) || 0);
  const tokenLimit = maxTotalTokens == null
    ? Number.POSITIVE_INFINITY
    : Math.max(0, Number(maxTotalTokens) || 0);
  const phaseEndingToolCallIds = new Set();
  let executedToolCalls = 0;
  let droppedToolCalls = 0;
  let modelCalls = 0;
  let totalTokens = 0;
  let forceStop = false;
  let stopReason = null;

  function markStop(reason, toolCallIds = []) {
    forceStop = true;
    stopReason ??= reason;
    for (const id of toolCallIds) {
      phaseEndingToolCallIds.add(id);
    }
  }

  function streamFn(model, context, options) {
    const target = createAssistantMessageEventStream();
    const remaining = Math.max(0, limit - executedToolCalls);

    (async () => {
      try {
        if (modelCalls >= modelLimit || totalTokens >= tokenLimit) {
          markStop(
            modelCalls >= modelLimit ? "max_model_calls" : "max_total_tokens",
          );
          const message = {
            role: "assistant",
            content: [],
            api: model.api,
            provider: model.provider,
            model: model.id,
            usage: emptyUsage(),
            stopReason: "stop",
            timestamp: Date.now(),
          };
          target.push({ type: "done", reason: "stop", message });
          return;
        }

        const source = baseStreamFn(model, context, options);
        for await (const event of source) {
          if (event.type === "done") {
            modelCalls += 1;
            totalTokens += Number(event.message?.usage?.totalTokens ?? 0);
            const truncated = truncateToolCalls(event.message, remaining);
            droppedToolCalls += truncated.droppedToolCalls;
            const modelBudgetReached = modelCalls >= modelLimit;
            const tokenBudgetReached = totalTokens >= tokenLimit;
            if (
              truncated.acceptedToolCallIds.length > 0
              && truncated.acceptedToolCallIds.length === remaining
            ) {
              markStop("max_tool_calls", truncated.acceptedToolCallIds);
            }
            if (modelBudgetReached) {
              markStop("max_model_calls", truncated.acceptedToolCallIds);
            } else if (tokenBudgetReached) {
              markStop("max_total_tokens", truncated.acceptedToolCallIds);
            }
            target.push({ ...event, message: truncated.message });
          } else if (event.type === "error") {
            target.push(event);
          } else {
            const truncated = truncateToolCalls(event.partial, remaining);
            target.push({ ...event, partial: truncated.message });
          }
        }
      } catch (error) {
        target.push({
          type: "error",
          reason: "error",
          error: errorMessage(model, error),
        });
      }
    })();

    return target;
  }

  function beforeToolCall({ toolCall }) {
    if (toolCall.name !== allowedToolName) {
      return {
        block: true,
        reason: `Only the ${allowedToolName} tool is available, got ${toolCall.name}.`,
      };
    }
    if (executedToolCalls >= limit) {
      return {
        block: true,
        reason: "Internal tool budget invariant violated.",
      };
    }
    executedToolCalls += 1;
    return undefined;
  }

  function afterToolCall({ toolCall, result }) {
    if (!phaseEndingToolCallIds.has(String(toolCall.id))) return undefined;
    return { ...result, terminate: true };
  }

  function stats() {
    return {
      maxToolCalls: limit,
      executedToolCalls,
      droppedToolCalls,
      maxModelCalls: Number.isFinite(modelLimit) ? modelLimit : null,
      modelCalls,
      maxTotalTokens: Number.isFinite(tokenLimit) ? tokenLimit : null,
      totalTokens,
      forceStop,
      budgetExhausted: executedToolCalls >= limit,
      stopReason,
    };
  }

  return {
    streamFn,
    beforeToolCall,
    afterToolCall,
    stats,
  };
}
