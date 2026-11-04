import pytest

from ensemble.retrieval_eval import evaluate_runs, ndcg_at_k, recall_at_k, reciprocal_rank_at_k


def test_retrieval_metrics_reward_relevant_results_and_graded_order():
    relevance = {"a": 3, "b": 1, "c": 0}
    ranked = ["b", "a", "c"]
    assert recall_at_k(ranked, relevance, 2) == 1.0
    assert reciprocal_rank_at_k(ranked, relevance, 2) == 1.0
    assert 0 < ndcg_at_k(ranked, relevance, 2) < 1.0
    assert ndcg_at_k(["a", "b"], relevance, 2) == 1.0


def test_evaluate_runs_compares_lexical_vector_and_fusion_baselines():
    result = evaluate_runs(
        {"q1": {"a": 1, "b": 1}, "q2": {"c": 1}},
        {
            "lexical": {"q1": ["a"], "q2": ["x", "c"]},
            "vector": {"q1": ["b", "x"], "q2": ["c"]},
            "rrf": {"q1": ["a", "b"], "q2": ["c"]},
        },
        k=2,
    )
    assert result["systems"]["rrf"]["macro"]["recall@2"] == 1.0
    assert result["systems"]["rrf"]["macro"]["mrr@2"] == 1.0
    assert result["systems"]["lexical"]["macro"]["recall@2"] < 1.0


@pytest.mark.parametrize("qrels,runs,k", [
    ({"q": {"a": -1}}, {}, 10),
    ({"q": {"a": 1}}, {"x": {"q": "a"}}, 10),
    ({"q": {"a": 1}}, {"x": {"q": ["a"]}}, 0),
])
def test_evaluation_rejects_invalid_inputs(qrels, runs, k):
    with pytest.raises(ValueError):
        evaluate_runs(qrels, runs, k=k)
