"""Persist deletion attribution and retention deadlines."""

from alembic import op
import sqlalchemy as sa

revision = "0034_deletion_metadata"
down_revision = "0033_thompson_selection_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ensemble_source_reclamation", sa.Column("actor", sa.String(length=256), nullable=True))
    op.add_column("ensemble_source_reclamation", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column("ensemble_source_reclamation", sa.Column("retain_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ensemble_source_reclamation", sa.Column("requested_retain_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ensemble_source_reclamation", sa.Column("erasure_required", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("ensemble_source_reclamation", "erasure_required")
    op.drop_column("ensemble_source_reclamation", "requested_retain_until")
    op.drop_column("ensemble_source_reclamation", "retain_until")
    op.drop_column("ensemble_source_reclamation", "reason")
    op.drop_column("ensemble_source_reclamation", "actor")
