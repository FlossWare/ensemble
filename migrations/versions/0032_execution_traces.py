"""Persist bounded operational execution traces for ingestion and retrieval."""

from alembic import op
import sqlalchemy as sa

revision = "0032_execution_traces"
down_revision = "0031_source_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_execution_traces",
        sa.Column("trace_id", sa.String(length=32), primary_key=True),
        sa.Column("request_id", sa.String(length=256), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("error_category", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_ensemble_execution_traces_started",
        "ensemble_execution_traces", ["started_at"],
    )
    op.create_index(
        "ix_ensemble_execution_traces_operation_status",
        "ensemble_execution_traces", ["operation", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_ensemble_execution_traces_operation_status", table_name="ensemble_execution_traces")
    op.drop_index("ix_ensemble_execution_traces_started", table_name="ensemble_execution_traces")
    op.drop_table("ensemble_execution_traces")
