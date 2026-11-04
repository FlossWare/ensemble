from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FusionResult:
    candidate_id: str
    candidate: Mapping[str, Any]
    score: float
    ranks: Mapping[str, int]
    contributions: Mapping[str, float]


def reciprocal_rank_fusion(
    ranked_channels: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    k: int = 60,
    weights: Mapping[str, float] | None = None,
    identity_key: str = "chunk_id",
) -> list[FusionResult]:
    """Fuse independently ranked candidate lists using weighted RRF.

    Duplicate identities within a channel keep their first occurrence and do not
    consume additional rank positions. Candidate payloads are taken from the first
    channel (in mapping insertion order) that supplies the identity.
    """
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 10_000:
        raise ValueError("k must be an integer between 1 and 10000")
    if not isinstance(identity_key, str) or not identity_key.strip():
        raise ValueError("identity_key must be a non-empty string")
    configured = dict(weights or {})
    unknown = set(configured) - set(ranked_channels)
    if unknown:
        raise ValueError(f"weights supplied for unknown channels: {', '.join(sorted(unknown))}")
    channel_weights: dict[str, float] = {}
    for channel in ranked_channels:
        value = configured.get(channel, 1.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"weight for channel {channel} must be finite and non-negative")
        channel_weights[channel] = float(value)

    candidates: dict[str, Mapping[str, Any]] = {}
    ranks_by_id: dict[str, dict[str, int]] = {}
    contributions_by_id: dict[str, dict[str, float]] = {}
    for channel, ranked in ranked_channels.items():
        seen: set[str] = set()
        rank = 0
        for candidate in ranked:
            if not isinstance(candidate, Mapping):
                raise ValueError(f"candidate in channel {channel} must be a mapping")
            identity = candidate.get(identity_key)
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError(f"candidate in channel {channel} has no valid {identity_key}")
            if identity in seen:
                continue
            seen.add(identity)
            rank += 1
            candidates.setdefault(identity, candidate)
            ranks_by_id.setdefault(identity, {})[channel] = rank
            contributions_by_id.setdefault(identity, {})[channel] = (
                channel_weights[channel] / (k + rank)
            )

    results = []
    for identity, candidate in candidates.items():
        contributions = contributions_by_id[identity]
        results.append(FusionResult(
            candidate_id=identity,
            candidate=candidate,
            score=sum(contributions.values()),
            ranks=dict(ranks_by_id[identity]),
            contributions=dict(contributions),
        ))
    return sorted(results, key=lambda item: (-item.score, item.candidate_id))


def thompson_channel_weights(
    channel_stats: Mapping[str, Mapping[str, Any]],
    *,
    rng: random.Random | None = None,
) -> dict[str, float]:
    """Sample positive channel weights from per-channel Beta posteriors.

    Stats use the same successes/failures shape as Ensemble Thompson records.
    This is deliberately a pure policy function; persistence and feedback remain
    the responsibility of the Thompson capability.
    """
    sampler = rng or random.Random()
    weights: dict[str, float] = {}
    for channel, stats in sorted(channel_stats.items()):
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("channel names must be non-empty strings")
        successes, failures = stats.get("successes", 0), stats.get("failures", 0)
        for name, value in (("successes", successes), ("failures", failures)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} for channel {channel} must be a non-negative integer")
        weights[channel] = sampler.betavariate(successes + 1, failures + 1)
    return weights
