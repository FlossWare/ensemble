"""Journal source mutations during index generation rebuilds."""

from alembic import op
import sqlalchemy as sa


revision = "0024_generation_write_journal"
down_revision = "0023_index_generation_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_index_generation_changes",
        sa.Column("change_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=512), nullable=False),
        sa.Column("high_sequence", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.String(length=512), nullable=False),
        sa.Column("profiles", sa.JSON(), nullable=False),
        sa.Column("tombstoned", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ensemble_index_generations.generation_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_generation_changes_cursor",
        "ensemble_index_generation_changes",
        ["generation_id", "change_id"],
    )
    op.create_table(
        "ensemble_index_generation_write_fence",
        sa.Column("singleton_id", sa.Integer(), primary_key=True),
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("singleton_id = 1", name="ck_generation_write_fence_singleton"),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ensemble_index_generations.generation_id"],
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("ensemble_index_generation_write_fence")
    op.drop_index("ix_generation_changes_cursor", table_name="ensemble_index_generation_changes")
    op.drop_table("ensemble_index_generation_changes")
