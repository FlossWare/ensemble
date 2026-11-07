import json
from pathlib import Path

from parity.dependency_audit import audit_dependencies


def test_audited_poc_dependencies_are_declared_in_ensemble():
    root = Path.cwd()
    policy = json.loads((root / "parity/dependency-parity.json").read_text())
    report = audit_dependencies(root, policy)
    assert report["status"] == "PASS"
    assert report["python"]["missing"] == []
    assert report["node_adapter"]["missing"] == []
    assert "pg" in report["approved_exceptions"]
    assert "psycopg2-binary" in report["approved_exceptions"]
