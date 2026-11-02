"""Add partition-scoped memory and graph persistence."""

from alembic import op
import sqlalchemy as sa

revision = "0004_memory_graph"
down_revision = "0003_partition_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_memories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("partition_id", sa.String(length=255), nullable=False),
        sa.Column("memory_key", sa.String(length=512), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("partition_id", "memory_key", name="uq_ensemble_memory_partition_key"),
    )
    op.create_index("ix_ensemble_memories_partition_category", "ensemble_memories", ["partition_id", "category"])
    op.create_table(
        "ensemble_graph_nodes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("partition_id", sa.String(length=255), nullable=False),
        sa.Column("node_type", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=512), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("partition_id", "node_type", "label", name="uq_ensemble_graph_node_partition_type_label"),
    )
    op.create_table(
        "ensemble_graph_edges",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("partition_id", sa.String(length=255), nullable=False),
        sa.Column("source_node_id", sa.String(length=36), sa.ForeignKey("ensemble_graph_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_node_id", sa.String(length=36), sa.ForeignKey("ensemble_graph_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(length=128), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("partition_id", "source_node_id", "target_node_id", "relation", name="uq_ensemble_graph_edge_partition_relation"),
    )
    op.create_index("ix_ensemble_graph_edges_source", "ensemble_graph_edges", ["partition_id", "source_node_id"])
    op.create_index("ix_ensemble_graph_edges_target", "ensemble_graph_edges", ["partition_id", "target_node_id"])


def downgrade() -> None:
    op.drop_index("ix_ensemble_graph_edges_target", table_name="ensemble_graph_edges")
    op.drop_index("ix_ensemble_graph_edges_source", table_name="ensemble_graph_edges")
    op.drop_table("ensemble_graph_edges")
    op.drop_table("ensemble_graph_nodes")
    op.drop_index("ix_ensemble_memories_partition_category", table_name="ensemble_memories")
    op.drop_table("ensemble_memories")
