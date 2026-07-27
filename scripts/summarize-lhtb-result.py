#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    result_path = Path(sys.argv[1])
    data = json.loads(result_path.read_text(encoding="utf-8"))
    trial_rewards: dict[str, float] = {}
    eval_agent_by_trial: dict[str, str] = {}
    for eval_key, eval_value in (data.get("stats", {}).get("evals", {}) or {}).items():
        eval_agent = eval_key.split("__", 1)[0]
        reward_stats = (eval_value.get("reward_stats") or {}).get("reward") or {}
        for reward_text, trial_names in reward_stats.items():
            for trial_name in trial_names:
                trial_rewards[trial_name] = float(reward_text)
                eval_agent_by_trial[trial_name] = eval_agent
    trials = []
    for child in sorted(result_path.parent.glob("*/result.json")):
        trials.append(json.loads(child.read_text(encoding="utf-8")))
    rows = []
    for trial in trials:
        agent_info = trial.get("agent_info") or {}
        trial_name = trial.get("trial_name")
        agent = agent_info.get("name") or eval_agent_by_trial.get(trial_name)
        task = trial.get("task_name") or trial.get("task_id")
        reward = trial.get("verifier_result", {}).get("reward")
        if reward is None:
            reward = trial.get("reward")
        if reward is None and trial_name in trial_rewards:
            reward = trial_rewards[trial_name]
        exception = trial.get("exception") or trial.get("error")
        rows.append(
            {
                "agent": agent,
                "task": task,
                "reward": reward,
                "success": reward is not None and float(reward) >= 0.95,
                "exception": bool(exception),
            }
        )
    by_agent: dict[str, list[dict]] = {}
    for row in rows:
        by_agent.setdefault(str(row["agent"]), []).append(row)
    summary = {}
    for agent, agent_rows in by_agent.items():
        rewards = [float(row["reward"]) for row in agent_rows if row["reward"] is not None]
        summary[agent] = {
            "trials": len(agent_rows),
            "mean_reward": sum(rewards) / len(rewards) if rewards else None,
            "success_rate": sum(1 for row in agent_rows if row["success"]) / len(agent_rows) if agent_rows else None,
            "exceptions": sum(1 for row in agent_rows if row["exception"]),
        }
    print(
        json.dumps(
            {
                "result_path": str(result_path),
                "job_stats": data.get("stats", {}),
                "rows": rows,
                "summary": summary,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
