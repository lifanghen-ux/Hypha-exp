from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BenchmarkSpec:
    benchmark_id: str
    relative_path: str
    expected_sha256: str
    expected_count: int
    kind: str


BENCHMARKS: dict[str, BenchmarkSpec] = {
    "spp.logic-grid-puzzle": BenchmarkSpec(
        benchmark_id="spp.logic-grid-puzzle",
        relative_path="logic_grid_puzzle/logic_grid_puzzle_200.jsonl",
        expected_sha256="5e796a1e6b983ee44bdddee122cfcbcfe454a2e96eb59d031abbe890cefeff37",
        expected_count=200,
        kind="logic",
    ),
    "spp.trivia-creative-writing-n5": BenchmarkSpec(
        benchmark_id="spp.trivia-creative-writing-n5",
        relative_path="trivia_creative_writing/trivia_creative_writing_100_n_5.jsonl",
        expected_sha256="0b87d6f9d4e7fee3d4d5ce8fa774e2255d486346692f7e8a451d544f233e3c1c",
        expected_count=100,
        kind="trivia",
    ),
    "spp.trivia-creative-writing-n10": BenchmarkSpec(
        benchmark_id="spp.trivia-creative-writing-n10",
        relative_path="trivia_creative_writing/trivia_creative_writing_100_n_10.jsonl",
        expected_sha256="052ede3dfff067bbce8ac0314764555e781f7d1993f0e38626a511d58890a3f4",
        expected_count=100,
        kind="trivia",
    ),
    "spp.codenames-collaborative": BenchmarkSpec(
        benchmark_id="spp.codenames-collaborative",
        relative_path="codenames_collaborative/codenames_50.jsonl",
        expected_sha256="2445d991787015be00330376447e5b0aaa6906908acb6d47869bc8fe90a8f4be",
        expected_count=50,
        kind="codenames",
    ),
}


def benchmark_spec(benchmark_id: str) -> BenchmarkSpec:
    try:
        return BENCHMARKS[benchmark_id]
    except KeyError as exc:
        choices = ", ".join(BENCHMARKS)
        raise ValueError(f"Unknown benchmark {benchmark_id!r}; choose from {choices}") from exc


def resolve_data_root(data_root: str | Path | None = None) -> Path:
    configured = data_root or os.environ.get("HYPHA_SPP_DATA_ROOT")
    if configured is None:
        spp_root = os.environ.get("HYPHA_SPP_ROOT")
        configured = str(Path(spp_root) / "data") if spp_root else None
    if configured is None:
        raise RuntimeError(
            "SPP data root is not configured. Set HYPHA_SPP_DATA_ROOT to the "
            "read-only SPP checkout's data directory."
        )
    root = Path(configured).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"SPP data root does not exist: {root}")
    return root


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_dataset(
    benchmark_id: str,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    spec = benchmark_spec(benchmark_id)
    root = resolve_data_root(data_root)
    path = root / spec.relative_path
    if not path.is_file():
        raise FileNotFoundError(f"SPP dataset is missing: {path}")
    digest = _sha256(path)
    if digest != spec.expected_sha256:
        raise RuntimeError(
            f"SPP dataset checksum mismatch for {benchmark_id}: "
            f"expected {spec.expected_sha256}, got {digest}"
        )
    with path.open("r", encoding="utf-8") as handle:
        count = sum(1 for line in handle if line.strip())
    if count != spec.expected_count:
        raise RuntimeError(
            f"SPP dataset row count mismatch for {benchmark_id}: "
            f"expected {spec.expected_count}, got {count}"
        )
    return {
        "benchmarkId": benchmark_id,
        "path": str(path),
        "sha256": digest,
        "count": count,
    }


def load_instances(
    benchmark_id: str,
    data_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    spec = benchmark_spec(benchmark_id)
    path = resolve_data_root(data_root) / spec.relative_path
    instances: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number} must contain a JSON object")
            instances.append(value)
    return instances


def render_task(
    benchmark_id: str,
    instance: dict[str, Any],
    role: str | None = None,
    hint: str | None = None,
) -> str:
    kind = benchmark_spec(benchmark_id).kind
    if kind == "logic":
        question = str(instance["inputs"]).strip()
        question = re.sub(r"\nA:\s*$", "", question)
        return (
            f"{question}\n\n"
            "Return exactly one house number. Do not include reasoning, labels, "
            "or any other text."
        )

    if kind == "trivia":
        questions = "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(instance["questions"], start=1)
        )
        return (
            f"Write a coherent creative story about {instance['topic']} that "
            "naturally includes the correct answer to every trivia question below.\n\n"
            f"{questions}\n\n"
            "Return only the story. Do not list the questions or explain your answers."
        )

    if role == "spymaster":
        targets = ", ".join(str(word) for word in instance["target_words"])
        board = ", ".join(str(word) for word in instance["word_list"])
        return (
            "You are the Spymaster in Codenames. Choose one single-word hint that "
            f"connects all {len(instance['target_words'])} target words while avoiding "
            "confusion with other board words.\n\n"
            f"Target words: {targets}\n"
            f"Complete board: {board}\n\n"
            "Return exactly the one-word hint, with no label or explanation."
        )
    if role == "guesser":
        if not hint:
            raise ValueError("Codenames guesser requires a non-empty hint")
        board = ", ".join(str(word) for word in instance["word_list"])
        return (
            "You are the Guesser in Codenames. The Spymaster supplied this hint:\n"
            f"{hint}\n\n"
            f"Choose exactly {len(instance['target_words'])} words from this board:\n"
            f"{board}\n\n"
            "Return only a comma-separated list of board words."
        )
    raise ValueError("Codenames rendering requires role='spymaster' or role='guesser'")


