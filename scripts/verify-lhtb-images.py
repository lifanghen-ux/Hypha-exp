from __future__ import annotations

from pathlib import Path
import json
import subprocess
import tomllib

TASKS_ROOT = Path("benchmarks/LHTB/tasks")

def main() -> int:
    tasks = sorted(path for path in TASKS_ROOT.iterdir() if path.is_dir())
    missing: list[tuple[str, str]] = []
    wrong_platform: list[tuple[str, str, str | None, str | None]] = []

    for task in tasks:
        with (task / "task.toml").open("rb") as handle:
            image = tomllib.load(handle)["environment"]["docker_image"]

        result = subprocess.run(
            ["docker", "image", "inspect", image],
            text=True,
            capture_output=True,
        )
        if result.returncode:
            missing.append((task.name, image))
            continue

        info = json.loads(result.stdout)[0]
        operating_system = info.get("Os")
        architecture = info.get("Architecture")
        if (operating_system, architecture) != ("linux", "amd64"):
            wrong_platform.append((task.name, image, operating_system, architecture))

    summary = {
        "tasks": len(tasks),
        "available": len(tasks) - len(missing),
        "missing": len(missing),
        "wrong_platform": len(wrong_platform),
    }
    print(json.dumps(summary, indent=2))
    for task, image in missing:
        print(f"MISSING {task}: {image}")
    for task, image, operating_system, architecture in wrong_platform:
        print(f"WRONG_PLATFORM {task}: {image} {operating_system}/{architecture}")
    return 1 if missing or wrong_platform else 0

if __name__ == "__main__":
    raise SystemExit(main())
