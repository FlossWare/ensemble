"""Persist Index source state per shadow generation."""

from alembic import op
import sqlalchemy as sa


revision = "0023_index_generation_sources"
down_revision = "0022_generation_source_tombstones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_index_generation_sources",
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=512), nullable=False),
        sa.Column("high_sequence", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.String(length=512), nullable=False),
        sa.Column("profiles", sa.JSON(), nullable=False),
        sa.Column("tombstoned", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "generation_id", "source_id",
            name="pk_ensemble_index_generation_sources",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ensemble_index_generations.generation_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_generation_index_sources_key",
        "ensemble_index_generation_sources",
        ["generation_id", "source_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_index_sources_key",
        table_name="ensemble_index_generation_sources",
    )
    op.drop_table("ensemble_index_generation_sources")
