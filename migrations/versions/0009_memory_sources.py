"""Track imported Claude Code knowledge sources in Memory."""

from alembic import op
import sqlalchemy as sa


revision = "0009_memory_sources"
down_revision = "0008_thompson"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_memory_sources",
        sa.Column("source_path", sa.String(length=4096), primary_key=True),
        sa.Column("document_name", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=4096), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_name"], ["ensemble_memories.name"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_name", name="uq_ensemble_memory_source_document"),
    )
    op.create_index("ix_ensemble_memory_sources_scope_stale", "ensemble_memory_sources", ["scope", "stale"])


def downgrade() -> None:
    op.drop_index("ix_ensemble_memory_sources_scope_stale", table_name="ensemble_memory_sources")
    op.drop_table("ensemble_memory_sources")
