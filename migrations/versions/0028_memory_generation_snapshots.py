"""Add generation snapshots and a durable change journal for Memory."""

from alembic import op
import sqlalchemy as sa


revision = "0028_memory_generation_snapshots"
down_revision = "0027_generation_reclamation_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_memory_generation_records",
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ensemble_index_generations.generation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("generation_id", "name"),
    )
    op.create_table(
        "ensemble_memory_generation_changes",
        sa.Column("change_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ensemble_index_generations.generation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("change_id"),
    )
    op.create_index(
        "ix_memory_generation_changes_pending",
        "ensemble_memory_generation_changes",
        ["generation_id", "acknowledged", "change_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memory_generation_changes_pending",
        table_name="ensemble_memory_generation_changes",
    )
    op.drop_table("ensemble_memory_generation_changes")
    op.drop_table("ensemble_memory_generation_records")
