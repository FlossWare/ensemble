"""Add append-only physical deletion audit history."""

from alembic import op
import sqlalchemy as sa

revision = "0036_deletion_audit"
down_revision = "0035_collaboration_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_deletion_audit",
        sa.Column("audit_id", sa.String(length=36), primary_key=True),
        sa.Column("source_id", sa.String(length=1024), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=256), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ensemble_deletion_audit_source_time",
        "ensemble_deletion_audit", ["source_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ensemble_deletion_audit_source_time", table_name="ensemble_deletion_audit")
    op.drop_table("ensemble_deletion_audit")
