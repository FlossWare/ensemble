from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_poc_service_runtime_libraries_are_declared_in_ensemble():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    required = project["project"]["dependencies"]
    optional = project["project"]["optional-dependencies"]

    assert any(item.lower().startswith("chromadb") for item in required)
    assert any(item.lower().startswith("networkx") for item in required)

    def declared(extra: str, package: str) -> bool:
        return any(item.lower().startswith(package.lower()) for item in optional[extra])

    assert declared("local-embeddings", "numpy")
    assert declared("local-embeddings", "tokenizers")
    assert declared("local-embeddings", "onnxruntime")
    assert declared("openvino", "openvino")
    assert declared("reranking", "sentence-transformers")

    for package in ("scikit-learn", "pandas", "joblib", "pyyaml", "filelock", "pillow"):
        assert declared("poc-learning", package)


def test_postgresql_clients_are_not_part_of_ensemble_runtime():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = project["project"]["dependencies"] + [
        item
        for group in project["project"]["optional-dependencies"].values()
        for item in group
    ]
    names = {item.split(">")[0].split("=")[0].strip().lower() for item in declared}
    assert "psycopg2-binary" not in names
    assert "asyncpg" not in names
