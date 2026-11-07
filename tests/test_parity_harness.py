import json
from pathlib import Path

import pytest

from parity.runner import compare_json, compare_observations, run_manifest


def test_compare_json_reports_nested_paths_and_ignores_explicit_fields():
    differences = compare_json(
        {"items": [{"id": "a", "score": 0.9}], "generated": "then"},
        {"items": [{"id": "a", "score": 0.901}], "generated": "now"},
        {"ignore_json_pointers": ["/generated"],
         "numeric_tolerances": {"/items/0/score": 0.01}},
    )
    assert differences == []


def test_compare_json_does_not_hide_unapproved_differences():
    differences = compare_json(
        {"results": [{"id": "a"}, {"id": "b"}]},
        {"results": [{"id": "b"}, {"id": "a"}]},
    )
    assert differences == ["/results/0/id: 'a' != 'b'", "/results/1/id: 'b' != 'a'"]


def test_compare_observations_checks_http_status_and_body():
    result = compare_observations(
        {"status_code": 200, "body": {"ready": True}},
        {"status_code": 503, "body": {"ready": False}},
    )
    assert result["status"] == "FAIL"
    assert len(result["differences"]) == 3


def test_missing_reference_or_candidate_is_not_run():
    report = run_manifest(
        {"version": 1, "scenarios": [{
            "id": "health", "capability": "platform",
            "reference": {"path": "/health"},
            "candidate": {"path": "/health"},
        }]},
        environ={},
    )
    assert report["overall"] == "NOT_RUN"
    assert report["summary"]["NOT_RUN"] == 1
    assert report["results"][0]["status"] == "NOT_RUN"


def test_initial_manifest_is_valid_and_has_stable_scenario_ids():
    manifest = json.loads(Path("parity/scenarios/core-contracts.json").read_text())
    ids = [scenario["id"] for scenario in manifest["scenarios"]]
    assert len(ids) == len(set(ids))
    assert all(scenario["reference"]["path"].startswith("/") for scenario in manifest["scenarios"])
    assert all(scenario["candidate"]["path"].startswith("/api/v1/") for scenario in manifest["scenarios"])


def test_matching_error_statuses_do_not_pass():
    result = compare_observations(
        {"status_code": 404, "body": {"error": "not found"}},
        {"status_code": 404, "body": {"error": "not found"}},
    )
    assert result["status"] == "FAIL"
    assert result["differences"][0] == "Reference HTTP status: 404 != expected 200"


def test_empty_manifest_is_rejected():
    with pytest.raises(ValueError, match="at least one scenario"):
        run_manifest({"version": 1, "scenarios": []}, environ={})



def test_json_pointer_projection_compares_only_declared_contract_fields():
    reference = {"label": "history", "confidence": 0.75, "evidence": [{"rule": "history"}]}
    candidate = {"label": "history", "confidence": 0.75, "evidence": [{"intent": "history"}], "extra": True}
    assert compare_json(
        reference, candidate,
        {"include_json_pointers": ["/label", "/confidence"]},
    ) == []


def test_json_pointer_projection_detects_missing_or_different_fields():
    reference = {"label": "history", "confidence": 0.75}
    candidate = {"label": "debug"}
    assert compare_json(
        reference, candidate,
        {"include_json_pointers": ["/label", "/confidence"]},
    ) == ["/label: 'history' != 'debug'", "/confidence: missing from candidate"]



def test_capability_map_references_known_scenarios_and_keeps_unmeasured_explicit():
    root = Path.cwd()
    capability_map = json.loads((root / "parity/capability-map.json").read_text())
    manifest = json.loads((root / "parity/scenarios/core-contracts.json").read_text())
    scenario_ids = {scenario["id"] for scenario in manifest["scenarios"]}
    capability_ids = [item["id"] for item in capability_map["capabilities"]]
    assert len(capability_ids) == len(set(capability_ids))
    for capability in capability_map["capabilities"]:
        assert set(capability["scenario_ids"]) <= scenario_ids
        if not capability["scenario_ids"]:
            assert capability["measurement"] == "mapped-unmeasured"
        else:
            assert capability["measurement"] == "scenario-backed"
