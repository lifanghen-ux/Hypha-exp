from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
    score_response,
    validate_dataset,
    write_item_artifact,
    write_summary,
)
from .spp_runner import _git_dirty, _git_revision, _select_indices, _sum_usage

PROMPT_REVISION = "direct-spp-neutral-v1"
SYSTEM_NAME = "direct-deepseek"
PROVIDER_NAME = "openai-compatible-direct"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _append_jsonl(path: Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def _token_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    usage = usage or {}
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    completion_details = (
        usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    )
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    return {
        "modelCalls": 1,
        "inputTokens": input_tokens,
        "cachedInputTokens": int(prompt_details.get("cached_tokens") or usage.get("cached_input_tokens") or 0),
        "outputTokens": output_tokens,
        "thinkingTokens": int(completion_details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0),
        "totalTokens": int(usage.get("total_tokens") or input_tokens + output_tokens),
    }


def _load_dotenv(workspace: Path) -> None:
    env_path = workspace / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        key, value = trimmed.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


class DirectDeepSeekClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        temperature: float,
        max_tokens: int,
        timeout_sec: int,
        retries: int,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec
        self.retries = retries

    def infer(self, task: str, *, run_id: str, role: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Solve the benchmark task faithfully. Follow its output format exactly.",
                },
                {"role": "user", "content": task},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, self.retries + 2):
            started = time.monotonic()
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=payload,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                    response_text = response.read().decode("utf-8", errors="replace")
                    status_code = response.status
                data = json.loads(response_text)
                message = ((data.get("choices") or [{}])[0].get("message") or {})
                content = message.get("content") if isinstance(message.get("content"), str) else ""
                finish_reason = (data.get("choices") or [{}])[0].get("finish_reason")
                usage = _token_usage(data.get("usage"))
                attempts.append(
                    {
                        "requestId": data.get("id"),
                        "model": data.get("model") or self.model,
                        "finishReason": finish_reason,
                        "elapsedMs": round((time.monotonic() - started) * 1000),
                        "attempt": attempt,
                        "status": "completed" if content else "missing_content",
                        "usage": usage,
                    }
                )
                return {
                    "status": "completed" if content else "failed",
                    "runId": run_id,
                    "role": role,
                    "output": content.strip() if content else "",
                    "error": None if content else f"Model response did not contain message.content (finish_reason={finish_reason})",
                    "model": data.get("model") or self.model,
                    "provider": PROVIDER_NAME,
                    "endpoint": self.base_url,
                    "usage": usage,
                    "calls": attempts,
                }
            except urllib.error.HTTPError as exc:
                response_text = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(
                    f"Model endpoint returned HTTP {exc.code}: {response_text[-1000:]}"
                )
                retryable = exc.code == 429 or exc.code >= 500
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                last_error = RuntimeError(f"Model request failed: {exc}")
                retryable = True
            except json.JSONDecodeError as exc:
                last_error = RuntimeError(f"Model endpoint returned invalid JSON: {exc}")
                retryable = False
            if not retryable or attempt > self.retries:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
        return {
            "status": "failed",
            "runId": run_id,
            "role": role,
            "output": "",
            "error": str(last_error) if last_error else "unknown model request failure",
            "model": self.model,
            "provider": PROVIDER_NAME,
            "endpoint": self.base_url,
            "usage": {key: 0 for key in ("modelCalls", "inputTokens", "cachedInputTokens", "outputTokens", "thinkingTokens", "totalTokens")},
            "calls": attempts,
        }


def _zero_score(benchmark_id: str, instance: dict[str, Any]) -> dict[str, Any]:
    return score_response(benchmark_id, instance, None)


