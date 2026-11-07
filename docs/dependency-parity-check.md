# Automated POC Dependency Retention Check

`parity/dependency-parity.json` is the machine-readable form of the audited direct-dependency baseline in [POC Dependency Parity Audit](poc-dependency-parity.md).

Run from the Ensemble repository root:

```sh
python -m parity.dependency_audit
```

The check parses Ensemble's `pyproject.toml` base and optional dependency groups, then checks the optional Claude Code host-adapter `package.json`. Python package names are normalized according to PEP 503. Node package names are compared case-insensitively after separator normalization.

The policy deliberately distinguishes:
- Python runtime and optional capabilities, including ChromaDB, NumPy, NetworkX, ONNX Runtime, OpenVINO, tokenizers, CrossEncoder, and POC learning libraries.
- Node host-adapter dependencies retained from the POC integration package.
- Explicit approved exceptions: `pg` and `psycopg2-binary`, because PostgreSQL is excluded by the established SQLite-only Ensemble architecture.

This is a direct-declaration retention check, not a transitive lockfile or license audit. It does not claim every host-side package is imported by the standalone Python server. Any new exception or library substitution requires a documented reason and explicit approval.
