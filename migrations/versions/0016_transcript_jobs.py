"""Persist transcript ingestion job state and restart recovery."""

from alembic import op
import sqlalchemy as sa

revision = "0016_transcript_jobs"
down_revision = "0015_transcript_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_transcript_jobs",
        sa.Column("job_id", sa.String(length=64), primary_key=True),
        sa.Column("source_id", sa.String(length=512), nullable=False),
        sa.Column("revision_id", sa.String(length=256), nullable=False),
        sa.Column("project", sa.String(length=256), nullable=False),
        sa.Column("file", sa.String(length=4096), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ensemble_transcript_jobs_updated_at",
        "ensemble_transcript_jobs",
        ["updated_at"],
    )
    op.create_index(
        "ix_ensemble_transcript_jobs_status",
        "ensemble_transcript_jobs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_ensemble_transcript_jobs_status", table_name="ensemble_transcript_jobs")
    op.drop_index("ix_ensemble_transcript_jobs_updated_at", table_name="ensemble_transcript_jobs")
    op.drop_table("ensemble_transcript_jobs")
