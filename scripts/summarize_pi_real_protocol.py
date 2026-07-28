from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace").strip()


def iter_history_records(agent_dir: Path):
    jsonl_path = agent_dir / "history_full.jsonl"
    if jsonl_path.exists():
        with jsonl_path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
        return
    for item in read_json(agent_dir / "history.json", []):
        yield item


def summarize_trial(trial_dir: Path) -> dict[str, Any]:
    agent_dir = trial_dir / "agent"
    trajectory = read_json(agent_dir / "trajectory.json", {})
    history_count = 0
    exceptions = []
    tool_requests = []
    dropped_tool_calls = 0
    budget_error_messages = 0
    orphan_calls = []
    orphan_results = []
    max_tool_calls_in_phase = 0
    for item in iter_history_records(agent_dir):
        history_count += 1
        if item.get("adapter_error") is not None:
            exceptions.append(item.get("adapter_error"))
        phase_requests = item.get("tool_requests", [])
        tool_requests.extend(phase_requests)
        max_tool_calls_in_phase = max(max_tool_calls_in_phase, len(phase_requests))
        pi_result = item.get("pi_result", {})
        dropped_tool_calls += int(pi_result.get("droppedToolCalls", 0) or 0)
        budget_error_messages += json.dumps(pi_result.get("messages", [])).count(
            "Tool budget for this phase is exhausted"
        )
        selected_integrity = (
            pi_result.get("context", {}).get("selected_integrity", {})
            if isinstance(pi_result, dict)
            else {}
        )
        orphan_calls.extend(selected_integrity.get("orphanToolCallIds", []) or [])
        orphan_results.extend(selected_integrity.get("orphanToolResultIds", []) or [])
    config = read_json(trial_dir / "config.json", {})
    metrics = trajectory.get("metrics", {})
    task_path = config.get("task", {}).get("path")
    exception_text = read_text(trial_dir / "exception.txt")
    return {
        "trial": trial_dir.name,
        "task_name": Path(task_path).name if task_path else trial_dir.name.rsplit("__", 1)[0],
        "reward": read_text(trial_dir / "verifier" / "reward.txt"),
        "harbor_exception": exception_text.splitlines()[-1] if exception_text else None,
        "adapter_errors": exceptions,
        "phases": history_count,
        "elapsed_sec": metrics.get("cumulative_elapsed_sec"),
        "usage": metrics.get("cumulative_usage", {}),
        "tool_calls": metrics.get("cumulative_tool_calls", len(tool_requests)),
        "dropped_tool_calls": dropped_tool_calls,
        "budget_error_messages": budget_error_messages,
        "max_tool_calls_in_phase": max_tool_calls_in_phase,
        "orphan_tool_call_ids": orphan_calls,
        "orphan_tool_result_ids": orphan_results,
        "effective_shell_timeouts_sec": sorted(
            {
                request.get("result", {}).get("effective_timeout_sec")
                for request in tool_requests
                if request.get("result", {}).get("effective_timeout_sec") is not None
            }
        ),
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Pi Real LHTB Protocol Review",
        "",
        f"- Job: `{summary['job_name']}`",
        f"- Completed trials: {summary['job_stats'].get('n_completed_trials')}",
        f"- Errored trials: {summary['job_stats'].get('n_errored_trials')}",
        "",
        "| Task | Reward | Harbor exception | Adapter errors | Phases | Tools | Input tokens | Output tokens | Elapsed (s) | Orphans |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for trial in summary["trials"]:
        usage = trial["usage"]
        lines.append(
            "| {task} | {reward} | {harbor} | {adapter} | {phases} | {tools} | {input} | {output} | {elapsed} | {orphans} |".format(
                task=trial["task_name"] or trial["trial"],
                reward=trial["reward"],
                harbor=trial["harbor_exception"] or "",
                adapter=len(trial["adapter_errors"]),
                phases=trial["phases"],
                tools=trial["tool_calls"],
                input=usage.get("input_tokens", 0),
                output=usage.get("output_tokens", 0),
                elapsed=trial["elapsed_sec"],
                orphans=len(trial["orphan_tool_call_ids"]) + len(trial["orphan_tool_result_ids"]),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args()
    job_dir = args.job_dir.resolve()
    result = read_json(job_dir / "result.json", {})
    trial_dirs = sorted(
        path
        for path in job_dir.iterdir()
        if path.is_dir() and (path / "config.json").exists()
    )
    summary = {
        "job_name": job_dir.name,
        "job_stats": result.get("stats", {}),
        "trials": [summarize_trial(path) for path in trial_dirs],
    }
    json_path = job_dir / "protocol-review.json"
    markdown_path = job_dir / "protocol-review.md"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(summary), encoding="utf-8")
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
