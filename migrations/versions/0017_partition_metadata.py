"""Record the identity of each physically isolated partition database."""

from alembic import op
import sqlalchemy as sa

revision = "0017_partition_metadata"
down_revision = "0016_generation_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_partition_metadata",
        sa.Column("partition_id", sa.String(length=128), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.current_timestamp()),
    )
    op.execute(
        sa.text("INSERT INTO ensemble_partition_metadata (partition_id) VALUES ('default')")
    )


def downgrade() -> None:
    op.drop_table("ensemble_partition_metadata")
