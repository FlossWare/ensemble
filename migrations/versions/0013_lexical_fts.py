"""Add the durable FTS5 lexical index for versioned document chunks."""

from alembic import op


revision = "0013_lexical_fts"
down_revision = "0012_chunk_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE VIRTUAL TABLE ensemble_lexical_fts USING fts5(
            chunk_id UNINDEXED,
            document_id UNINDEXED,
            source_id UNINDEXED,
            title UNINDEXED,
            analyzed,
            tokenize = "porter unicode61 remove_diacritics 2 tokenchars '-._:/'"
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE ensemble_lexical_fts")
