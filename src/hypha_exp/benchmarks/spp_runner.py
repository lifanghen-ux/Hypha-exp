from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .spp import (
    BENCHMARKS,
    benchmark_spec,
    load_instances,
    parse_response,
    render_task,
    resolve_data_root,
    run_hypha_agent,
    score_response,
    validate_dataset,
    write_item_artifact,
    write_summary,
)

PROMPT_REVISION = "hypha-spp-neutral-v1"
SYSTEM_NAME = "hypha"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git_revision(path: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _git_dirty(path: Path) -> bool | None:
    completed = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=False,
    )
    return bool(completed.stdout.strip()) if completed.returncode == 0 else None


def _source_hashes(workspace: Path) -> dict[str, str]:
    relative_paths = (
        "src/hypha_exp/benchmarks/spp.py",
        "src/hypha_exp/benchmarks/spp_runner.py",
        "scripts/hypha-spp-agent.mjs",
    )
    return {
        relative_path: hashlib.sha256(
            (workspace / relative_path).read_bytes()
        ).hexdigest()
        for relative_path in relative_paths
    }


def _usage(call: dict[str, Any]) -> dict[str, int]:
    value = call.get("usage") or {}
    return {
        "modelCalls": int(value.get("modelCalls") or 0),
        "inputTokens": int(value.get("inputTokens") or 0),
        "cachedInputTokens": int(value.get("cachedInputTokens") or 0),
        "outputTokens": int(value.get("outputTokens") or 0),
        "thinkingTokens": int(value.get("thinkingTokens") or 0),
        "totalTokens": int(value.get("totalTokens") or 0),
    }


def _sum_usage(calls: list[dict[str, Any]]) -> dict[str, int]:
    result = {
        "modelCalls": 0,
        "inputTokens": 0,
        "cachedInputTokens": 0,
        "outputTokens": 0,
        "thinkingTokens": 0,
        "totalTokens": 0,
    }
    for call in calls:
        for key, value in _usage(call).items():
            result[key] += value
    return result


def _zero_score(benchmark_id: str, instance: dict[str, Any]) -> dict[str, Any]:
    return score_response(benchmark_id, instance, None)


def _select_indices(
    total: int,
    start: int,
    limit: int,
    explicit: str | None,
) -> list[int]:
    if explicit:
        indices = [int(value.strip()) for value in explicit.split(",") if value.strip()]
    else:
        indices = list(range(start, min(total, start + limit)))
    if not indices:
        raise ValueError("No SPP indices were selected")
    invalid = [index for index in indices if index < 0 or index >= total]
    if invalid:
        raise IndexError(f"SPP indices out of range for {total} rows: {invalid}")
    if len(indices) != len(set(indices)):
        raise ValueError("SPP indices must be unique")
    return indices


