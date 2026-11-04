"""Persist generation lifecycle, checkpoints, and active pointer."""

from alembic import op
import sqlalchemy as sa

revision = "0016_generation_lifecycle"
down_revision = "0015_transcript_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_generations",
        sa.Column("generation_id", sa.String(length=128), primary_key=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("snapshot_watermark", sa.Integer(), nullable=True),
        sa.Column("applied_watermark", sa.Integer(), nullable=True),
        sa.Column("previous_generation_id", sa.String(length=128), nullable=True),
        sa.Column("failure_reason", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ensemble_generation_checkpoints",
        sa.Column("generation_id", sa.String(length=128), primary_key=True),
        sa.Column("phase", sa.String(length=32), primary_key=True),
        sa.Column("cursor", sa.JSON(), nullable=False),
        sa.Column("sealed", sa.Boolean(), nullable=False),
        sa.Column("watermark", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ensemble_generation_control",
        sa.Column("control_id", sa.Integer(), primary_key=True),
        sa.Column("active_generation_id", sa.String(length=128), nullable=True),
        sa.Column("fenced_generation_id", sa.String(length=128), nullable=True),
        sa.Column("fence_watermark", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ensemble_generation_control")
    op.drop_table("ensemble_generation_checkpoints")
    op.drop_table("ensemble_generations")
