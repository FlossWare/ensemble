from __future__ import annotations

from ensemble.learning_analytics import analyze_outcomes


def outcomes(count=20):
    rows = []
    for index in range(count):
        rows.append({
            "task_id": f"task-{index}", "task_type": "review",
            "model": "model-a" if index % 2 else "model-b",
            "rating": 4, "tokens": 1000 + index, "cost": 0.01,
        })
    rows[-1].update(rating=1, tokens=100000, cost=10.0)
    return rows


def test_analyzer_reports_insufficient_samples_without_fitting():
    result = analyze_outcomes(outcomes(5))
    assert result["status"] == "insufficient_data"
    assert result["sample_count"] == 5
    assert result["anomalies"] == []


def test_analyzer_uses_pandas_and_sklearn_and_flags_outlier(tmp_path):
    result = analyze_outcomes(outcomes(), cache_dir=tmp_path)
    assert result["status"] == "measured"
    assert result["algorithm"] == "sklearn.ensemble.IsolationForest"
    assert result["feature_order"] == ["rating", "tokens", "cost"]
    assert result["anomaly_count"] >= 1
    assert any(item["task_id"] == "task-19" for item in result["anomalies"])
    assert result["promotion_eligible"] is False
    assert list(tmp_path.glob("*.joblib"))


def test_analyzer_reuses_content_addressed_cache(tmp_path):
    first = analyze_outcomes(outcomes(), cache_dir=tmp_path)
    second = analyze_outcomes(outcomes(), cache_dir=tmp_path)
    assert first["dataset_sha256"] == second["dataset_sha256"]
    assert first["anomalies"] == second["anomalies"]


def test_analyzer_rejects_invalid_values():
    rows = outcomes()
    rows[0]["cost"] = float("nan")
    try:
        analyze_outcomes(rows)
    except ValueError as exc:
        assert "finite" in str(exc)
    else:
        raise AssertionError("non-finite cost was accepted")
