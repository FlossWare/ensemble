"""Merge independent generation-write and source-reclamation migration branches."""

revision = "0027_generation_reclamation_merge"
down_revision = (
    "0026_knowledge_generation_records",
    "0025_source_purge_state",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
