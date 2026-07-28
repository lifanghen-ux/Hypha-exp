#!/usr/bin/env python3
from __future__ import annotations

import json

from hypha_exp.benchmarks.trial_budget import (
    TrialBudgetLimits,
    summarize_trial_budget,
)


def phase(model_calls: int, tool_calls: int, total_tokens: int) -> dict:
    return {
        "metrics": {
            "usage": {
                "model_calls": model_calls,
                "total_tokens": total_tokens,
            },
            "tool_calls": tool_calls,
        }
    }


limits = TrialBudgetLimits(
    max_phases=3,
    max_model_calls=7,
    max_tool_calls=10,
    max_total_tokens=1000,
)

history = [phase(model_calls=2, tool_calls=4, total_tokens=250)]
first = summarize_trial_budget(history, limits)
assert first.stop_reason is None
assert first.phase_limits(max_tool_calls_per_phase=4) == {
    "max_tool_calls": 4,
    "max_model_calls": 5,
    "max_total_tokens": 750,
}

history.append(phase(model_calls=3, tool_calls=4, total_tokens=350))
second = summarize_trial_budget(history, limits)
assert second.stop_reason is None
assert second.phase_limits(max_tool_calls_per_phase=4) == {
    "max_tool_calls": 2,
    "max_model_calls": 2,
    "max_total_tokens": 400,
}

history.append(phase(model_calls=2, tool_calls=2, total_tokens=400))
third = summarize_trial_budget(history, limits)
assert third.phases == 3
assert third.model_calls == 7
assert third.tool_calls == 10
assert third.total_tokens == 1000
assert third.stop_reasons == [
    "max_phases",
    "max_model_calls",
    "max_tool_calls",
    "max_total_tokens",
]
assert third.phase_limits(max_tool_calls_per_phase=4) == {
    "max_tool_calls": 0,
    "max_model_calls": 0,
    "max_total_tokens": 0,
}

print(json.dumps(third.as_dict(), sort_keys=True))
