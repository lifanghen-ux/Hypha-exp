from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class KernelPlanLHTBAgent(BaseAgent):
    """Shared Harbor adapter for kernel-planned LHTB actions."""

    SUPPORTS_WINDOWS = False
    AGENT_NAME = "kernel-plan-lhtb"
    HELPER_SCRIPT = ""
    DEFAULT_NODE_PATH: str | None = None

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        planned_actions: list[dict[str, Any]] | None = None,
        execution_mode: str = "kernel_plan",
        helper_path: str | None = None,
        node_path: str | None = None,
        planner_timeout_sec: int = 60,
        **kwargs: Any,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self.planned_actions = planned_actions or []
        self.execution_mode = execution_mode
        self.helper_path = Path(helper_path) if helper_path else self._repo_root() / self.HELPER_SCRIPT
        self.node_path = node_path or self.DEFAULT_NODE_PATH or "node"
        self.planner_timeout_sec = planner_timeout_sec

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[3]

    @staticmethod
    def name() -> str:
        return KernelPlanLHTBAgent.AGENT_NAME

    def version(self) -> str:
        return "0.2.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "setup.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "agent": self.name(),
                    "version": self.version(),
                    "execution_mode": self.execution_mode,
                    "helper_path": str(self.helper_path),
                    "node_path": self.node_path,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _plan_with_kernel(self, instruction: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = {
            "instruction": instruction,
            "planned_actions": self.planned_actions,
            "model_alias": self.model_name or self.name(),
            "run_id": self.logs_dir.name,
        }
        completed = subprocess.run(
            [self.node_path, str(self.helper_path)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=self.planner_timeout_sec,
            check=False,
        )
        record: dict[str, Any] = {
            "command": [self.node_path, str(self.helper_path)],
            "return_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if completed.returncode != 0:
            raise RuntimeError(f"{self.name()} planner failed: {completed.stderr or completed.stdout}")

        kernel_result = json.loads(completed.stdout)
        record["kernel_result"] = kernel_result
        output = kernel_result.get("output") or {}
        actions = output.get("planned_actions") or []
        if not isinstance(actions, list):
            raise TypeError(f"{self.name()} planner output.planned_actions must be a list")
        return actions, record

    def _resolve_actions(self, instruction: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if self.execution_mode == "planned_actions":
            return self.planned_actions, None
        if self.execution_mode == "kernel_plan":
            return self._plan_with_kernel(instruction)
        raise ValueError(f"Unsupported execution_mode: {self.execution_mode}")

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        trajectory: list[dict[str, Any]] = []
        final_output = ""

        actions, planner_record = self._resolve_actions(instruction)
        if planner_record is not None:
            (self.logs_dir / "kernel_plan.json").write_text(
                json.dumps(planner_record, indent=2),
                encoding="utf-8",
            )

        for index, action in enumerate(actions, start=1):
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
                raise ValueError(f"Unsupported action type: {action_type}")

        if not actions:
            final_output = "No planned actions configured."
            trajectory.append({"index": 1, "type": "finish", "content": final_output})

        payload = {
            "agent": self.name(),
            "version": self.version(),
            "model_name": self.model_name,
            "execution_mode": self.execution_mode,
            "instruction_chars": len(instruction),
            "planner": planner_record,
            "trajectory": trajectory,
            "final_output": final_output,
        }
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        context.metadata = payload


class HyphaLHTBAgent(KernelPlanLHTBAgent):
    AGENT_NAME = "hypha-lhtb"
    HELPER_SCRIPT = "scripts/hypha-lhtb-kernel-plan.mjs"

    @staticmethod
    def name() -> str:
        return HyphaLHTBAgent.AGENT_NAME


class PiLHTBAgent(KernelPlanLHTBAgent):
    AGENT_NAME = "pi-lhtb"
    HELPER_SCRIPT = "scripts/pi-lhtb-kernel-plan.mjs"

    @staticmethod
    def name() -> str:
        return PiLHTBAgent.AGENT_NAME

    def __init__(self, *args: Any, **kwargs: Any):
        if "node_path" not in kwargs:
            kwargs["node_path"] = str(self._repo_root() / "benchmarks" / "pi-agent" / "node_modules" / "node" / "bin" / "node")
        super().__init__(*args, **kwargs)
