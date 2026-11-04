"""Add isolated vector records for shadow index generations."""

from alembic import op
import sqlalchemy as sa


revision = "0020_vector_generation_records"
down_revision = "0019_index_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_vector_generation_records",
        sa.Column("collection_name", sa.String(length=128), nullable=False),
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=512), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("document", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint(
            "collection_name", "generation_id", "record_id",
            name="pk_ensemble_vector_generation_records",
        ),
        sa.ForeignKeyConstraint(
            ["collection_name"], ["ensemble_vector_collections.name"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ensemble_index_generations.generation_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_vector_generation_collection",
        "ensemble_vector_generation_records",
        ["generation_id", "collection_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vector_generation_collection",
        table_name="ensemble_vector_generation_records",
    )
    op.drop_table("ensemble_vector_generation_records")
