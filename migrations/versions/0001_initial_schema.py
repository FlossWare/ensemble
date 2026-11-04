"""Create initial Ensemble document and chunk schema."""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_id", sa.String(length=1024), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=2048), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "source_id", "revision",
            name="uq_ensemble_document_source_revision",
        ),
    )
    op.create_index(
        "ix_ensemble_documents_current_source",
        "ensemble_documents",
        ["source_id", "is_current"],
    )
    op.create_table(
        "ensemble_chunks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("ensemble_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "document_id", "ordinal",
            name="uq_ensemble_chunk_document_ordinal",
        ),
    )
    op.create_index(
        "ix_ensemble_chunks_document",
        "ensemble_chunks",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ensemble_chunks_document", table_name="ensemble_chunks")
    op.drop_table("ensemble_chunks")
    op.drop_index("ix_ensemble_documents_current_source", table_name="ensemble_documents")
    op.drop_table("ensemble_documents")
