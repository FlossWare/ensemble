"""Verify that audited POC dependencies remain declared by Ensemble."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
    if not match:
        raise ValueError(f"cannot parse dependency declaration: {requirement!r}")
    return normalize(match.group(1))


def audit_dependencies(root: Path, policy: dict[str, Any]) -> dict[str, Any]:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})
    python_declared = {
        _requirement_name(item)
        for item in project.get("dependencies", [])
    }
    for group in project.get("optional-dependencies", {}).values():
        python_declared.update(_requirement_name(item) for item in group)

    package_path = root / "integrations/claude-code/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    node_declared = {
        normalize(name) for name in package.get("dependencies", {})
    }

    python_required = {normalize(name) for name in policy.get("python_required", [])}
    node_required = {normalize(name) for name in policy.get("node_adapter_required", [])}
    exceptions = {normalize(name): reason for name, reason in policy.get("approved_exceptions", {}).items()}
    missing_python = sorted(python_required - python_declared)
    missing_node = sorted(node_required - node_declared)
    return {
        "schema_version": 1,
        "reference": policy.get("reference", {}),
        "python": {
            "required": sorted(python_required),
            "declared": sorted(python_declared),
            "missing": missing_python,
        },
        "node_adapter": {
            "required": sorted(node_required),
            "declared": sorted(node_declared),
            "missing": missing_node,
        },
        "approved_exceptions": exceptions,
        "status": "FAIL" if missing_python or missing_node else "PASS",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit POC dependency retention.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=Path("parity/dependency-parity.json"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    policy_path = args.policy if args.policy.is_absolute() else args.root / args.policy
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    report = audit_dependencies(args.root, policy)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.report:
        report_path = args.report if args.report.is_absolute() else args.root / args.report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if report["status"] != "PASS" else 0


if __name__ == "__main__":
    sys.exit(main())
