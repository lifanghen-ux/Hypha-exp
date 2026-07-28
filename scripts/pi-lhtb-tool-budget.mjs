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
  baseStreamFn,
  allowedToolName,
}) {
  const limit = Math.max(1, Number(maxToolCalls) || 1);
  const phaseEndingToolCallIds = new Set();
  let executedToolCalls = 0;
  let droppedToolCalls = 0;
  let forceStop = false;

  function streamFn(model, context, options) {
    const target = createAssistantMessageEventStream();
    const remaining = Math.max(0, limit - executedToolCalls);

    (async () => {
      try {
        const source = baseStreamFn(model, context, options);
        for await (const event of source) {
          if (event.type === "done") {
            const truncated = truncateToolCalls(event.message, remaining);
            droppedToolCalls += truncated.droppedToolCalls;
            if (
              truncated.acceptedToolCallIds.length > 0
              && truncated.acceptedToolCallIds.length === remaining
            ) {
              forceStop = true;
              for (const id of truncated.acceptedToolCallIds) {
                phaseEndingToolCallIds.add(id);
              }
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
      forceStop,
      budgetExhausted: executedToolCalls >= limit,
    };
  }

  return {
    streamFn,
    beforeToolCall,
    afterToolCall,
    stats,
  };
}
