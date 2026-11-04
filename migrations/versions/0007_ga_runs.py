"""Persist GA tuning runs, fitness histories, and explicit promotions."""

from alembic import op
import sqlalchemy as sa


revision = "0007_ga_runs"
down_revision = "0006_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_ga_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ensemble_ga_runs_status_created", "ensemble_ga_runs", ["status", "created_at"])
    op.create_table(
        "ensemble_ga_promotions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("ensemble_ga_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("system", sa.String(length=32), nullable=False),
        sa.Column("candidate", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=2048), nullable=False),
        sa.Column("approved_by", sa.String(length=256), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("run_id", "system", name="uq_ensemble_ga_promotion_run_system"),
    )


def downgrade() -> None:
    op.drop_table("ensemble_ga_promotions")
    op.drop_index("ix_ensemble_ga_runs_status_created", table_name="ensemble_ga_runs")
    op.drop_table("ensemble_ga_runs")
