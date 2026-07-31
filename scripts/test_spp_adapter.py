#!/usr/bin/env python3
from __future__ import annotations

import os

from hypha_exp.benchmarks.spp import (
    parse_response,
    render_task,
    score_response,
    validate_dataset,
)

LOGIC = {
    "inputs": "Q: Which house?\n  choice: 1\n  choice: 2\nA:",
    "targets": ["2"],
}
TRIVIA = {
    "topic": "Space",
    "questions": ["Who first walked on the Moon?", "What is the red planet?"],
    "answers": [["Neil Armstrong", "Armstrong"], ["Mars"]],
}
CODENAMES = {
    "word_list": ["moon", "boot", "mars", "river", "king arthur"],
    "target_words": ["moon", "boot"],
}


def main() -> None:
    logic_id = "spp.logic-grid-puzzle"
    logic_task = render_task(logic_id, LOGIC)
    assert "Return exactly one house number" in logic_task
    assert parse_response(logic_id, "Answer: 2") == "2"
    assert parse_response(logic_id, "The answer is 2") is None
    assert score_response(logic_id, LOGIC, "2")["score"] == 1.0

    trivia_id = "spp.trivia-creative-writing-n5"
    trivia_task = render_task(trivia_id, TRIVIA)
    assert "Neil Armstrong" not in trivia_task
    trivia_score = score_response(
        trivia_id,
        TRIVIA,
        "Neil Armstrong took a dream voyage toward Mars.",
    )
    assert trivia_score["earned"] == 2
    assert trivia_score["possible"] == 2

    codenames_id = "spp.codenames-collaborative"
    spymaster_task = render_task(codenames_id, CODENAMES, role="spymaster")
    assert "Target words: moon, boot" in spymaster_task
    hint = parse_response(codenames_id, "Final answer: lunar", role="spymaster")
    assert hint == "lunar"
    guesser_task = render_task(
        codenames_id,
        CODENAMES,
        role="guesser",
        hint=hint,
    )
    assert "Target words:" not in guesser_task
    guesses = parse_response(
        codenames_id,
        "Answer: moon, boot",
        role="guesser",
    )
    codenames_score = score_response(codenames_id, CODENAMES, guesses)
    assert codenames_score["earned"] == 2
    assert codenames_score["possible"] == 2

    data_root = os.environ.get("HYPHA_SPP_DATA_ROOT")
    if data_root:
        for benchmark_id in (
            logic_id,
            trivia_id,
            "spp.trivia-creative-writing-n10",
            codenames_id,
        ):
            validate_dataset(benchmark_id, data_root)
    print("SPP adapter tests passed")


if __name__ == "__main__":
    main()

