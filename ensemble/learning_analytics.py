"""Learning analytics built on the POC's pandas/scikit-learn stack.

Anomaly scores are descriptive signals over observed outcomes, never automatic
promotion or routing decisions. The fitted detector cache is content-addressed
and protected against concurrent writers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from filelock import FileLock
from sklearn.ensemble import IsolationForest

FEATURES = ("rating", "tokens", "cost")
MINIMUM_TRAINING_ROWS = 10
CACHE_VERSION = 1


def _frame(outcomes: list[dict[str, Any]]) -> pd.DataFrame:
    if not isinstance(outcomes, list):
        raise ValueError("outcomes must be a list")
    rows = []
    for row in outcomes:
        if not isinstance(row, dict):
            raise ValueError("each outcome must be an object")
        values = {name: row.get(name) for name in FEATURES}
        for name in FEATURES:
            value = values[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"outcome {name} must be numeric")
            if not pd.notna(value) or value < 0:
                raise ValueError(f"outcome {name} must be finite and non-negative")
        if not 1 <= values["rating"] <= 5:
            raise ValueError("outcome rating must be between 1 and 5")
        rows.append({
            **values,
            "task_id": str(row.get("task_id", "")),
            "task_type": str(row.get("task_type", "")),
            "model": str(row.get("model", "")),
        })
    return pd.DataFrame(rows, columns=[*FEATURES, "task_id", "task_type", "model"])


def analyze_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    cache_dir: str | Path | None = None,
    contamination: float = 0.1,
) -> dict[str, Any]:
    """Summarize observed outcomes and flag multivariate anomalies."""
    if not 0.0 < contamination <= 0.5:
        raise ValueError("contamination must be greater than 0 and at most 0.5")
    frame = _frame(outcomes)
    if frame.empty:
        return {"ok": True, "status": "insufficient_data", "sample_count": 0,
                "minimum_samples": MINIMUM_TRAINING_ROWS, "anomalies": []}

    summary = {
        "sample_count": int(len(frame)),
        "average_rating": round(float(frame["rating"].mean()), 4),
        "average_tokens": round(float(frame["tokens"].mean()), 2),
        "average_cost": round(float(frame["cost"].mean()), 6),
        "by_model": {
            str(model): {
                "sample_count": int(len(group)),
                "average_rating": round(float(group["rating"].mean()), 4),
                "average_cost": round(float(group["cost"].mean()), 6),
            }
            for model, group in frame.groupby("model", sort=True)
        },
    }
    if len(frame) < MINIMUM_TRAINING_ROWS:
        return {"ok": True, "status": "insufficient_data",
                "minimum_samples": MINIMUM_TRAINING_ROWS, **summary, "anomalies": []}

    feature_frame = frame.loc[:, FEATURES].astype("float64")
    canonical = json.dumps(
        {"version": CACHE_VERSION, "contamination": contamination,
         "rows": feature_frame.to_dict(orient="records")},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    detector = None
    cache_path = None
    if cache_dir is not None:
        directory = Path(cache_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        cache_path = directory / f"outcome-anomaly-{digest}.joblib"
        with FileLock(str(cache_path) + ".lock", timeout=10):
            if cache_path.is_file():
                try:
                    cached = joblib.load(cache_path)
                    if cached.get("digest") == digest and cached.get("version") == CACHE_VERSION:
                        detector = cached.get("detector")
                except Exception:
                    detector = None
            if detector is None:
                detector = IsolationForest(
                    n_estimators=100, contamination=contamination,
                    random_state=42, n_jobs=1,
                ).fit(feature_frame)
                temporary = cache_path.with_suffix(".joblib.tmp")
                joblib.dump(
                    {"version": CACHE_VERSION, "digest": digest, "detector": detector},
                    temporary,
                )
                os.replace(temporary, cache_path)
    else:
        detector = IsolationForest(
            n_estimators=100, contamination=contamination,
            random_state=42, n_jobs=1,
        ).fit(feature_frame)

    labels = detector.predict(feature_frame)
    scores = detector.decision_function(feature_frame)
    anomalies = []
    for index, (label, score) in enumerate(zip(labels, scores)):
        if int(label) == -1:
            row = frame.iloc[index]
            anomalies.append({
                "task_id": row["task_id"],
                "task_type": row["task_type"],
                "model": row["model"],
                "rating": int(row["rating"]),
                "tokens": int(row["tokens"]),
                "cost": float(row["cost"]),
                "anomaly_score": round(float(score), 8),
            })
    anomalies.sort(key=lambda item: (item["anomaly_score"], item["task_id"]))
    return {
        "ok": True, "status": "measured", **summary,
        "algorithm": "sklearn.ensemble.IsolationForest",
        "algorithm_version": __import__("sklearn").__version__,
        "feature_order": list(FEATURES),
        "dataset_sha256": digest,
        "cache": "enabled" if cache_path else "disabled",
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "promotion_eligible": False,
        "interpretation": "Anomalies are review signals, not evidence of failure or grounds for automatic promotion.",
    }
