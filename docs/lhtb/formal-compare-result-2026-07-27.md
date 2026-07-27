# LHTB Hypha vs Pi Formal Compare Smoke, 2026-07-27

This run validates the formal comparison harness for Hypha and Pi on LHTB.

It is not a final problem-solving score yet. Both agents use a deterministic
kernel-plan smoke action (`pwd && ls -la | head -20`) so the result mainly
proves that both repositories can be wrapped into the same Harbor/LHTB benchmark
interface and scored by the same verifier.

## Environment

- Experiment repo: `/root/projects/Hypha-exp`
- Benchmark: LHTB
- LHTB task: `langchain-version-migration`
- Docker images: 46/46 available
- Python environment: `uv`
- Hypha submodule: `benchmarks/Hypha`
- Pi submodule: `benchmarks/pi-agent`
- Pi runtime: local Node `22.19.0` binary under `benchmarks/pi-agent/node_modules/node/bin/node`

## Result

Result file:

```text
/root/projects/Hypha-exp/outputs/lhtb/hypha-exp-lhtb-formal-compare/result.json
```

Harbor summary:

| Agent | Trials | Exceptions | Mean reward |
| --- | ---: | ---: | ---: |
| Hypha | 1 | 0 | 0.000 |
| Pi | 1 | 0 | 0.000 |

The zero reward is expected for this smoke run because neither adapter attempted
the actual LangChain migration task. The important milestone is that both
systems now run through the same LHTB job and produce comparable verifier
metrics.

## What This Proves

- LHTB Docker image reuse works after restoring the missing three images.
- Hypha can be called through its built kernel package from a Harbor agent.
- Pi can be called through its built agent package from a Harbor agent.
- Both adapters produce the same action schema and use the same benchmark
config, verifier, timeout, output directory, and metric extraction path.

## Next Step

Replace deterministic `planned_actions` with a real model-backed policy for each
kernel, then run the same config across a small task set before expanding to
larger LHTB coverage.
