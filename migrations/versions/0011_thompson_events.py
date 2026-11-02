"""Add idempotency keys for Thompson outcome updates."""

from alembic import op
import sqlalchemy as sa


revision = "0011_thompson_events"
down_revision = "0010_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_thompson_events",
        sa.Column("event_id", sa.String(length=256), primary_key=True),
        sa.Column("model_name", sa.String(length=256), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ensemble_thompson_events")