def _append_jsonl(path: Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def _run_item(
    *,
    benchmark_id: str,
    instance: dict[str, Any],
    index: int,
    args: argparse.Namespace,
    run_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    raw: dict[str, Any] = {
        "benchmarkId": benchmark_id,
        "index": index,
        "system": SYSTEM_NAME,
        "model": args.model,
        "calls": [],
    }
    calls: list[dict[str, Any]] = []
    parsed: str | list[str] | None = None
    status = "ok"
    error: str | None = None

    def call(task: str, role: str) -> dict[str, Any]:
        result = run_hypha_agent(
            task,
            args.model,
            args.workspace,
            helper_path=args.helper,
            node_path=args.node,
            timeout_sec=args.timeout,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            retries=args.retries,
            run_id=f"{run_id}:{index}:{role}",
            session_id=f"{run_id}:{index}:{role}",
        )
        calls.append(result)
        raw["calls"].append({"role": role, "task": task, "result": result})
        if result.get("status") != "completed":
            detail = result.get("error") or "no error detail"
            raise RuntimeError(
                f"Hypha SPP run did not complete: {result.get('status')}: {detail}"
            )
        model_attempts = result.get("calls", [])
        final_finish_reason = (
            model_attempts[-1].get("finishReason") if model_attempts else None
        )
        if final_finish_reason == "length":
            raise RuntimeError(
                f"Hypha model output was truncated at {args.max_tokens} tokens"
            )
        return result

    try:
        if benchmark_spec(benchmark_id).kind == "codenames":
            spymaster_task = render_task(benchmark_id, instance, role="spymaster")
            spymaster = call(spymaster_task, "spymaster")
            hint = parse_response(
                benchmark_id,
                str(spymaster.get("output") or ""),
                role="spymaster",
            )
            raw["hint"] = hint
            if not isinstance(hint, str):
                status = "parse_error"
            else:
                guesser_task = render_task(
                    benchmark_id,
                    instance,
                    role="guesser",
                    hint=hint,
                )
                guesser = call(guesser_task, "guesser")
                parsed = parse_response(
                    benchmark_id,
                    str(guesser.get("output") or ""),
                    role="guesser",
                )
                if parsed is None:
                    status = "parse_error"
        else:
            task = render_task(benchmark_id, instance)
            solver = call(task, "solver")
            parsed = parse_response(benchmark_id, str(solver.get("output") or ""))
            if parsed is None:
                status = "parse_error"
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        status = "agent_error"
        error = str(exc)
        raw["error"] = error

    official = (
        score_response(benchmark_id, instance, parsed)
        if parsed is not None
        else _zero_score(benchmark_id, instance)
    )
    raw_ref = write_item_artifact(output_dir, index, raw)
    return {
        "benchmarkId": benchmark_id,
        "index": index,
        "status": status,
        "official": official,
        "rawArtifact": raw_ref,
        "modelUsage": _sum_usage(calls),
        "wallTimeSec": round(time.monotonic() - started, 3),
        **({"error": error} if error else {}),
    }


def _summary(
    benchmark_id: str,
    items: list[dict[str, Any]],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    earned = sum(float(item["official"]["earned"]) for item in items)
    possible = sum(float(item["official"]["possible"]) for item in items)
    usage = _sum_usage(
        [{"usage": item["modelUsage"]} for item in items]
    )
    return {
        "benchmarkId": benchmark_id,
        "system": SYSTEM_NAME,
        "startedAt": started_at,
        "completedAt": completed_at,
        "items": len(items),
        "statusCounts": {
            status: sum(item["status"] == status for item in items)
            for status in ("ok", "parse_error", "agent_error")
        },
        "official": {
            "earned": earned,
            "possible": possible,
            "score": earned / possible if possible else 0.0,
        },
        "modelUsage": usage,
        "wallTimeSec": round(sum(item["wallTimeSec"] for item in items), 3),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Hypha independently on a pinned SPP dataset."
    )
    parser.add_argument("benchmark_id", choices=tuple(BENCHMARKS))
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--indices", help="Comma-separated official row indices")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--model", default=os.environ.get("HYPHA_MODEL"))
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[3],
    )
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--node", default=os.environ.get("HYPHA_NODE", "node"))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.environ.get("HYPHA_SPP_MAX_TOKENS", "8192")),
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--run-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.model:
        raise SystemExit("Set HYPHA_MODEL or pass --model")
    if args.start < 0 or args.limit < 1:
        raise SystemExit("--start must be >= 0 and --limit must be >= 1")

    args.workspace = args.workspace.resolve()
    args.output = args.output.resolve()
    if (args.output / "run.json").exists() or (args.output / "items.jsonl").exists():
        raise SystemExit(f"Refusing to overwrite an existing SPP run: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    data_root = resolve_data_root(args.data_root)
    dataset = validate_dataset(args.benchmark_id, data_root)
    instances = load_instances(args.benchmark_id, data_root)
    indices = _select_indices(len(instances), args.start, args.limit, args.indices)
    run_id = args.run_id or args.output.parent.name
    started_at = _now()
    run_path = args.output / "run.json"
    items_path = args.output / "items.jsonl"
    spp_root = data_root.parent

    run_metadata: dict[str, Any] = {
        "runId": run_id,
        "status": "running",
        "system": SYSTEM_NAME,
        "benchmarkId": args.benchmark_id,
        "startedAt": started_at,
        "hyphaCommit": _git_revision(args.workspace),
        "hyphaGitDirty": _git_dirty(args.workspace),
        "sourceFiles": _source_hashes(args.workspace),
        "sppCommit": _git_revision(spp_root),
        "dataset": dataset,
        "model": args.model,
        "provider": "openai-compatible",
        "config": {
            "promptRevision": PROMPT_REVISION,
            "temperature": args.temperature,
            "maxTokens": args.max_tokens,
            "timeoutSec": args.timeout,
            "retries": args.retries,
        },
        "selectedIndices": indices,
        "environment": {
            "hostname": platform.node(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "workspace": str(args.workspace),
            "sppDataRoot": str(data_root),
        },
    }
    run_path.write_text(
        json.dumps(run_metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    items: list[dict[str, Any]] = []
    try:
        for index in indices:
            item = _run_item(
                benchmark_id=args.benchmark_id,
                instance=instances[index],
                index=index,
                args=args,
                run_id=run_id,
                output_dir=args.output,
            )
            items.append(item)
            _append_jsonl(items_path, item)
            print(
                f"[{args.benchmark_id}] index={index} status={item['status']} "
                f"score={item['official']['earned']}/{item['official']['possible']}",
                flush=True,
            )
    except BaseException:
        run_metadata["status"] = "failed"
        run_metadata["completedAt"] = _now()
        run_metadata["completedItems"] = len(items)
        run_path.write_text(
            json.dumps(run_metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        raise

    completed_at = _now()
    summary = _summary(args.benchmark_id, items, started_at, completed_at)
    write_summary(args.output, summary)
    run_metadata["status"] = (
        "completed"
        if all(item["status"] == "ok" for item in items)
        else "completed_with_errors"
    )
    run_metadata["completedAt"] = completed_at
    run_metadata["completedItems"] = len(items)
    run_path.write_text(
        json.dumps(run_metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if run_metadata["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
