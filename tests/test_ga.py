import pytest

from ensemble.ga import (
    GAConfig,
    GeneticAlgorithm,
    PARAMETERS,
    SYSTEMS,
    validate_parameter,
)


def test_parameter_schema_rejects_unknown_nonfinite_and_out_of_range_values():
    assert validate_parameter("compression", "compression_level", 2) == 2.0
    with pytest.raises(ValueError):
        validate_parameter("missing", "x", 1)
    with pytest.raises(ValueError):
        validate_parameter("compression", "compression_level", float("nan"))
    with pytest.raises(ValueError):
        validate_parameter("compression", "compression_level", 99)
    with pytest.raises(ValueError):
        validate_parameter("compression", "compression_level", True)


def test_ga_run_is_reproducible_and_reports_all_five_evaluators():
    config = GAConfig(population_size=15, generations=1, tournament_k=3, seed=2718)
    first = GeneticAlgorithm(config).run()
    second = GeneticAlgorithm(config).run()

    assert first == second
    assert set(first["best_by_system"]) == set(SYSTEMS)
    assert first["total_evaluations"] == 24
    assert first["evaluation_scope"] == {
        "mode": "synthetic-local",
        "api_calls": 0,
        "externally_validated": False,
        "systems": list(SYSTEMS),
    }
    assert len(first["fitness_history"]) == 1
    for system, candidates in first["best_by_system"].items():
        assert len(candidates) == 3
        assert candidates[0]["rank"] == 1
        assert 0.0 <= candidates[0]["fitness"] <= 1.0
        for candidate in candidates:
            for name, value in candidate["parameters"].items():
                spec = PARAMETERS[system][name]
                assert spec["lower"] <= value <= spec["upper"]


def test_ga_rejects_unbounded_or_invalid_run_configuration():
    with pytest.raises(ValueError):
        GeneticAlgorithm(GAConfig(population_size=14, generations=1))
    with pytest.raises(ValueError):
        GeneticAlgorithm(GAConfig(population_size=15, generations=101))
    with pytest.raises(ValueError):
        GeneticAlgorithm(GAConfig(population_size=15, generations=1, mutation_rate=2.0))
