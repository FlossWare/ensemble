import pytest

from parity.retrieval_metrics import (
    evaluate_corpus,
    kendall_tau_common,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank_at_k,
    top_k_jaccard,
)


def test_retrieval_metrics_distinguish_quality_from_order_agreement():
    relevance = {"doc-a": 3, "doc-b": 1, "doc-c": 0}
    reference = ["doc-a", "doc-c", "doc-b"]
    candidate = ["doc-b", "doc-a", "doc-c"]

    assert recall_at_k(relevance, reference, 1) == 0.5
    assert reciprocal_rank_at_k(relevance, candidate, 1) == 1.0
    assert ndcg_at_k(relevance, reference, 3) > ndcg_at_k(relevance, candidate, 3)
    assert top_k_jaccard(reference, candidate, 1) == 0.0
    assert top_k_jaccard(reference, candidate, 3) == 1.0
    assert kendall_tau_common(reference, candidate) == pytest.approx(-1 / 3)


def test_corpus_report_is_macro_averaged_and_does_not_apply_unapproved_thresholds():
    report = evaluate_corpus({
        "schema_version": 1,
        "name": "small-fixture",
        "k_values": [1, 3],
        "queries": [{
            "id": "q1",
            "relevance": {"a": 3, "b": 1, "c": 0},
            "reference": ["a", "c", "b"],
            "candidate": ["b", "a", "c"],
        }],
    })
    assert report["overall"] == "MEASURED"
    assert report["thresholds_applied"] is False
    assert report["aggregate"]["3"]["delta_candidate_minus_reference"]["ndcg"] < 0
    assert report["aggregate"]["3"]["agreement"]["top_k_jaccard"] == 1.0


@pytest.mark.parametrize("corpus", [
    {"schema_version": 2, "queries": [{"id": "q", "relevance": {"a": 1}}]},
    {"schema_version": 1, "queries": []},
    {"schema_version": 1, "queries": [{"id": "q", "relevance": {"a": 0}}]},
    {"schema_version": 1, "queries": [{"id": "q", "relevance": {"a": 1}, "reference": ["a", "a"]}]},
])
def test_invalid_judged_corpora_are_rejected(corpus):
    with pytest.raises(ValueError):
        evaluate_corpus(corpus)
