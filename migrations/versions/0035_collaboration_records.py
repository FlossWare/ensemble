"""Add durable collaboration records for participants, contributions, handoffs, and decisions."""

from alembic import op
import sqlalchemy as sa

revision = "0035_collaboration_records"
down_revision = "0034_deletion_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_collaboration_records",
        sa.Column("record_id", sa.String(length=256), primary_key=True),
        sa.Column("record_type", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=True),
        sa.Column("actor_id", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("identity_key", sa.String(length=512), nullable=True, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ensemble_collaboration_project_activity",
        "ensemble_collaboration_records",
        ["project_id", "created_at", "record_type"],
    )
    op.create_index(
        "ix_ensemble_collaboration_actor",
        "ensemble_collaboration_records",
        ["actor_id", "created_at"],
    )
    op.create_index(
        "ix_ensemble_collaboration_status",
        "ensemble_collaboration_records",
        ["project_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ensemble_collaboration_status", table_name="ensemble_collaboration_records")
    op.drop_index("ix_ensemble_collaboration_actor", table_name="ensemble_collaboration_records")
    op.drop_index("ix_ensemble_collaboration_project_activity", table_name="ensemble_collaboration_records")
    op.drop_table("ensemble_collaboration_records")
