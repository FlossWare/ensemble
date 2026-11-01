"""Add native vector search for PostgreSQL."""

import os
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0002_vector_index"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        dimensions = int(os.getenv("ENSEMBLE_EMBEDDING_DIMENSIONS", "0"))
        if dimensions < 1:
            raise RuntimeError(
                "Set ENSEMBLE_EMBEDDING_DIMENSIONS to the embedding model dimension "
                "before applying this PostgreSQL migration."
            )
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.add_column("ensemble_chunks", sa.Column("embedding_vector", Vector(dimensions), nullable=True))
        op.execute(
            "UPDATE ensemble_chunks SET embedding_vector = embedding::text::vector "
            f"WHERE embedding IS NOT NULL AND jsonb_array_length(embedding::jsonb) = {dimensions}"
        )
        op.execute(
            "CREATE INDEX ix_ensemble_chunks_embedding_vector "
            "ON ensemble_chunks USING hnsw (embedding_vector vector_cosine_ops) "
            "WHERE embedding_vector IS NOT NULL"
        )
    else:
        op.add_column("ensemble_chunks", sa.Column("embedding_vector", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("ensemble_chunks", "embedding_vector")
