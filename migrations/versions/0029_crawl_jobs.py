"""Persist bounded URI crawl jobs and restart recovery."""

from alembic import op
import sqlalchemy as sa

revision = "0029_crawl_jobs"
down_revision = "0028_memory_generation_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_crawl_jobs",
        sa.Column("job_id", sa.String(length=32), primary_key=True),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("max_pages", sa.Integer(), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ensemble_crawl_jobs_status_created", "ensemble_crawl_jobs", ["status", "created_at"])
    op.create_index("ix_ensemble_crawl_jobs_updated", "ensemble_crawl_jobs", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_ensemble_crawl_jobs_updated", table_name="ensemble_crawl_jobs")
    op.drop_index("ix_ensemble_crawl_jobs_status_created", table_name="ensemble_crawl_jobs")
    op.drop_table("ensemble_crawl_jobs")
