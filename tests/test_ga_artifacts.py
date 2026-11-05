from ensemble.ga_artifacts import build_ga_learning_artifact


def test_ga_learning_artifact_preserves_candidates_and_blocks_knowledge_promotion():
    run = {
        "id": "run-123",
        "status": "completed",
        "created_at": "2026-11-05T00:00:00+00:00",
        "completed_at": "2026-11-05T00:01:00+00:00",
        "config": {"population_size": 15, "generations": 1, "seed": 1234},
        "result": {
            "population_size": 15, "generations": 1, "total_evaluations": 24,
            "best_by_system": {
                "compression": [
                    {"rank": 1, "fitness": 0.91, "parameters": {"compression_level": 4.0, "target_reduction": 0.4}},
                    {"rank": 2, "fitness": 0.82, "parameters": {"compression_level": 3.0, "target_reduction": 0.35}},
                ],
                "thompson": [],
            },
        },
    }
    artifact = build_ga_learning_artifact(run)
    assert artifact["artifact_type"] == "ga.tuning.result"
    assert artifact["payload"]["run_id"].startswith("ga-run-123-")
    assert artifact["payload"]["selected_parameters"]["compression"]["compression_level"] == 4.0
    assert len(artifact["payload"]["candidate_population"]["candidates"]["compression"]) == 2
    assert artifact["payload"]["evidence"]["externally_validated"] is False
    assert artifact["payload"]["knowledge_promotion"]["eligible"] is False
    assert build_ga_learning_artifact(run) == artifact


def test_ga_learning_artifact_rejects_invalid_candidate_parameters_and_fitness():
    import math
    import pytest

    run = {
        "id": "run-invalid", "status": "completed",
        "completed_at": "2026-11-05T00:01:00+00:00", "config": {},
        "result": {"best_by_system": {"compression": [
            {"fitness": 0.9, "parameters": {"unknown": 1.0}},
        ]}},
    }
    with pytest.raises(ValueError, match="unsupported GA parameter"):
        build_ga_learning_artifact(run)
    run["result"]["best_by_system"]["compression"] = [
        {"fitness": math.nan, "parameters": {"compression_level": 4.0}},
    ]
    with pytest.raises(ValueError, match="finite JSON numbers"):
        build_ga_learning_artifact(run)

