from __future__ import annotations

import hashlib
import json
import math
import random
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable


PARAMETER_SCHEMA_VERSION = "1.0"
RESULT_SCHEMA_VERSION = 1
PARAMETERS: dict[str, dict[str, dict[str, float]]] = {
    "compression": {
        "compression_level": {"lower": 0.0, "upper": 5.0, "default": 3.0},
        "target_reduction": {"lower": 0.2, "upper": 0.7, "default": 0.35},
    },
    "thompson": {
        "alpha_prior": {"lower": 0.5, "upper": 3.0, "default": 2.0},
        "beta_prior": {"lower": 0.5, "upper": 3.0, "default": 1.0},
        "cost_weight": {"lower": 0.1, "upper": 0.5, "default": 0.25},
    },
    "caching": {
        "ttl_seconds": {"lower": 60.0, "upper": 600.0, "default": 246.87},
        "cache_threshold": {"lower": 0.1, "upper": 0.9, "default": 0.44},
    },
    "matrix": {
        "domain_weight": {"lower": 0.1, "upper": 0.5, "default": 0.3},
        "complexity_weight": {"lower": 0.2, "upper": 0.6, "default": 0.4},
        "task_weight": {"lower": 0.1, "upper": 0.5, "default": 0.3},
    },
    "dashboard": {
        "learning_rate": {"lower": 0.01, "upper": 0.2, "default": 0.05},
        "exploration_decay": {"lower": 0.85, "upper": 0.99, "default": 0.95},
        "alert_threshold": {"lower": 0.3, "upper": 0.9, "default": 0.7},
    },
}
SYSTEMS = tuple(PARAMETERS)


