"""Add durable source revision manifest."""

from alembic import op
import sqlalchemy as sa


revision = "0003_source_index"
down_revision = "0002_vector_collections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_index_sources",
        sa.Column("source_id", sa.String(length=512), primary_key=True),
        sa.Column("high_sequence", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.String(length=512), nullable=False),
        sa.Column("profiles", sa.JSON(), nullable=False),
        sa.Column("tombstoned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ensemble_index_sources")
