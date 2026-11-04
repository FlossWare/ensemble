"""Add durable graph nodes and edges."""

from alembic import op
import sqlalchemy as sa


revision = "0005_graph"
down_revision = "0004_knowledge_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_graph_nodes",
        sa.Column("id", sa.String(length=256), primary_key=True),
        sa.Column("node_type", sa.String(length=256), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
    )
    op.create_index("ix_ensemble_graph_nodes_type", "ensemble_graph_nodes", ["node_type"])
    op.create_table(
        "ensemble_graph_edges",
        sa.Column("id", sa.String(length=256), primary_key=True),
        sa.Column("source_id", sa.String(length=256), sa.ForeignKey("ensemble_graph_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", sa.String(length=256), sa.ForeignKey("ensemble_graph_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("edge_type", sa.String(length=256), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
    )
    op.create_index("ix_ensemble_graph_edges_source", "ensemble_graph_edges", ["source_id"])
    op.create_index("ix_ensemble_graph_edges_target", "ensemble_graph_edges", ["target_id"])
    op.create_index("ix_ensemble_graph_edges_type", "ensemble_graph_edges", ["edge_type"])


def downgrade() -> None:
    op.drop_index("ix_ensemble_graph_edges_type", table_name="ensemble_graph_edges")
    op.drop_index("ix_ensemble_graph_edges_target", table_name="ensemble_graph_edges")
    op.drop_index("ix_ensemble_graph_edges_source", table_name="ensemble_graph_edges")
    op.drop_table("ensemble_graph_edges")
    op.drop_index("ix_ensemble_graph_nodes_type", table_name="ensemble_graph_nodes")
    op.drop_table("ensemble_graph_nodes")
