# LHTB Hypha Adapter Smoke Result - 2026-07-27

This run verifies that Harbor can load and run the custom Hypha LHTB adapter from
`hypha_exp.benchmarks.lhtb:HyphaLHTBAgent` inside the official LHTB benchmark
path.

## Command

```bash
cd /root/projects/Hypha-exp
./scripts/run-lhtb-hypha-smoke.sh
```

## Result

- Job name: `hypha-exp-lhtb-hypha-smoke`
- Result path: `/root/projects/Hypha-exp/outputs/lhtb/hypha-exp-lhtb-hypha-smoke/result.json`
- Total trials: `1`
- Completed trials: `1`
- Errors: `0`
- Mean reward: `0.0`
- Reward distribution: `0.0 x 1`

## Interpretation

This is a custom-agent integration smoke, not a real Hypha capability score. The
adapter currently runs a deterministic shell plan and does not solve the task.
The important result is that Harbor imports the Hypha adapter, starts the Docker
task environment, invokes the adapter repeatedly under LHTB's
`continue_until_timeout` behavior, runs the verifier, and writes a formal
`result.json`.
