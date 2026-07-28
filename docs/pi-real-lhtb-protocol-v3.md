# Real Pi LHTB Protocol v3

## Scope

This protocol adds generic trial-level accounting and stopping controls to the
real Pi adapter. It does not contain task names, task-specific commands,
artifact generation, verifier overrides, or answer logic.

The official LHTB submodule is unchanged and pinned at:

```text
11c5e775a9f5a296744b7e9b9051a9d8ab88f04f
```

The 46 official `linux/amd64` task images are reused from the host Docker
daemon. Trial cleanup may remove containers and volumes, but must not remove or
prune the shared images.

## Frozen revisions

```text
Hypha-exp: 02c681e9d99a2d08e4c5a977a4f8e6279beda0a7
Pi-agent:  cee5ff7520d8828bed9955ef00419e995d1f91e0
LHTB:      11c5e775a9f5a296744b7e9b9051a9d8ab88f04f
Adapter:   pi-real-lhtb 0.4.0
Model:     deepseek-v4-pro
Provider:  deepseek, OpenAI-compatible completions
```

## Generic controls

- Per-phase tool calls remain capped and excess calls are silently removed
  before Pi executes them.
- Tool call and tool result messages remain paired during context compaction.
- Trial counters accumulate across phases for phases, model calls, tool calls,
  and total tokens.
- The next phase receives only its remaining model, tool, and token allowance.
- Reaching a model or token boundary stops the Pi phase without adding a budget
  error message to the conversation.
- Harbor timeout cancellation is recorded as `termination.status=cancelled`
  with `stop_reason=harbor_timeout`; it is not an adapter error.
- Input, cache, and output tokens are populated in Harbor's standard
  `AgentContext`. Reasoning tokens remain in adapter metadata because the
  standard context has no separate reasoning-token field.

## Official Harbor limitation

The pinned Harbor implementation does not expose an agent-requested clean-stop
hook for `continue_until_timeout`. The adapter records `stop_requested` and
stops making model and tool calls after a trial budget is reached, but Harbor
may continue running interim verifiers until its official task timeout.

No LHTB or Harbor source was patched to bypass this behavior. A future clean
outer-loop stop requires an upstream Harbor interface or an experiment-owned
runner used identically for Pi and Hypha. Verifier reward must never be forged
to force the loop to stop.

## Tests

The following checks passed:

```text
uv run python -m py_compile \
  src/hypha_exp/benchmarks/lhtb.py \
  src/hypha_exp/benchmarks/trial_budget.py \
  scripts/test_pi_lhtb_trial_budget.py \
  scripts/test_pi_real_lhtb_adapter.py

uv run python scripts/test_pi_lhtb_trial_budget.py
uv run python scripts/test_pi_real_lhtb_adapter.py
node --check scripts/pi-lhtb-real-agent.mjs
node --check scripts/pi-lhtb-tool-budget.mjs
node scripts/test-pi-lhtb-tool-budget.mjs
```

The tests verify cross-phase accumulation, reduced next-phase allowances,
model and token boundaries, standard Harbor metrics, cancellation
classification, silent tool-call trimming, and zero orphan tool messages.

## 2048 smoke v6

```text
Output:
outputs/lhtb/hypha-exp-lhtb-pi-real-tool-loop-smoke-v6/2048__seNp36Y

Reward:                  0.090818
Phases:                  1
Model calls:             4
Tool calls:              4
Input tokens:            877
Cache tokens:            9216
Output tokens:           1157
Reasoning tokens:        938
Total tokens:            11250
Adapter elapsed:         21.562 seconds
Job elapsed:             43 seconds
Adapter error:           null
Harbor exception:        null
Residual Node processes: 0
Orphan tool calls:       0
Orphan tool results:     0
```

The trial-level limits were not reached, so the trial `stop_reason` is null.
The single Pi phase ended at the existing per-phase tool boundary:

```text
pi_result.stopReason=max_tool_calls
```

The missing best-effort `moves.log` artifact does not indicate an adapter
failure. The official verifier produced its own move log and returned the
reward above. The adapter did not synthesize an artifact.

## Preserved v2 run

The stopped v2 output remains available and was not modified:

```text
outputs/lhtb/hypha-exp-lhtb-pi-real-five-task-protocol-v2
```
