"""Add durable episodic memory and session-event records."""

from alembic import op
import sqlalchemy as sa


revision = "0006_memory"
down_revision = "0005_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_memories",
        sa.Column("name", sa.String(length=128), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ensemble_memory_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=256), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["name"], ["ensemble_memories.name"], ondelete="CASCADE"),
        sa.UniqueConstraint("name", "event_id", name="uq_ensemble_memory_event"),
    )
    op.create_index(
        "ix_ensemble_memory_entries_name_created",
        "ensemble_memory_entries",
        ["name", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ensemble_memory_entries_name_created", table_name="ensemble_memory_entries")
    op.drop_table("ensemble_memory_entries")
    op.drop_table("ensemble_memories")
