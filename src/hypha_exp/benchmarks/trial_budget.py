from __future__ import annotations

from dataclasses import dataclass
from typing import Any


USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
    "model_calls",
)


def _positive_limit(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    normalized = int(value)
    if normalized <= 0:
        raise ValueError(f"{name} must be positive when configured")
    return normalized


@dataclass(frozen=True)
class TrialBudgetLimits:
    max_phases: int | None = None
    max_model_calls: int | None = None
    max_tool_calls: int | None = None
    max_total_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_phases",
            "max_model_calls",
            "max_tool_calls",
            "max_total_tokens",
        ):
            object.__setattr__(self, name, _positive_limit(name, getattr(self, name)))

    def as_dict(self) -> dict[str, int | None]:
        return {
            "max_phases": self.max_phases,
            "max_model_calls": self.max_model_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_total_tokens": self.max_total_tokens,
        }


@dataclass(frozen=True)
class TrialBudgetSnapshot:
    limits: TrialBudgetLimits
    phases: int
    model_calls: int
    tool_calls: int
    total_tokens: int

    @property
    def stop_reasons(self) -> list[str]:
        reached: list[str] = []
        checks = (
            ("max_phases", self.phases, self.limits.max_phases),
            ("max_model_calls", self.model_calls, self.limits.max_model_calls),
            ("max_tool_calls", self.tool_calls, self.limits.max_tool_calls),
            ("max_total_tokens", self.total_tokens, self.limits.max_total_tokens),
        )
        for name, consumed, limit in checks:
            if limit is not None and consumed >= limit:
                reached.append(name)
        return reached

    @property
    def stop_reason(self) -> str | None:
        return self.stop_reasons[0] if self.stop_reasons else None

    @staticmethod
    def _remaining(limit: int | None, consumed: int) -> int | None:
        if limit is None:
            return None
        return max(0, limit - consumed)

    def remaining(self) -> dict[str, int | None]:
        return {
            "phases": self._remaining(self.limits.max_phases, self.phases),
            "model_calls": self._remaining(
                self.limits.max_model_calls, self.model_calls
            ),
            "tool_calls": self._remaining(
                self.limits.max_tool_calls, self.tool_calls
            ),
            "total_tokens": self._remaining(
                self.limits.max_total_tokens, self.total_tokens
            ),
        }

    def phase_limits(self, max_tool_calls_per_phase: int) -> dict[str, int | None]:
        remaining = self.remaining()
        remaining_tools = remaining["tool_calls"]
        return {
            "max_tool_calls": min(
                max_tool_calls_per_phase,
                remaining_tools
                if remaining_tools is not None
                else max_tool_calls_per_phase,
            ),
            "max_model_calls": remaining["model_calls"],
            "max_total_tokens": remaining["total_tokens"],
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "limits": self.limits.as_dict(),
            "consumed": {
                "phases": self.phases,
                "model_calls": self.model_calls,
                "tool_calls": self.tool_calls,
                "total_tokens": self.total_tokens,
            },
            "remaining": self.remaining(),
            "stop_reason": self.stop_reason,
            "stop_reasons": self.stop_reasons,
        }


def summarize_trial_budget(
    history: list[dict[str, Any]],
    limits: TrialBudgetLimits,
) -> TrialBudgetSnapshot:
    model_calls = 0
    tool_calls = 0
    total_tokens = 0
    for item in history:
        metrics = item.get("metrics") or {}
        usage = metrics.get("usage") or {}
        model_calls += int(usage.get("model_calls", 0) or 0)
        tool_calls += int(metrics.get("tool_calls", 0) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)
    return TrialBudgetSnapshot(
        limits=limits,
        phases=len(history),
        model_calls=model_calls,
        tool_calls=tool_calls,
        total_tokens=total_tokens,
    )
