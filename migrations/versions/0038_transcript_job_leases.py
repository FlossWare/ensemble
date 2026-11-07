"""Add renewable ownership leases to transcript ingestion jobs."""

from alembic import op
import sqlalchemy as sa

revision = "0038_transcript_job_leases"
down_revision = "0037_legal_erasure_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ensemble_transcript_jobs",
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ensemble_transcript_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ensemble_transcript_jobs_lease_expires_at",
        "ensemble_transcript_jobs",
        ["lease_expires_at"],
    )
    # Give pre-lease workers a bounded grace period to finish after upgrade.
    op.execute(
        "UPDATE ensemble_transcript_jobs "
        "SET lease_expires_at = datetime(updated_at, '+5 minutes') "
        "WHERE status = 'running'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ensemble_transcript_jobs_lease_expires_at",
        table_name="ensemble_transcript_jobs",
    )
    op.drop_column("ensemble_transcript_jobs", "lease_expires_at")
    op.drop_column("ensemble_transcript_jobs", "lease_owner")
