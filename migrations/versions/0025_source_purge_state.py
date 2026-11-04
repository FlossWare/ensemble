"""Track permanent source purge separately from reversible deletion."""

from alembic import op
import sqlalchemy as sa


revision = "0025_source_purge_state"
down_revision = "0024_deferred_source_reclamation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ensemble_index_sources",
        sa.Column("purged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("ensemble_index_sources", "purged")