def _run_item(
    *,
    benchmark_id: str,
    instance: dict[str, Any],
    index: int,
    args: argparse.Namespace,
    client: DirectDeepSeekClient,
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
    warnings: list[dict[str, Any]] = []
    parsed: str | list[str] | None = None
    status = "ok"
    error: str | None = None

    def call(task: str, role: str) -> dict[str, Any]:
        result = client.infer(task, run_id=f"{run_id}:{index}:{role}", role=role)
        calls.append(result)
        raw["calls"].append({"role": role, "task": task, "result": result})
        if result.get("status") != "completed":
            detail = result.get("error") or "no error detail"
            raise RuntimeError(f"Direct DeepSeek SPP run did not complete: {detail}")
        model_attempts = result.get("calls", [])
        final_finish_reason = model_attempts[-1].get("finishReason") if model_attempts else None
        if final_finish_reason == "length":
            output = str(result.get("output") or "")
            if not output:
                raise RuntimeError(
                    "Direct DeepSeek model hit the max token limit without returning message.content "
                    f"(max_tokens={args.max_tokens})"
                )
            warning = {
                "type": "length_truncated_with_content",
                "role": role,
                "maxTokens": args.max_tokens,
                "message": "Model finish_reason=length but returned content; scoring available truncated content.",
            }
            warnings.append(warning)
            result.setdefault("warnings", []).append(warning)
            raw.setdefault("warnings", []).append(warning)
        return result

    try:
        if benchmark_spec(benchmark_id).kind == "codenames":
            spymaster_task = render_task(benchmark_id, instance, role="spymaster")
            spymaster = call(spymaster_task, "spymaster")
            hint = parse_response(benchmark_id, str(spymaster.get("output") or ""), role="spymaster")
            raw["hint"] = hint
            if not isinstance(hint, str):
                status = "parse_error"
            else:
                guesser_task = render_task(benchmark_id, instance, role="guesser", hint=hint)
                guesser = call(guesser_task, "guesser")
                parsed = parse_response(benchmark_id, str(guesser.get("output") or ""), role="guesser")
                if parsed is None:
                    status = "parse_error"
        else:
            task = render_task(benchmark_id, instance)
            solver = call(task, "solver")
            parsed = parse_response(benchmark_id, str(solver.get("output") or ""))
            if parsed is None:
                status = "parse_error"
    except RuntimeError as exc:
        status = "agent_error"
        error = str(exc)
        raw["error"] = error

    official = score_response(benchmark_id, instance, parsed) if parsed is not None else _zero_score(benchmark_id, instance)
    raw_ref = write_item_artifact(output_dir, index, raw)
    return {
        "benchmarkId": benchmark_id,
        "index": index,
        "status": status,
        "official": official,
        "rawArtifact": raw_ref,
        "modelUsage": _sum_usage(calls),
        "wallTimeSec": round(time.monotonic() - started, 3),
        **({"warnings": warnings} if warnings else {}),
        **({"error": error} if error else {}),
    }


def _summary(benchmark_id: str, items: list[dict[str, Any]], started_at: str, completed_at: str) -> dict[str, Any]:
    earned = sum(float(item["official"]["earned"]) for item in items)
    possible = sum(float(item["official"]["possible"]) for item in items)
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
        "warningCounts": {
            "length_truncated_with_content": sum(
                any(warning.get("type") == "length_truncated_with_content" for warning in item.get("warnings", []))
                for item in items
            )
        },
        "official": {"earned": earned, "possible": possible, "score": earned / possible if possible else 0.0},
        "modelUsage": _sum_usage([{"usage": item["modelUsage"]} for item in items]),
        "wallTimeSec": round(sum(item["wallTimeSec"] for item in items), 3),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run direct DeepSeek on a pinned SPP dataset.")
    parser.add_argument("benchmark_id", choices=tuple(BENCHMARKS))
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--indices", help="Comma-separated official row indices")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--model", default=os.environ.get("HYPHA_MODEL"))
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("HYPHA_SPP_MAX_TOKENS", "8192")))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--run-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.workspace = args.workspace.resolve()
    _load_dotenv(args.workspace)
    if not args.model:
        args.model = os.environ.get("HYPHA_MODEL")
    if not args.model:
        raise SystemExit("Set HYPHA_MODEL or pass --model")
    if args.start < 0 or args.limit < 1:
        raise SystemExit("--start must be >= 0 and --limit must be >= 1")
    api_key = os.environ.get("HYPHA_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("HYPHA_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY is required")
    base_url = os.environ.get("HYPHA_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com"

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
    client = DirectDeepSeekClient(
        model=args.model,
        api_key=api_key,
        base_url=base_url,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_sec=args.timeout,
        retries=args.retries,
    )

    run_metadata: dict[str, Any] = {
        "runId": run_id,
        "status": "running",
        "system": SYSTEM_NAME,
        "benchmarkId": args.benchmark_id,
        "startedAt": started_at,
        "hyphaCommit": _git_revision(args.workspace),
        "hyphaGitDirty": _git_dirty(args.workspace),
        "sourceFiles": {
            "src/hypha_exp/benchmarks/spp.py": __import__("hashlib").sha256((args.workspace / "src/hypha_exp/benchmarks/spp.py").read_bytes()).hexdigest(),
            "src/hypha_exp/benchmarks/spp_direct_deepseek_runner.py": __import__("hashlib").sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "sppCommit": _git_revision(spp_root),
        "dataset": dataset,
        "model": args.model,
        "provider": PROVIDER_NAME,
        "endpoint": base_url.rstrip("/"),
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
    run_path.write_text(json.dumps(run_metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    items: list[dict[str, Any]] = []
    try:
        for index in indices:
            item = _run_item(
                benchmark_id=args.benchmark_id,
                instance=instances[index],
                index=index,
                args=args,
                client=client,
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
        run_path.write_text(json.dumps(run_metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        raise

    completed_at = _now()
    summary = _summary(args.benchmark_id, items, started_at, completed_at)
    write_summary(args.output, summary)
    run_metadata["status"] = "completed" if all(item["status"] == "ok" for item in items) else "completed_with_errors"
    run_metadata["completedAt"] = completed_at
    run_metadata["completedItems"] = len(items)
    run_path.write_text(json.dumps(run_metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if run_metadata["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
