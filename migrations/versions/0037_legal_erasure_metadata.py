"""Persist legal-erasure overrides and requested retention deadlines."""

from alembic import op
import sqlalchemy as sa

revision = "0037_legal_erasure_metadata"
down_revision = "0036_deletion_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ensemble_source_reclamation",
        sa.Column("requested_retain_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ensemble_source_reclamation",
        sa.Column("erasure_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("ensemble_source_reclamation", "erasure_required")
    op.drop_column("ensemble_source_reclamation", "requested_retain_until")
