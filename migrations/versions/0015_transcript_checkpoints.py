"""Persist incremental transcript ingestion checkpoints."""

from alembic import op
import sqlalchemy as sa

revision = "0015_transcript_checkpoints"
down_revision = "0014_document_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_transcript_checkpoints",
        sa.Column("source_id", sa.String(length=512), primary_key=True),
        sa.Column("revision_id", sa.String(length=256), primary_key=True),
        sa.Column("project", sa.String(length=256), nullable=False),
        sa.Column("file", sa.String(length=4096), nullable=False),
        sa.Column("byte_offset", sa.Integer(), nullable=False),
        sa.Column("last_start_offset", sa.Integer(), nullable=False),
        sa.Column("last_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("last_result", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ensemble_transcript_checkpoints")
