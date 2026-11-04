"""Persist standalone Thompson sampling model statistics."""

from alembic import op
import sqlalchemy as sa


revision = "0008_thompson"
down_revision = "0007_ga_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_thompson_models",
        sa.Column("model_name", sa.String(length=256), primary_key=True),
        sa.Column("successes", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("total_cost", sa.Float(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ensemble_thompson_models")
