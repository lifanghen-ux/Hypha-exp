from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import harbor.trial.trial as harbor_trial
from harbor.models.agent.context import AgentContext
from harbor.models.trial.result import TimingInfo
from harbor.trial.hooks import TrialEvent


class BudgetAwareTrial(harbor_trial.Trial):
    """Official Harbor trial with an opt-in adapter stop-request boundary."""

    async def _execute_agent(self) -> None:
        await self._invoke_hooks(TrialEvent.AGENT_START)

        self.result.agent_execution = TimingInfo(started_at=datetime.now(timezone.utc))

        continue_until = (
            self._task.config.agent.continue_until_timeout
            and self._agent_timeout_sec is not None
        )
        deadline = (
            time.monotonic() + self._agent_timeout_sec if continue_until else None
        )
        base_instruction = self._task.instruction
        instruction = base_instruction
        phase = 0
        aggregated: AgentContext | None = None

        try:
            while True:
                remaining = (
                    deadline - time.monotonic()
                    if deadline is not None
                    else self._agent_timeout_sec
                )
                if remaining is not None and remaining <= 0:
                    break

                phase_context = AgentContext()
                try:
                    await asyncio.wait_for(
                        self._agent.run(
                            instruction=instruction,
                            environment=self._environment,
                            context=phase_context,
                        ),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError as error:
                    if aggregated is not None:
                        aggregated = harbor_trial._merge_agent_contexts(
                            aggregated, phase_context
                        )
                        self.result.agent_result = aggregated
                    raise harbor_trial.AgentTimeoutError(
                        f"Agent execution timed out after {self._agent_timeout_sec} seconds"
                    ) from error

                if phase_context.metadata is None:
                    phase_context.metadata = {}
                phase_context.metadata["continue_until_timeout_phase"] = phase
                aggregated = harbor_trial._merge_agent_contexts(
                    aggregated, phase_context
                )
                self.result.agent_result = aggregated

                if phase_context.metadata.get("stop_requested") is True:
                    reason = phase_context.metadata.get("stop_reason") or "agent_requested"
                    if aggregated.metadata is None:
                        aggregated.metadata = {}
                    aggregated.metadata["protocol_stop_requested"] = True
                    aggregated.metadata["protocol_stop_reason"] = reason
                    self.result.agent_result = aggregated
                    self._logger.info(
                        "continue_until_timeout: agent requested clean stop at "
                        "phase %s (%s)",
                        phase + 1,
                        reason,
                    )
                    break

                if not continue_until:
                    break

                remaining = deadline - time.monotonic() if deadline is not None else 0
                if remaining <= 0:
                    break

                prior_user = self._environment.default_user
                self._environment.default_user = self._task.config.verifier.user
                try:
                    interim = await self._run_interim_verifier()
                except asyncio.TimeoutError:
                    await self._hide_shared_verifier_tests()
                    self._logger.warning(
                        "Interim verifier timed out during continue_until_timeout; "
                        "resuming agent until overall timeout"
                    )
                    phase += 1
                    instruction = harbor_trial._format_continue_instruction(
                        base_instruction,
                        phase=phase,
                        remaining_sec=max(0, int(remaining)),
                        feedback=(
                            "Interim verifier timed out. Keep working on the migration."
                        ),
                    )
                    continue
                finally:
                    self._environment.default_user = prior_user
                    await self._hide_shared_verifier_tests()

                if harbor_trial._verifier_passed(interim):
                    break

                phase += 1
                feedback = harbor_trial._read_verifier_feedback(
                    self._trial_paths.verifier_dir
                )
                instruction = harbor_trial._format_continue_instruction(
                    base_instruction,
                    phase=phase,
                    remaining_sec=max(0, int(remaining)),
                    feedback=feedback,
                )
                self._logger.info(
                    "continue_until_timeout: verification failed at phase %s; "
                    "resuming agent (%.0fs remaining)",
                    phase,
                    remaining,
                )
        finally:
            if aggregated is not None:
                if aggregated.metadata is None:
                    aggregated.metadata = {}
                if continue_until:
                    aggregated.metadata["continue_until_timeout_phases"] = phase
                self.result.agent_result = aggregated
            self.result.agent_execution.finished_at = datetime.now(timezone.utc)


def main() -> None:
    harbor_trial.Trial = BudgetAwareTrial
    from harbor.cli.main import app

    app()


if __name__ == "__main__":
    main()
