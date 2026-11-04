"""Merge independent generation-write and source-reclamation migration branches."""

revision = "0024_generation_reclamation_merge"
down_revision = (
    "0023_knowledge_generation_records",
    "0022_source_purge_state",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
