"""Add recurring source refresh schedules and automatic crawl ingestion."""

from alembic import op
import sqlalchemy as sa

revision = "0031_source_schedules"
down_revision = "0030_crawl_ingestion_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ensemble_crawl_jobs",
        sa.Column("auto_ingest", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("ensemble_crawl_jobs", sa.Column("schedule_id", sa.String(length=32), nullable=True))
    op.create_table(
        "ensemble_source_schedules",
        sa.Column("schedule_id", sa.String(length=32), primary_key=True),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("max_pages", sa.Integer(), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_job_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ensemble_source_schedules_due",
        "ensemble_source_schedules", ["enabled", "next_run_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ensemble_source_schedules_due", table_name="ensemble_source_schedules")
    op.drop_table("ensemble_source_schedules")
    op.drop_column("ensemble_crawl_jobs", "schedule_id")
    op.drop_column("ensemble_crawl_jobs", "auto_ingest")
