"""Persist source tombstones within shadow document generations."""

from alembic import op
import sqlalchemy as sa


revision = "0019_generation_source_tombstones"
down_revision = "0018_document_generation_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_generation_source_tombstones",
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=1024), nullable=False),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "generation_id", "source_id",
            name="pk_ensemble_generation_source_tombstones",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ensemble_index_generations.generation_id"],
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("ensemble_generation_source_tombstones")
