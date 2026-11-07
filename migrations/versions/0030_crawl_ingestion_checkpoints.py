"""Persist per-page ingestion checkpoints for completed crawl jobs."""

from alembic import op
import sqlalchemy as sa

revision = "0030_crawl_ingestion_checkpoints"
down_revision = "0029_crawl_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ensemble_crawl_jobs", sa.Column("ingestion", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("ensemble_crawl_jobs", "ingestion")
