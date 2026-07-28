#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from harbor.models.agent.context import AgentContext

from hypha_exp.benchmarks.lhtb import PiRealLHTBAgent


class FakePiAgent(PiRealLHTBAgent):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.helper_calls: list[dict[str, int | None]] = []

    async def _run_pi_helper(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.helper_calls.append(kwargs["phase_limits"])
        phase = len(self.helper_calls)
        return {
            "status": "completed",
            "messages": [{"role": "user", "content": f"phase-{phase}"}],
            "tool_requests": [
                {"type": "tool_request", "id": f"{phase}-1"},
                {"type": "tool_request", "id": f"{phase}-2"},
            ],
            "usage": {
                "phase": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_tokens": 2,
                    "cache_write_tokens": 1,
                    "reasoning_tokens": 3,
                    "total_tokens": 20,
                    "model_calls": 2,
                },
                "all_messages": {},
            },
        }


class CancelledPiAgent(PiRealLHTBAgent):
    async def _run_pi_helper(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise asyncio.CancelledError


async def test_cross_phase_budget(logs_dir: Path) -> dict[str, Any]:
    agent = FakePiAgent(
        logs_dir=logs_dir,
        model_name="pi-real/test",
        max_tool_calls_per_phase=4,
        max_phases=2,
        max_model_calls=4,
        max_tool_calls=4,
        max_total_tokens=40,
    )
    await agent.setup(object())

    first = AgentContext()
    await agent.run("test", object(), first)
    assert first.n_input_tokens == 13
    assert first.n_cache_tokens == 3
    assert first.n_output_tokens == 5
    assert first.metadata is not None
    assert first.metadata["adapter_error"] is None
    assert first.metadata["stop_requested"] is False

    second = AgentContext()
    await agent.run("test", object(), second)
    assert second.metadata is not None
    assert second.metadata["stop_requested"] is True
    assert second.metadata["stop_reasons"] == [
        "max_phases",
        "max_model_calls",
        "max_tool_calls",
        "max_total_tokens",
    ]
    assert agent.helper_calls == [
        {
            "max_tool_calls": 4,
            "max_model_calls": 4,
            "max_total_tokens": 40,
        },
        {
            "max_tool_calls": 2,
            "max_model_calls": 2,
            "max_total_tokens": 20,
        },
    ]

    third = AgentContext()
    await agent.run("test", object(), third)
    assert third.metadata is not None
    assert third.metadata["stop_requested"] is True
    assert len(agent.helper_calls) == 2
    return second.metadata["trial_budget"]


async def test_cancelled_is_not_adapter_error(logs_dir: Path) -> dict[str, Any]:
    agent = CancelledPiAgent(
        logs_dir=logs_dir,
        model_name="pi-real/test",
        max_phases=2,
        max_model_calls=4,
        max_tool_calls=4,
        max_total_tokens=40,
    )
    await agent.setup(object())
    context = AgentContext()
    try:
        await agent.run("test", object(), context)
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("Cancelled adapter run must propagate cancellation")

    assert context.metadata is not None
    assert context.metadata["adapter_error"] is None
    assert context.metadata["stop_reason"] == "harbor_timeout"
    assert context.metadata["termination"]["status"] == "cancelled"
    return context.metadata["termination"]


async def main() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        budget = await test_cross_phase_budget(root / "budget")
        cancellation = await test_cancelled_is_not_adapter_error(
            root / "cancelled"
        )
    print(
        json.dumps(
            {"trial_budget": budget, "cancellation": cancellation},
            sort_keys=True,
        )
    )


asyncio.run(main())
