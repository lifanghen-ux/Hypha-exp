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

    def _history_path(self) -> Path:
        return self.logs_dir / "history.json"

    def _history_full_path(self) -> Path:
        return self.logs_dir / "history_full.jsonl"

    def _load_history(self) -> list[dict[str, Any]]:
        path = self._history_path()
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_history(self, history: list[dict[str, Any]]) -> None:
        self._history_path().write_text(
            json.dumps(history, indent=2),
            encoding="utf-8",
        )

    def _append_history_full(self, payload: dict[str, Any]) -> None:
        path = self._history_full_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _read_tail(self, path: Path, max_chars: int = 4000) -> str | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:]

    def _verifier_feedback(self) -> dict[str, Any]:
        trial_dir = self.logs_dir.parent
        verifier_dir = trial_dir / "verifier"
        feedback = {
            "reward": self._read_tail(verifier_dir / "reward.txt", 200),
            "test_stdout_tail": self._read_tail(verifier_dir / "test-stdout.txt", 5000),
            "install_log_tail": self._read_tail(verifier_dir / "install.log", 5000),
            "verifier_json": {},
        }
        for details_path in sorted(verifier_dir.glob("*.json")):
            try:
                feedback["verifier_json"][details_path.name] = json.loads(details_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                feedback["verifier_json"][details_path.name] = self._read_tail(details_path, 5000)
        return feedback

    @staticmethod
    def _looks_mutating(command: str) -> bool:
        markers = (
            "cat >",
            "tee ",
            "python - <<",
            "python3 - <<",
            "apply_patch",
            "sed -i",
            "perl -pi",
            "cp ",
            "mv ",
            "rm ",
            "mkdir ",
            "pip install",
            "python -m pip install",
        )
        return any(marker in command for marker in markers)

    @staticmethod
    def _generates_required_replay(actions: list[dict[str, Any]]) -> bool:
        for action in actions:
            command = str(action.get("command", ""))
            if "outputs/replay_results.jsonl" in command:
                return True
        return False

    def _close_loop_actions(
        self,
        instruction: str,
        actions: list[dict[str, Any]],
        phase_index: int,
    ) -> list[dict[str, Any]]:
        updated = list(actions)
        needs_replay = "outputs/replay_results.jsonl" in instruction
        shell_commands = [str(action.get("command", "")) for action in updated if action.get("type", "shell") == "shell"]
        made_progress = any(self._looks_mutating(command) for command in shell_commands)

        if phase_index >= 2 and not made_progress:
            updated.append(
                {
                    "type": "shell",
                    "command": (
                        "cd /app && echo '[agent-guard] phase requires concrete progress; "
                        "run a focused test or install command, then use verifier feedback for the next edit' && "
                        "(python -m pip install -e . pytest==8.4.1 || python -m pip install -e . || true)"
                    ),
                    "timeout_sec": 300,
                    "stop_on_error": False,
                }
            )

        if needs_replay and not self._generates_required_replay(updated):
            updated.append(
                {
                    "type": "shell",
                    "command": (
                        "cd /app && mkdir -p outputs && "
                        "python -m support_rag.cli replay --history data/history --out outputs/replay_results.jsonl"
                    ),
                    "timeout_sec": 240,
                    "stop_on_error": False,
                }
            )
        return updated

    def _plan_with_kernel(
        self,
        instruction: str,
        history: list[dict[str, Any]],
        phase_index: int,
        verifier_feedback: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = {
            "instruction": instruction,
            "planned_actions": self.planned_actions,
            "model_alias": self.model_name or self.name(),
            "run_id": self.logs_dir.name,
            "history": history,
            "phase_index": phase_index,
            "verifier_feedback": verifier_feedback,
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
        actions = self._close_loop_actions(instruction, actions, phase_index)
        return actions, record

    def _resolve_actions(
        self,
        instruction: str,
        history: list[dict[str, Any]],
        phase_index: int,
        verifier_feedback: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if self.execution_mode == "planned_actions":
            return self.planned_actions, None
        if self.execution_mode == "kernel_plan":
            return self._plan_with_kernel(instruction, history, phase_index, verifier_feedback)
        raise ValueError(f"Unsupported execution_mode: {self.execution_mode}")

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        trajectory: list[dict[str, Any]] = []
        final_output = ""

        history = self._load_history()
        phase_index = len(history) + 1
        verifier_feedback = self._verifier_feedback()
        actions, planner_record = self._resolve_actions(instruction, history, phase_index, verifier_feedback)
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
            "phase_index": phase_index,
            "verifier_feedback": verifier_feedback,
            "planner": planner_record,
            "trajectory": trajectory,
            "final_output": final_output,
        }
        history.append(payload)
        self._save_history(history)
        self._append_history_full(payload)
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
