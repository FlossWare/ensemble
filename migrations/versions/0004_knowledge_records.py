"""Add sourced knowledge records."""

from alembic import op
import sqlalchemy as sa


revision = "0004_knowledge_records"
down_revision = "0003_source_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_knowledge_records",
        sa.Column("source_id", sa.String(length=512), primary_key=True),
        sa.Column("revision_sequence", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=2048), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=512), nullable=False),
        sa.Column("locator", sa.String(length=2048), nullable=False),
        sa.Column("profiles", sa.JSON(), nullable=False),
        sa.Column("tombstoned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ensemble_knowledge_records")
