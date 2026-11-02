"""Add durable vector collections and records."""

from alembic import op
import sqlalchemy as sa


revision = "0002_vector_collections"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_vector_collections",
        sa.Column("name", sa.String(length=128), primary_key=True),
        sa.Column("model", sa.String(length=512), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ensemble_vector_records",
        sa.Column("collection_name", sa.String(length=128),
                  sa.ForeignKey("ensemble_vector_collections.name", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("record_id", sa.String(length=512), primary_key=True),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("document", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_ensemble_vector_records_collection",
        "ensemble_vector_records",
        ["collection_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_ensemble_vector_records_collection", table_name="ensemble_vector_records")
    op.drop_table("ensemble_vector_records")
    op.drop_table("ensemble_vector_collections")