def validate_parameter(system: str, name: str, value: Any) -> float:
    if system not in PARAMETERS or name not in PARAMETERS[system]:
        raise ValueError(f"unsupported GA parameter: {system}.{name}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"GA parameter {system}.{name} must be numeric")
    number = float(value)
    spec = PARAMETERS[system][name]
    if not math.isfinite(number) or not spec["lower"] <= number <= spec["upper"]:
        raise ValueError(
            f"GA parameter {system}.{name} must be finite and in "
            f"[{spec['lower']}, {spec['upper']}]"
        )
    return number


@dataclass(frozen=True)
class GAConfig:
    population_size: int = 50
    generations: int = 25
    crossover_rate: float = 0.8
    mutation_rate: float = 0.1
    tournament_k: int = 3
    seed: int = 42


@dataclass
class Individual:
    system: str
    parameters: dict[str, float]
    fitness: float | None = None
    generation: int = 0


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w'-]+", text.casefold())


def _cosine_similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    vocab = set(a) | set(b)
    dot = sum(a.count(token) * b.count(token) for token in vocab)
    norm_a = math.sqrt(sum(a.count(token) ** 2 for token in vocab))
    norm_b = math.sqrt(sum(b.count(token) ** 2 for token in vocab))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _compression_fitness(p: dict[str, float], rng: random.Random) -> float:
    corpus = [
        "Memory records preserve context, decisions, evidence, and outcomes across sessions. "
        "Reliable memory supports retrieval, debugging, and consistent execution.",
        "Index revisions prevent stale documents from replacing newer content. "
        "Knowledge records retain provenance and source identity.",
        "Search combines lexical evidence and vector similarity. "
        "Reranking improves candidate ordering while preserving explainable scores.",
        "Execution context connects parent tasks, child tasks, and prior results. "
        "Idempotent events make retries safe after failures.",
    ]
    level, target = p["compression_level"], p["target_reduction"]
    original_count = compressed_count = 0
    similarities = []
    for text in corpus:
        sentences = [part.strip() + "." for part in text.split(". ") if part.strip()]
        keep = max(1, int(len(sentences) * (1.0 - level / 10.0)))
        if keep >= len(sentences):
            compressed = text
        else:
            importance = []
            for index, sentence in enumerate(sentences):
                position_score = 1.0 - abs(index - len(sentences) / 2) / len(sentences)
                length_score = min(1.0, len(sentence) / 100.0)
                importance.append((index, 0.4 * position_score + 0.6 * length_score))
            selected = sorted(index for index, _ in sorted(importance, key=lambda item: item[1], reverse=True)[:keep])
            compressed = " ".join(sentences[index] for index in selected)
        original_count += len(_tokens(text))
        compressed_count += len(_tokens(compressed))
        similarities.append(_cosine_similarity(text, compressed))
    if not original_count:
        return 0.0
    reduction = (original_count - compressed_count) / original_count
    similarity = sum(similarities) / len(similarities)
    if similarity < 0.85:
        similarity = max(0.0, similarity - (0.85 - similarity) * 2.0)
    bonus = 1.2 if abs(reduction - target) < 0.1 else 1.0
    return max(0.0, min(1.0, reduction * similarity * bonus))


def _thompson_fitness(p: dict[str, float], rng: random.Random) -> float:
    models = {
        "haiku": (0.0008, 0.80),
        "sonnet": (0.003, 0.88),
        "opus": (0.015, 0.95),
        "gemini": (0.002, 0.82),
    }
    optimal = {
        "code_review": "opus", "deployment": "sonnet",
        "documentation": "haiku", "testing": "sonnet", "debugging": "opus",
    }
    distribution = [
        ("code_review", 20), ("deployment", 15), ("documentation", 25),
        ("testing", 25), ("debugging", 15),
    ]
    tasks = [kind for kind, count in distribution for _ in range(count)]
    total = 0.0
    for _ in range(3):
        rng.shuffle(tasks)
        state = {name: [p["alpha_prior"], p["beta_prior"]] for name in models}
        cost_sum = quality_sum = correct = 0.0
        for task in tasks:
            scores = {
                name: rng.betavariate(*belief) * (1.0 - p["cost_weight"] * cost / 0.015)
                for name, (cost, _) in models.items()
                for belief in [state[name]]
            }
            selected = max(scores, key=scores.get)
            cost, base = models[selected]
            quality = max(0.5, min(1.0, base + (0.05 if optimal[task] == selected else 0.0) + rng.gauss(0, 0.05)))
            state[selected][0 if quality >= 0.85 else 1] += 1
            cost_sum += cost
            quality_sum += quality
            correct += int(selected == optimal[task])
        avg_cost, avg_quality = cost_sum / len(tasks), quality_sum / len(tasks)
        baseline = sum(value[0] for value in models.values()) / len(models)
        savings = max(0.0, (baseline - avg_cost) / baseline)
        quality_maintained = max(0.0, (avg_quality - 0.80) / 0.15)
        total += min(1.0, 0.5 * savings + 0.5 * quality_maintained + 0.2 * correct / len(tasks))
    return max(0.0, min(1.0, total / 3.0))


def _caching_fitness(p: dict[str, float], rng: random.Random) -> float:
    patterns = [
        ("memory_queries", 300, 0.10, 0.30),
        ("code_review", 1000, 0.30, 0.25),
        ("deployment", 500, 0.05, 0.15),
        ("documentation", 800, 0.20, 0.20),
        ("debugging", 600, 0.40, 0.10),
    ]
    scores = []
    for _ in range(5):
        cache: dict[str, tuple[int, int]] = {}
        hits = misses = saved = total = false_negatives = 0
        for turn in range(30):
            choice = rng.random()
            cumulative = 0.0
            pattern = patterns[-1]
            for item in patterns:
                cumulative += item[3]
                if choice <= cumulative:
                    pattern = item
                    break
            name, avg, change_rate, _ = pattern
            tokens = max(100, int(rng.gauss(avg, avg * 0.2)))
            cacheable = int(tokens * rng.uniform(0.5, 0.9))
            changed = rng.random() < change_rate
            body = f"{name}-{turn if changed else 0}"
            key = hashlib.sha256(body.encode()).hexdigest()
            now = (turn + 1) * 5
            if cacheable / tokens < p["cache_threshold"]:
                misses += 1
            elif key in cache and now - cache[key][0] <= p["ttl_seconds"]:
                hits += 1
                saved += cache[key][1]
            else:
                misses += 1
                if key in cache:
                    false_negatives += 1
                cache[key] = (now, cacheable)
            total += cacheable
            cache = {k: v for k, v in cache.items() if now - v[0] <= p["ttl_seconds"]}
        accesses = max(1, hits + misses)
        hit_rate = hits / accesses
        savings = saved / max(1, total)
        false_negative_rate = false_negatives / accesses
        penalty = max(0.0, (false_negative_rate - 0.05) * 10.0)
        scores.append(max(0.0, (hit_rate * 0.5 + min(1.0, savings * 2.0) * 0.5) * (1.0 - penalty)))
    return sum(scores) / len(scores)


_MATRIX = {
    "haiku": {"code_reading": .85, "documentation": .90, "simple_fixes": .80, "code_review": .65, "architecture": .50, "debugging": .70},
    "sonnet": {"code_reading": .92, "documentation": .85, "simple_fixes": .88, "code_review": .92, "architecture": .85, "debugging": .90},
    "opus": {"code_reading": .95, "documentation": .90, "simple_fixes": .95, "code_review": .97, "architecture": .95, "debugging": .98},
    "cursor": {"code_reading": .94, "documentation": .88, "simple_fixes": .93, "code_review": .96, "architecture": .94, "debugging": .97},
    "gemini": {"code_reading": .90, "documentation": .87, "simple_fixes": .85, "code_review": .88, "architecture": .92, "debugging": .84},
}
_MATRIX_OPTIMAL = {
    "code_reading": "sonnet", "documentation": "haiku", "simple_fixes": "sonnet",
    "code_review": "opus", "architecture": "opus", "debugging": "opus",
}
_MATRIX_COMPLEXITY = {
    "code_reading": .45, "documentation": .15, "simple_fixes": .25,
    "code_review": .72, "architecture": .90, "debugging": .72,
}


def _matrix_fitness(p: dict[str, float], rng: random.Random) -> float:
    total = p["domain_weight"] + p["complexity_weight"] + p["task_weight"]
    if total <= 0:
        return 0.0
    dw, cw, tw = p["domain_weight"] / total, p["complexity_weight"] / total, p["task_weight"] / total
    correct = 0
    tasks = list(_MATRIX_OPTIMAL.items()) * 20
    for task, optimal in tasks:
        complexity = _MATRIX_COMPLEXITY[task]
        scores = {}
        for model, capabilities in _MATRIX.items():
            capability = capabilities[task]
            complexity_score = (1 - complexity) if model == "haiku" else (1 - abs(complexity - .5)) if model == "sonnet" else complexity
            scores[model] = (tw * capability + cw * complexity_score + dw * capability) / (tw + cw + dw)
        correct += int(max(scores, key=scores.get) == optimal)
    return correct / len(tasks)


def _dashboard_fitness(p: dict[str, float], rng: random.Random) -> float:
    models = ("haiku", "sonnet", "opus")
    model_params = {
        "haiku": [1.0, 2.0], "sonnet": [2.0, 1.5], "opus": [3.0, 1.0]
    }
    costs = {"haiku": .1, "sonnet": 1.0, "opus": 10.0}
    tasks = []
    for task_id in range(500):
        drift = 1.0 + .3 * math.sin(task_id / 100)
        qualities = {
            model: rng.betavariate(model_params[model][0] * drift, model_params[model][1] / drift)
            for model in models
        }
        optimal = max(qualities, key=qualities.get)
        baseline_selected = rng.choice(models)
        tasks.append((qualities[baseline_selected], baseline_selected, optimal))
    history, correct, regressions, max_quality = [], 0, 0, 0.0
    for task_id, (actual_quality, _baseline_model, optimal) in enumerate(tasks):
        exploration = p["exploration_decay"] ** (task_id / len(tasks))
        samples = {}
        for model in models:
            alpha, beta = model_params[model]
            sample = rng.betavariate(alpha, beta)
            if rng.random() < (1.0 - exploration):
                sample = rng.random()
            samples[model] = sample
        selected = max(samples, key=samples.get)
        correct += int(selected == optimal)
        model_params[selected][0 if actual_quality >= .7 else 1] += p["learning_rate"]
        history.append(actual_quality)
        max_quality = max(max_quality, actual_quality)
        if task_id > 50 and sum(history[-50:]) / 50 < max_quality * .95:
            regressions += 1
    accuracy = correct / len(tasks)
    regression_rate = regressions / (len(tasks) / 50)
    stability = 1.0 - min(1.0, regression_rate * .5)
    first, last = sum(history[:50]) / 50, sum(history[-50:]) / 50
    improvement = max(0.0, (last - first) / first) if first > 0 else 0.0
    speed = min(1.0, improvement / .2)
    accuracy_component = min(1.0, accuracy * 1.2)
    return max(0.0, min(1.0, .4 * speed + .4 * stability + .2 * accuracy_component))


EVALUATORS: dict[str, Callable[[dict[str, float], random.Random], float]] = {
    "compression": _compression_fitness,
    "thompson": _thompson_fitness,
    "caching": _caching_fitness,
    "matrix": _matrix_fitness,
    "dashboard": _dashboard_fitness,
}


class GeneticAlgorithm:
    """Bounded, reproducible GA with evaluator-isolated populations."""

    def __init__(self, config: GAConfig | None = None):
        self.config = config or GAConfig()
        self._validate_config()
        self.rng = random.Random(self.config.seed)
        self.population: list[Individual] = []
        self.fitness_history: list[dict[str, Any]] = []
        self.best_by_system: dict[str, list[dict[str, Any]]] = {}
        self.total_evaluations = 0

    def _validate_config(self) -> None:
        c = self.config
        if isinstance(c.population_size, bool) or not isinstance(c.population_size, int) or c.population_size < len(SYSTEMS) * c.tournament_k:
            raise ValueError(f"population_size must be at least {len(SYSTEMS) * c.tournament_k}")
        if isinstance(c.generations, bool) or not isinstance(c.generations, int) or not 1 <= c.generations <= 100:
            raise ValueError("generations must be between 1 and 100")
        if not 1 <= c.tournament_k <= 10:
            raise ValueError("tournament_k must be between 1 and 10")
        for name, value in (("crossover_rate", c.crossover_rate), ("mutation_rate", c.mutation_rate)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be numeric and between 0 and 1")
        if isinstance(c.tournament_k, bool) or not isinstance(c.tournament_k, int) or not 1 <= c.tournament_k <= 10:
            raise ValueError("tournament_k must be an integer between 1 and 10")
        if isinstance(c.seed, bool) or not isinstance(c.seed, int) or not 0 <= c.seed <= 2**32 - 1:
            raise ValueError("seed must be an integer between 0 and 2^32-1")

    def _random_individual(self, system: str, generation: int = 0) -> Individual:
        params = {
            name: self.rng.uniform(spec["lower"], spec["upper"])
            for name, spec in PARAMETERS[system].items()
        }
        return Individual(system, params, generation=generation)

    def _evaluate(self, individual: Individual) -> float:
        canonical = json.dumps(individual.parameters, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{self.config.seed}:{individual.system}:{canonical}".encode()).digest()
        evaluator_rng = random.Random(int.from_bytes(digest[:8], "big"))
        fitness = EVALUATORS[individual.system](individual.parameters, evaluator_rng)
        if not math.isfinite(fitness):
            raise ValueError(f"{individual.system} evaluator returned non-finite fitness")
        self.total_evaluations += 1
        return max(0.0, min(1.0, float(fitness)))

    def _evaluate_population(self) -> None:
        for individual in self.population:
            if individual.fitness is None:
                individual.fitness = self._evaluate(individual)

    def _select(self, system: str) -> Individual:
        candidates = [item for item in self.population if item.system == system]
        return max(self.rng.sample(candidates, self.config.tournament_k), key=lambda item: item.fitness or 0.0)

    def _cross(self, left: Individual, right: Individual) -> tuple[Individual, Individual]:
        if left.system != right.system:
            raise ValueError("cannot cross individuals from different evaluators")
        if self.rng.random() >= self.config.crossover_rate:
            import copy
            return copy.deepcopy(left), copy.deepcopy(right)
        names = list(left.parameters)
        point = self.rng.randrange(len(names))
        a, b = {}, {}
        for index, name in enumerate(names):
            a[name], b[name] = (left.parameters[name], right.parameters[name]) if index < point else (right.parameters[name], left.parameters[name])
        return Individual(left.system, a), Individual(left.system, b)

    def _mutate(self, item: Individual) -> None:
        changed = False
        for name, spec in PARAMETERS[item.system].items():
            if self.rng.random() < self.config.mutation_rate:
                sigma = (spec["upper"] - spec["lower"]) * .1
                value = self.rng.gauss(item.parameters[name], sigma)
                item.parameters[name] = min(spec["upper"], max(spec["lower"], value))
                changed = True
        if changed:
            item.fitness = None

    def run(self) -> dict[str, Any]:
        per_system, remainder = divmod(self.config.population_size, len(SYSTEMS))
        for index, system in enumerate(SYSTEMS):
            count = per_system + int(index < remainder)
            self.population.extend(self._random_individual(system) for _ in range(count))
        for generation in range(self.config.generations):
            self._evaluate_population()
            stats = {}
            next_population = []
            for system in SYSTEMS:
                members = [item for item in self.population if item.system == system]
                scores = [float(item.fitness) for item in members]
                best = max(members, key=lambda item: item.fitness or 0.0)
                stats[system] = {
                    "best": max(scores), "mean": sum(scores) / len(scores),
                    "worst": min(scores), "population_size": len(members),
                    "best_parameters": dict(best.parameters),
                }
                ranked = sorted(members, key=lambda item: item.fitness or 0.0, reverse=True)
                next_population.append(Individual(system, dict(ranked[0].parameters), ranked[0].fitness, generation + 1))
                while sum(item.system == system for item in next_population) < len(members):
                    child_a, child_b = self._cross(self._select(system), self._select(system))
                    for child in (child_a, child_b):
                        self._mutate(child)
                        child.generation = generation + 1
                        next_population.append(child)
                        if sum(item.system == system for item in next_population) >= len(members):
                            break
            self.fitness_history.append({"generation": generation, "systems": stats})
            self.population = next_population
        self._evaluate_population()
        for system in SYSTEMS:
            ranked = sorted(
                (item for item in self.population if item.system == system),
                key=lambda item: item.fitness or 0.0,
                reverse=True,
            )
            self.best_by_system[system] = [
                {"rank": index + 1, "fitness": float(item.fitness), "parameters": dict(item.parameters)}
                for index, item in enumerate(ranked[:3])
            ]
        return {
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "parameter_schema_version": PARAMETER_SCHEMA_VERSION,
            "seed": self.config.seed,
            "population_size": self.config.population_size,
            "generations": self.config.generations,
            "total_evaluations": self.total_evaluations,
            "evaluation_scope": {
                "mode": "synthetic-local",
                "api_calls": 0,
                "externally_validated": False,
                "systems": list(SYSTEMS),
            },
            "fitness_history": self.fitness_history,
            "best_by_system": self.best_by_system,
        }
