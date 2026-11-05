from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .ga import SYSTEMS, validate_parameter


OPTIMIZER_VERSION = "0.1"


def build_ga_learning_artifact(run: dict[str, Any]) -> dict[str, Any]:
    """Convert one completed synthetic GA run into a portable Learning artifact."""
    if not isinstance(run, dict) or run.get("status") != "completed":
        raise ValueError("a completed GA run is required")
    run_id = run.get("id")
    result = run.get("result")
    config = run.get("config")
    created_at = run.get("completed_at")
    if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 256:
        raise ValueError("GA run id is required")
    if not isinstance(result, dict) or not isinstance(config, dict):
        raise ValueError("completed GA run must contain result and configuration objects")
    if not isinstance(created_at, str) or not created_at.strip():
        raise ValueError("completed GA run timestamp is required")
    best_by_system = result.get("best_by_system")
    if not isinstance(best_by_system, dict):
        raise ValueError("GA result must contain best_by_system")

    selected_parameters: dict[str, dict[str, float]] = {}
    objectives: dict[str, float] = {}
    top_candidates: dict[str, list[dict[str, Any]]] = {}
    for system, candidates in sorted(best_by_system.items()):
        if system not in SYSTEMS:
            raise ValueError(f"unsupported GA result system: {system}")
        if not isinstance(candidates, list):
            raise ValueError(f"GA candidates for {system} must be an array")
        normalized = []
        for candidate in candidates[:3]:
            if not isinstance(candidate, dict):
                continue
            fitness = candidate.get("fitness")
            if isinstance(fitness, bool) or not isinstance(fitness, (int, float)) or not math.isfinite(float(fitness)):
                continue
            parameters = candidate.get("parameters")
            if not isinstance(parameters, dict):
                raise ValueError(f"GA candidate parameters for {system} must be an object")
            validated = {
                str(name): validate_parameter(system, str(name), value)
                for name, value in parameters.items()
            }
            normalized.append({
                "rank": len(normalized) + 1,
                "fitness": float(fitness),
                "parameters": validated,
            })
        if normalized:
            top_candidates[system] = normalized
            selected_parameters[system] = dict(normalized[0]["parameters"])
            objectives[system] = normalized[0]["fitness"]

    try:
        identity = json.dumps(
            {"ga_run_id": run_id, "config": config, "result": result},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except ValueError as exc:
        raise ValueError("GA result and configuration must contain finite JSON numbers") from exc
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    artifact_run_id = f"ga-{run_id}-{digest}"
    payload = {
        "run_id": artifact_run_id,
        "ga_run_id": run_id,
        "optimizer": {"name": "genetic-algorithm", "version": OPTIMIZER_VERSION},
        "population_size": result.get("population_size"),
        "generations": result.get("generations"),
        "total_evaluations": result.get("total_evaluations"),
        "evaluation_scope": {
            "systems_evaluated": sorted(selected_parameters),
            "evaluation_mode": "synthetic-local-evaluators",
            "api_calls": 0,
        },
        "candidate_population": {
            "kind": "top_candidates_per_system",
            "candidates": top_candidates,
        },
        "objectives": objectives,
        "selected_parameters": selected_parameters,
        "fallback_parameters": {},
        "evidence": {
            "kind": "synthetic-fitness",
            "externally_validated": False,
            "confidence": None,
        },
        "configuration_status": "candidate",
        "knowledge_promotion": {
            "eligible": False,
            "reason": "synthetic GA fitness alone is not operational evidence",
        },
    }
    artifact = {
        "schema": "learning-artifact",
        "version": "0.1",
        "artifact_type": "ga.tuning.result",
        "source": "ensemble.ga",
        "created_at": created_at,
        "provenance": {
            "ga_run_id": run_id,
            "optimizer": "genetic-algorithm",
            "optimizer_version": OPTIMIZER_VERSION,
            "configuration": config,
            "candidate_count_by_system": {
                system: len(candidates) for system, candidates in top_candidates.items()
            },
        },
        "payload": payload,
    }
    # Enforce the same finite, portable JSON contract as LearningStore.
    json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return artifact
