"""Persist structured source metadata on document records."""

from alembic import op
import sqlalchemy as sa


revision = "0014_document_metadata"
down_revision = "0013_lexical_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ensemble_documents",
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("ensemble_documents", "metadata")
