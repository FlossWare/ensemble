import random

import pytest

from ensemble.rrf import reciprocal_rank_fusion, thompson_channel_weights


def test_rrf_fuses_channels_with_ranks_and_contributions():
    fused = reciprocal_rank_fusion({
        "lexical": [{"chunk_id": "a"}, {"chunk_id": "b"}],
        "vector": [{"chunk_id": "b"}, {"chunk_id": "c"}],
        "graph": [{"chunk_id": "c"}, {"chunk_id": "a"}],
    }, k=10)
    assert [item.candidate_id for item in fused] == ["a", "b", "c"]
    assert fused[0].ranks == {"lexical": 1, "graph": 2}
    assert fused[0].contributions == {"lexical": 1 / 11, "graph": 1 / 12}


def test_weighted_rrf_applies_channel_weights():
    fused = reciprocal_rank_fusion({
        "lexical": [{"chunk_id": "a"}],
        "vector": [{"chunk_id": "b"}],
    }, k=10, weights={"lexical": 2.0, "vector": 0.5})
    assert fused[0].candidate_id == "a"
    assert fused[0].score == 2 / 11
    assert fused[1].score == 0.5 / 11


def test_rrf_deduplicates_within_channel_before_assigning_rank():
    fused = reciprocal_rank_fusion({
        "lexical": [{"chunk_id": "a"}, {"chunk_id": "a"}, {"chunk_id": "b"}],
    }, k=10)
    assert fused[0].ranks == {"lexical": 1}
    assert fused[1].ranks == {"lexical": 2}


def test_rrf_has_stable_identity_ties_and_first_payload_wins():
    fused = reciprocal_rank_fusion({
        "lexical": [{"chunk_id": "b", "text": "first"}, {"chunk_id": "a"}],
        "vector": [{"chunk_id": "b", "text": "second"}],
    }, k=10)
    assert [item.candidate_id for item in fused] == ["b", "a"]
    assert fused[0].candidate["text"] == "first"


@pytest.mark.parametrize("kwargs", [
    {"k": 0}, {"k": 10001}, {"k": True},
    {"weights": {"missing": 1.0}},
    {"weights": {"lexical": -1.0}},
    {"weights": {"lexical": float("nan")}},
])
def test_rrf_rejects_invalid_configuration(kwargs):
    base = {"ranked_channels": {"lexical": [{"chunk_id": "a"}]}}
    base.update(kwargs)
    with pytest.raises(ValueError):
        reciprocal_rank_fusion(**base)


def test_thompson_channel_weights_are_seed_reproducible_and_positive():
    stats = {"lexical": {"successes": 8, "failures": 2}, "vector": {"successes": 2, "failures": 8}}
    first = thompson_channel_weights(stats, rng=random.Random(7))
    second = thompson_channel_weights(stats, rng=random.Random(7))
    assert first == second
    assert all(value > 0 for value in first.values())


def test_thompson_channel_weights_validate_feedback():
    with pytest.raises(ValueError):
        thompson_channel_weights({"lexical": {"successes": -1}})


def test_rrf_normalizes_uuid_candidate_identity():
    from uuid import uuid4

    identity = uuid4()
    fused = reciprocal_rank_fusion({
        "lexical": [{"chunk_id": identity}],
    })
    assert fused[0].candidate_id == str(identity)
