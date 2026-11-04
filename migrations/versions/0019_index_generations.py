"""Add durable index generation and cutover coordination."""

from alembic import op
import sqlalchemy as sa


revision = "0019_index_generations"
down_revision = "0018_transcript_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_index_generations",
        sa.Column("generation_id", sa.String(length=64), primary_key=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("checkpoints", sa.JSON(), nullable=False),
        sa.Column("verification", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "ensemble_index_generation_control",
        sa.Column("singleton_id", sa.Integer(), primary_key=True),
        sa.Column("active_generation_id", sa.String(length=64), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("singleton_id = 1", name="ck_index_generation_control_singleton"),
        sa.ForeignKeyConstraint(
            ["active_generation_id"],
            ["ensemble_index_generations.generation_id"],
        ),
    )
    op.create_index(
        "uq_ensemble_index_one_building_generation",
        "ensemble_index_generations",
        ["state"],
        unique=True,
        sqlite_where=sa.text("state = 'building'"),
    )
    op.execute(sa.text("""
        INSERT INTO ensemble_index_generations
            (generation_id, state, checkpoints, verification, created_at, updated_at, activated_at)
        VALUES
            ('generation-0001', 'active', '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """))
    op.execute(sa.text("""
        INSERT INTO ensemble_index_generation_control
            (singleton_id, active_generation_id, epoch, updated_at)
        VALUES (1, 'generation-0001', 1, CURRENT_TIMESTAMP)
    """))


def downgrade() -> None:
    op.drop_index(
        "uq_ensemble_index_one_building_generation",
        table_name="ensemble_index_generations",
    )
    op.drop_table("ensemble_index_generation_control")
    op.drop_table("ensemble_index_generations")
