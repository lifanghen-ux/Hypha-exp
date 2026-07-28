# Pi Real LHTB Protocol Freeze

Frozen on 2026-07-28 before the five-task cross-task protocol run.

## Source versions

- Hypha-exp adapter freeze commit: `f904bbb`
- Pi-agent commit: `cee5ff7520d8828bed9955ef00419e995d1f91e0`
- LHTB commit: `11c5e775a9f5a296744b7e9b9051a9d8ab88f04f`

## Model

- Provider: `deepseek`
- API compatibility: `openai-completions`
- Model ID: `deepseek-v4-pro`
- Base URL: `https://api.deepseek.com`
- Reasoning effort environment value: `medium`
- Maximum model output tokens: `3072`

No API key or other secret is recorded in this document.

## Frozen protocol

- Agent: `hypha_exp.benchmarks.lhtb:PiRealLHTBAgent`
- Attempts per task: `1`
- Concurrent trials: `1`
- Agent timeout per five-task trial: `1200` seconds
- Tool calls per phase: `4`
- Context message limit: `20`
- Helper timeout per phase: `180` seconds
- Shell timeout hard ceiling: `120` seconds
- Automatic retries: none
- Task-specific guards or answer logic: none

The final freeze smoke used three tool calls per phase and the same context,
helper, and shell-timeout settings. Its output is:

`outputs/lhtb/hypha-exp-lhtb-pi-real-tool-loop-smoke-v3`

Smoke result:

- Reward: `0.090818`
- Harbor errors: `0`
- Adapter error: none
- Tool calls: `3`
- Model calls: `3`
- Input tokens: `605`
- Output tokens: `415`
- Total tokens including cache usage: `6652`
- Agent elapsed time: `8.315` seconds
- Orphan tool calls/results: `0`
- Residual `pi-lhtb-real-agent` Node processes: `0`
