from __future__ import annotations

import pytest

from ensemble.ga_config import load_ga_config


def test_loads_poc_compatible_yaml_defaults():
    config = load_ga_config()
    assert config == {
        "population_size": 50, "generations": 25, "crossover_rate": 0.8,
        "mutation_rate": 0.1, "tournament_k": 3, "seed": 42,
    }


def test_rejects_parameter_bound_substitution(tmp_path):
    path = tmp_path / "ga.yaml"
    path.write_text("""
population_size: 50
systems:
  compression:
    compression_level: {min: 0, max: 99}
""")
    with pytest.raises(ValueError, match="exactly the supported systems|bounds"):
        load_ga_config(path)


def test_rejects_unknown_configuration_fields(tmp_path):
    path = tmp_path / "ga.yaml"
    path.write_text("population_size: 50\nnew_optimizer: true\n")
    with pytest.raises(ValueError, match="unsupported GA configuration fields"):
        load_ga_config(path)
