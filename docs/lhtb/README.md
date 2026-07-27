# LHTB Experiment Notes

This repository pins LHTB as a Git submodule and reuses the host Docker daemon's
existing LHTB images. Do not pull or copy the 46 task images unless
`scripts/verify-lhtb-images.py` reports missing images.

Use `uv`, not conda:

```bash
cd /root/projects/Hypha-exp
./scripts/bootstrap-lhtb.sh
./scripts/run-lhtb-oracle-smoke.sh
```

Outputs are written under `/root/projects/Hypha-exp/outputs/lhtb`.
