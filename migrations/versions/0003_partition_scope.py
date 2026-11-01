"""Add partition isolation to documents and source revision identity."""

from alembic import op
import sqlalchemy as sa

revision = "0003_partition_scope"
down_revision = "0002_vector_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ensemble_documents") as batch:
        batch.add_column(
            sa.Column("partition_id", sa.String(length=255), nullable=False, server_default="default")
        )
        batch.drop_constraint("uq_ensemble_document_source_revision", type_="unique")
        batch.create_unique_constraint(
            "uq_ensemble_document_partition_source_revision",
            ["partition_id", "source_id", "revision"],
        )
        batch.alter_column("partition_id", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("ensemble_documents") as batch:
        batch.drop_constraint("uq_ensemble_document_partition_source_revision", type_="unique")
        batch.create_unique_constraint(
            "uq_ensemble_document_source_revision", ["source_id", "revision"]
        )
        batch.drop_column("partition_id")
