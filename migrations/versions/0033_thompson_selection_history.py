"""Persist bounded Thompson model selection history."""

from alembic import op
import sqlalchemy as sa

revision = "0033_thompson_selection_history"
down_revision = "0032_execution_traces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_thompson_selections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("selection_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("selected_model", sa.String(length=256), nullable=False),
        sa.Column("task_type", sa.String(length=128), nullable=False),
        sa.Column("required_capability", sa.Float(), nullable=False),
        sa.Column("max_cost", sa.Float(), nullable=True),
        sa.Column("request_id", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ensemble_thompson_selections_created",
        "ensemble_thompson_selections", ["created_at"],
    )
    op.create_index(
        "ix_ensemble_thompson_selections_model",
        "ensemble_thompson_selections", ["selected_model", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ensemble_thompson_selections_model", table_name="ensemble_thompson_selections")
    op.drop_index("ix_ensemble_thompson_selections_created", table_name="ensemble_thompson_selections")
    op.drop_table("ensemble_thompson_selections")
