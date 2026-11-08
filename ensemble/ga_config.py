"""Load the POC-compatible GA configuration using PyYAML."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "ga.yaml"
ALLOWED = {"population_size", "generations", "crossover_rate", "mutation_rate", "tournament_k", "seed"}


def load_ga_config(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path or os.getenv("ENSEMBLE_GA_CONFIG", DEFAULT_CONFIG)).expanduser()
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"GA configuration cannot be read: {source}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"GA configuration is invalid YAML: {source}") from exc
    if not isinstance(data, dict):
        raise ValueError("GA configuration root must be an object")
    unknown = set(data) - ALLOWED - {"systems"}
    if unknown:
        raise ValueError(f"unsupported GA configuration fields: {', '.join(sorted(unknown))}")
    systems = data.get("systems", {})
    if systems:
        if not isinstance(systems, dict):
            raise ValueError("GA systems configuration must be an object")
        from .ga import PARAMETERS
        if set(systems) != set(PARAMETERS):
            raise ValueError("GA systems must define exactly the supported systems")
        for system, parameters in systems.items():
            if not isinstance(parameters, dict):
                raise ValueError(f"GA system {system} must be an object")
            for name, spec in PARAMETERS[system].items():
                configured = parameters.get(name)
                if not isinstance(configured, dict):
                    raise ValueError(f"GA configuration is missing {system}.{name}")
                if configured.get("min") != spec["lower"] or configured.get("max") != spec["upper"]:
                    raise ValueError(f"GA bounds for {system}.{name} differ from the versioned parameter schema")
    values = {key: value for key, value in data.items() if key in ALLOWED}
    defaults = {"population_size": 50, "generations": 25, "crossover_rate": 0.8,
                "mutation_rate": 0.1, "tournament_k": 3, "seed": 42}
    defaults.update(values)
    from .ga import GAConfig
    try:
        GAConfig(**defaults)
        # Constructor validation is performed by GeneticAlgorithm; validate here
        # so malformed configuration fails before a run is persisted.
        from .ga import GeneticAlgorithm
        GeneticAlgorithm(GAConfig(**defaults))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid GA configuration: {exc}") from exc
    return defaults
