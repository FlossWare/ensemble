"""Add a durable queue for deferred source reclamation."""

from alembic import op
import sqlalchemy as sa


revision = "0021_deferred_source_reclamation"
down_revision = "0020_index_generation_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_source_reclamation",
        sa.Column("source_id", sa.String(length=1024), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("source_id", name="pk_ensemble_source_reclamation"),
    )
    op.create_index(
        "ix_source_reclamation_due",
        "ensemble_source_reclamation",
        ["state", "available_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_reclamation_due", table_name="ensemble_source_reclamation")
    op.drop_table("ensemble_source_reclamation")
