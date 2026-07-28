# Pi Real LHTB Protocol Freeze v2

Frozen on 2026-07-28 after fixing batched tool-call budget termination.

## Source versions

- Hypha-exp adapter commit: `132fe0c`
- Pi-agent commit: `cee5ff7520d8828bed9955ef00419e995d1f91e0`
- LHTB commit: `11c5e775a9f5a296744b7e9b9051a9d8ab88f04f`
- PiRealLHTBAgent version: `0.3.0`

## Model

- Provider: `deepseek`
- API compatibility: `openai-completions`
- Model ID: `deepseek-v4-pro`
- Base URL: `https://api.deepseek.com`
- Maximum model output tokens: `3072`

No API key or other secret is recorded in this document.

## Protocol

- Attempts per task: `1`
- Concurrent trials: `1`
- Agent timeout per five-task trial: `1200` seconds
- Tool calls per phase: `4`
- Context message limit: `20`
- Helper timeout per phase: `180` seconds
- Shell timeout hard ceiling: `120` seconds
- Automatic retries: none
- Task-specific guards or answer logic: none

Tool calls beyond the remaining phase budget are removed from the assistant
message before Pi executes the batch. When a batch consumes the remaining
budget, all executed results carry Pi's native `terminate` signal so the agent
loop emits a normal `agent_end`. No synthetic budget-error tool results are
stored in context. Every new helper process creates a fresh budget controller.

## Validation

Deterministic Pi Agent protocol test:

- First phase requested 6 tools, executed 4, and dropped 2.
- No budget-error text entered context.
- No orphan tool calls or tool results were produced.
- A new second phase executed a tool with a fresh budget.

Final Harbor smoke:

- Output: `outputs/lhtb/hypha-exp-lhtb-pi-real-tool-loop-smoke-v5`
- Reward: `0.090818`
- Harbor errors: `0`
- Adapter error: none
- Executed tools: `4`
- Budget exhausted: true
- Model calls: `4`
- Input tokens: `875`
- Output tokens: `792`
- Total tokens including cache usage: `10371`
- Agent elapsed time: `16.061` seconds
- Orphan tool calls/results: `0`
- Budget-error messages: `0`
- Residual Node helper processes: `0`

## Superseded run

The pre-fix five-task run is preserved at:

`outputs/lhtb/hypha-exp-lhtb-pi-real-five-task-protocol-v1`

It must not be used as a Pi performance result. Its LangChain trial exposed the
batched tool-budget protocol defect and ended with `AgentTimeoutError`. The
document-table trial was cancelled when the run was intentionally stopped.

The corrected five-task run uses job name:

`hypha-exp-lhtb-pi-real-five-task-protocol-v2`
