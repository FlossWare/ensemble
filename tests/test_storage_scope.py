import pytest

from ensemble.store import SqlDocumentStore


def test_sqlite_is_the_supported_persistence_backend(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'ensemble.db'}"
    store = SqlDocumentStore(database_url)
    assert store.engine.url.get_backend_name() == "sqlite"


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://user:secret@localhost/ensemble",
        "mysql+aiomysql://user:secret@localhost/ensemble",
    ],
)
def test_alternative_persistence_backends_are_rejected(database_url):
    with pytest.raises(ValueError, match="SQLite persistence only"):
        SqlDocumentStore(database_url)
