"""Add generation-scoped Knowledge records."""

from alembic import op
import sqlalchemy as sa


revision = "0026_knowledge_generation_records"
down_revision = "0025_generation_change_acknowledgements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_knowledge_generation_records",
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=512), nullable=False),
        sa.Column("revision_sequence", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=2048), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=512), nullable=False),
        sa.Column("locator", sa.String(length=2048), nullable=False),
        sa.Column("profiles", sa.JSON(), nullable=False),
        sa.Column("tombstoned", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ensemble_index_generations.generation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("generation_id", "source_id"),
    )
    op.create_index(
        "ix_knowledge_generation_source",
        "ensemble_knowledge_generation_records",
        ["generation_id", "source_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_generation_source",
        table_name="ensemble_knowledge_generation_records",
    )
    op.drop_table("ensemble_knowledge_generation_records")
