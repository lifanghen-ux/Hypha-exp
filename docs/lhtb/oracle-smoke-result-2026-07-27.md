# LHTB Oracle Smoke Result - 2026-07-27

This run verifies that the pinned LHTB checkout, the shared Docker image cache,
Harbor, and the hidden verifiers work on the remote server before running Hypha.

## Command

```bash
cd /root/projects/Hypha-exp
./scripts/run-lhtb-oracle-smoke.sh
```

## Result

- Job name: `hypha-exp-lhtb-oracle-smoke`
- Result path: `/root/projects/Hypha-exp/outputs/lhtb/hypha-exp-lhtb-oracle-smoke/result.json`
- Total trials: `3`
- Completed trials: `3`
- Errors: `0`
- Mean reward: `1.0`
- Reward distribution: `1.0 x 3`

## Tasks

- `langchain-version-migration`
- `document-table-layout-reconstruction`
- `great-expectations-audit`

## Interpretation

This is not a Hypha score. It is an infrastructure check using LHTB's oracle
agent. It confirms that the official benchmark path can run and grade tasks on
this server without re-pulling the 46 Docker images.
