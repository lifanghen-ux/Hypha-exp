from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class HyphaLHTBAgent(BaseAgent):
    """Harbor adapter for running Hypha-controlled actions in LHTB tasks.

    This first version provides a deterministic shell-plan mode so that the
    custom-agent path can be benchmarked end to end before the full Hypha kernel
    policy is wired in. The full adapter should replace `planned_actions` with
    Hypha-generated actions while preserving the same Harbor interface.
    """

    SUPPORTS_WINDOWS = False

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        planned_actions: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self.planned_actions = planned_actions or []

    @staticmethod
    def name() -> str:
        return "hypha-lhtb"

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "setup.json").write_text(
            json.dumps({"status": "ok", "agent": self.name()}, indent=2),
            encoding="utf-8",
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        trajectory: list[dict[str, Any]] = []
        final_output = ""

        for index, action in enumerate(self.planned_actions, start=1):
            action_type = action.get("type", "shell")
            if action_type == "shell":
                command = str(action["command"])
                timeout_sec = action.get("timeout_sec")
                result = await environment.exec(
                    command,
                    timeout_sec=int(timeout_sec) if timeout_sec is not None else None,
                )
                item = {
                    "index": index,
                    "type": "shell",
                    "command": command,
                    "return_code": result.return_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
                trajectory.append(item)
                final_output = result.stdout or result.stderr or ""
                if result.return_code != 0 and action.get("stop_on_error", True):
                    break
            elif action_type == "finish":
                final_output = str(action.get("content", ""))
                trajectory.append({"index": index, "type": "finish", "content": final_output})
                break
            else:
                raise ValueError(f"Unsupported HyphaLHTBAgent action type: {action_type}")

        if not self.planned_actions:
            trajectory.append({"index": 1, "type": "finish", "content": "No planned actions configured."})
            final_output = "No planned actions configured."

        payload = {
            "agent": self.name(),
            "version": self.version(),
            "model_name": self.model_name,
            "instruction_chars": len(instruction),
            "trajectory": trajectory,
            "final_output": final_output,
        }
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        context.metadata = payload
