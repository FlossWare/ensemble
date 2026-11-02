"""Persist Learning outcomes and portable artifacts."""

from alembic import op
import sqlalchemy as sa


revision = "0010_learning"
down_revision = "0009_memory_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_learning_outcomes",
        sa.Column("task_id", sa.String(length=256), primary_key=True),
        sa.Column("task_type", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("memory_recorded", sa.Boolean(), nullable=False),
        sa.Column("thompson_recorded", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ensemble_learning_outcomes_timestamp", "ensemble_learning_outcomes", ["timestamp"])
    op.create_table(
        "ensemble_learning_artifacts",
        sa.Column("artifact_key", sa.String(length=512), primary_key=True),
        sa.Column("artifact_type", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=False),
        sa.Column("schema", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=128), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ensemble_learning_artifacts")
    op.drop_index("ix_ensemble_learning_outcomes_timestamp", table_name="ensemble_learning_outcomes")
    op.drop_table("ensemble_learning_outcomes")
