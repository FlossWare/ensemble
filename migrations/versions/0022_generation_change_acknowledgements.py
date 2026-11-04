"""Track end-to-end acknowledgement of generation journal entries."""

from alembic import op
import sqlalchemy as sa


revision = "0022_generation_change_acknowledgements"
down_revision = "0021_generation_write_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ensemble_index_generation_changes",
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_generation_changes_pending",
        "ensemble_index_generation_changes",
        ["generation_id", "acknowledged", "change_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_changes_pending",
        table_name="ensemble_index_generation_changes",
    )
    op.drop_column("ensemble_index_generation_changes", "acknowledged")
