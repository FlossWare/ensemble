"""Add isolated document and chunk records for shadow generations."""

from alembic import op
import sqlalchemy as sa


revision = "0021_document_generation_records"
down_revision = "0020_vector_generation_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_document_generation_records",
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=1024), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=2048), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint(
            "generation_id", "document_id",
            name="pk_ensemble_document_generation_records",
        ),
        sa.UniqueConstraint(
            "generation_id", "source_id", "revision",
            name="uq_generation_document_source_revision",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["ensemble_index_generations.generation_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_generation_documents_current_source",
        "ensemble_document_generation_records",
        ["generation_id", "source_id", "is_current"],
    )
    op.create_table(
        "ensemble_chunk_generation_records",
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("profile", sa.String(length=64), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("header", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "generation_id", "chunk_id",
            name="pk_ensemble_chunk_generation_records",
        ),
        sa.UniqueConstraint(
            "generation_id", "document_id", "ordinal",
            name="uq_generation_chunk_document_ordinal",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id", "document_id"],
            ["ensemble_document_generation_records.generation_id",
             "ensemble_document_generation_records.document_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_generation_chunks_document",
        "ensemble_chunk_generation_records",
        ["generation_id", "document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_chunks_document", table_name="ensemble_chunk_generation_records")
    op.drop_table("ensemble_chunk_generation_records")
    op.drop_index("ix_generation_documents_current_source", table_name="ensemble_document_generation_records")
    op.drop_table("ensemble_document_generation_records")