def _after_answer_label(response: str) -> str:
    matches = list(re.finditer(r"(?i)\b(?:final\s+answer|answer)\s*:\s*", response))
    value = response[matches[-1].end() :] if matches else response
    return value.strip().strip("`").strip()


def parse_response(
    benchmark_id: str,
    response: str,
    role: str | None = None,
) -> str | list[str] | None:
    kind = benchmark_spec(benchmark_id).kind
    value = _after_answer_label(response)
    if kind == "logic":
        match = re.fullmatch(r"(\d{1,2})[.!]?", value)
        return match.group(1) if match else None
    if kind == "trivia":
        return value or None
    if role == "spymaster":
        first_line = value.splitlines()[0].strip().strip("\"'").rstrip(".")
        if not first_line or re.search(r"\s", first_line):
            return None
        return first_line
    if role == "guesser":
        words = []
        for part in re.split(r"[,;\n]", value):
            word = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", part)
            word = word.strip().strip("\"'").rstrip(".").strip()
            if word:
                words.append(word)
        return words or None
    raise ValueError("Codenames parsing requires a role")


def score_response(
    benchmark_id: str,
    instance: dict[str, Any],
    parsed: str | list[str] | None,
) -> dict[str, Any]:
    kind = benchmark_spec(benchmark_id).kind
    if kind == "logic":
        target = str(instance["targets"][0]).strip()
        earned = int(isinstance(parsed, str) and parsed == target)
        return {
            "metric": "exact-house-number",
            "earned": earned,
            "possible": 1,
            "score": float(earned),
            "exactMatch": bool(earned),
            "parsed": parsed,
        }

    if kind == "trivia":
        story = parsed if isinstance(parsed, str) else ""
        folded_story = story.casefold()
        matched = [
            index
            for index, aliases in enumerate(instance["answers"])
            if any(str(alias).casefold() in folded_story for alias in aliases)
        ]
        possible = len(instance["answers"])
        earned = len(matched)
        return {
            "metric": "answer-alias-substring",
            "earned": earned,
            "possible": possible,
            "score": earned / possible if possible else 0.0,
            "exactMatch": earned == possible,
            "parsed": parsed,
            "matchedQuestionIndices": matched,
        }

    predicted = parsed if isinstance(parsed, list) else []
    board_by_folded = {
        str(word).strip().casefold(): str(word).strip()
        for word in instance["word_list"]
    }
    normalized = {
        board_by_folded[word.strip().casefold()]
        for word in predicted
        if word.strip().casefold() in board_by_folded
    }
    targets = {str(word).strip() for word in instance["target_words"]}
    matched = sorted(normalized.intersection(targets))
    earned = len(matched)
    possible = len(targets)
    return {
        "metric": "target-word-recall",
        "earned": earned,
        "possible": possible,
        "score": earned / possible if possible else 0.0,
        "exactMatch": earned == possible,
        "parsed": parsed,
        "matchedWords": matched,
    }


def run_hypha_agent(
    task: str,
    model: str,
    workspace: str | Path,
    *,
    helper_path: str | Path | None = None,
    node_path: str = "node",
    timeout_sec: int = 180,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    retries: int = 2,
    run_id: str,
    session_id: str,
) -> dict[str, Any]:
    workspace_path = Path(workspace).resolve()
    helper = (
        Path(helper_path).resolve()
        if helper_path
        else workspace_path / "scripts" / "hypha-spp-agent.mjs"
    )
    payload = {
        "task": task,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "retries": retries,
        "run_id": run_id,
        "session_id": session_id,
    }
    completed = subprocess.run(
        [node_path, str(helper)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=workspace_path,
        timeout=timeout_sec,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-3000:]
        raise RuntimeError(
            f"Hypha SPP helper exited with {completed.returncode}: {detail}"
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Hypha SPP helper returned invalid JSON: {completed.stdout[-1000:]}"
        ) from exc
    return result


def write_item_artifact(
    output_dir: str | Path,
    index: int,
    payload: dict[str, Any],
) -> str:
    raw_dir = Path(output_dir) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{index:04d}.json"
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path.relative_to(Path(output_dir)).as_posix()


def write_summary(output_dir: str | Path, summary: dict[str, Any]) -> Path:
    path = Path(output_dir) / "summary.json"
    path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
