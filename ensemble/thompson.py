from __future__ import annotations

import math
import random
from typing import Any


def select_model(
    state: dict[str, Any],
    task_type: str = "unknown",
    required_capability: float = 0.5,
    max_cost: float = math.inf,
    rng: random.Random | None = None,
) -> str:
    """Select a model using the POC's global Beta(successes+1, failures+1) posterior.

    task_type and required_capability are retained in the contract for the future
    capability-matrix integration. The POC currently does not use either to filter.
    """
    if not isinstance(task_type, str):
        raise ValueError("task_type must be a string")
    if isinstance(required_capability, bool) or not isinstance(required_capability, (int, float)) or not 0 <= required_capability <= 1:
        raise ValueError("required_capability must be between 0 and 1")
    if isinstance(max_cost, bool) or not isinstance(max_cost, (int, float)) or math.isnan(float(max_cost)) or max_cost < 0:
        raise ValueError("max_cost must be a non-negative number")
    models = state.get("models", {})
    if not isinstance(models, dict) or not models:
        return "haiku"
    candidates = {
        name: stats for name, stats in sorted(models.items())
        if (
            (stats.get("avg_cost", stats.get("total_cost", 0.0) / stats.get("calls", 1)) <= max_cost)
            or stats.get("calls", 0) == 0
        )
    }
    if not candidates:
        name = min(
            models,
            key=lambda item: (
                models[item].get("avg_cost", models[item].get("total_cost", 0.0) / max(1, models[item].get("calls", 0))),
                item,
            ),
        )
        candidates = {name: models[name]}
    sampler = rng or random.Random()
    selected, best = None, -1.0
    for name, stats in candidates.items():
        successes, failures = stats.get("successes", 0), stats.get("failures", 0)
        if (
            isinstance(successes, bool) or not isinstance(successes, int) or successes < 0
            or isinstance(failures, bool) or not isinstance(failures, int) or failures < 0
        ):
            raise ValueError(f"invalid Thompson statistics for model {name}")
        sample = sampler.betavariate(successes + 1, failures + 1)
        if sample > best:
            selected, best = name, sample
    return selected or "haiku"
