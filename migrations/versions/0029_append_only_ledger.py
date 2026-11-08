"""Add the first-class append-only event ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0029_append_only_ledger"
down_revision = "0028_memory_generation_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_ledger_state",
        sa.Column("singleton_id", sa.Integer(), primary_key=True),
        sa.Column("ledger_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("head_sequence", sa.BigInteger(), nullable=False),
        sa.Column("head_hash", sa.String(length=64), nullable=True),
        sa.CheckConstraint("singleton_id = 1", name="ck_ensemble_ledger_singleton"),
        sa.CheckConstraint("head_sequence >= 0", name="ck_ensemble_ledger_head_sequence"),
    )
    op.create_table(
        "ensemble_ledger_entries",
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        sa.Column("event_id", sa.String(length=256), nullable=False, unique=True),
        sa.Column("event_type", sa.String(length=256), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=False),
        sa.Column("timestamp", sa.String(length=64), nullable=True),
        sa.Column("captured_at", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=256), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("previous_entry_sha256", sa.String(length=64), nullable=True),
        sa.Column("entry_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_ensemble_ledger_sequence"),
    )
    op.create_index("ix_ensemble_ledger_event_type_sequence", "ensemble_ledger_entries", ["event_type", "sequence"])
    op.create_index("ix_ensemble_ledger_source_sequence", "ensemble_ledger_entries", ["source", "sequence"])
    op.create_index("ix_ensemble_ledger_correlation_sequence", "ensemble_ledger_entries", ["correlation_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_ensemble_ledger_correlation_sequence", table_name="ensemble_ledger_entries")
    op.drop_index("ix_ensemble_ledger_source_sequence", table_name="ensemble_ledger_entries")
    op.drop_index("ix_ensemble_ledger_event_type_sequence", table_name="ensemble_ledger_entries")
    op.drop_table("ensemble_ledger_entries")
    op.drop_table("ensemble_ledger_state")
