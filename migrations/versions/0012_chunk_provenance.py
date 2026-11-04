"""Persist versioned chunk profile and provenance metadata."""

from alembic import op
import sqlalchemy as sa


revision = "0012_chunk_provenance"
down_revision = "0011_thompson_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ensemble_chunks", sa.Column("profile", sa.String(length=64), nullable=False, server_default="window-v1"))
    op.add_column("ensemble_chunks", sa.Column("start_offset", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ensemble_chunks", sa.Column("end_offset", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ensemble_chunks", sa.Column("header", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("ensemble_chunks", "header")
    op.drop_column("ensemble_chunks", "end_offset")
    op.drop_column("ensemble_chunks", "start_offset")
    op.drop_column("ensemble_chunks", "profile")
