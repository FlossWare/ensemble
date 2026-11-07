import json
from pathlib import Path
import shutil

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


def test_approved_postgresql_exceptions_must_remain_excluded(tmp_path):
    root = Path.cwd()
    shutil.copy(root / "pyproject.toml", tmp_path / "pyproject.toml")
    package_dir = tmp_path / "integrations/claude-code"
    package_dir.mkdir(parents=True)
    package = json.loads((root / "integrations/claude-code/package.json").read_text())
    package["dependencies"]["pg"] = "^8.0.0"
    (package_dir / "package.json").write_text(json.dumps(package))
    policy = json.loads((root / "parity/dependency-parity.json").read_text())
    report = audit_dependencies(tmp_path, policy)
    assert report["status"] == "FAIL"
    assert report["declared_exceptions"] == ["pg"]
