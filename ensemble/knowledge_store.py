from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Index, JSON, MetaData, String, Table,
    Text, UniqueConstraint, and_, or_, select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine


metadata = MetaData()
memories = Table(
    "ensemble_memories", metadata,
    Column("id", String(36), primary_key=True),
    Column("partition_id", String(255), nullable=False),
    Column("memory_key", String(512), nullable=False),
    Column("category", String(128), nullable=False),
    Column("content", Text, nullable=False),
    Column("importance", Float, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("partition_id", "memory_key", name="uq_ensemble_memory_partition_key"),
)
nodes = Table(
    "ensemble_graph_nodes", metadata,
    Column("id", String(36), primary_key=True),
    Column("partition_id", String(255), nullable=False),
    Column("node_type", String(128), nullable=False),
    Column("label", String(512), nullable=False),
    Column("properties", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("partition_id", "node_type", "label", name="uq_ensemble_graph_node_partition_type_label"),
)
edges = Table(
    "ensemble_graph_edges", metadata,
    Column("id", String(36), primary_key=True),
    Column("partition_id", String(255), nullable=False),
    Column("source_node_id", String(36), ForeignKey("ensemble_graph_nodes.id", ondelete="CASCADE"), nullable=False),
    Column("target_node_id", String(36), ForeignKey("ensemble_graph_nodes.id", ondelete="CASCADE"), nullable=False),
    Column("relation", String(128), nullable=False),
    Column("properties", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("partition_id", "source_node_id", "target_node_id", "relation", name="uq_ensemble_graph_edge_partition_relation"),
)
Index("ix_ensemble_memories_partition_category", memories.c.partition_id, memories.c.category)
Index("ix_ensemble_graph_edges_source", edges.c.partition_id, edges.c.source_node_id)
Index("ix_ensemble_graph_edges_target", edges.c.partition_id, edges.c.target_node_id)


class KnowledgeStore:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.backend = engine.url.get_backend_name()

    def _insert(self, table):
        if self.backend == "postgresql":
            return pg_insert(table)
        if self.backend == "sqlite":
            return sqlite_insert(table)
        raise RuntimeError(f"Unsupported database backend: {self.backend}")

    async def upsert_memory(
        self, partition_id: str, memory_key: str, category: str, content: str,
        importance: float, metadata_value: dict, expires_at: datetime | None,
    ) -> dict:
        statement = self._insert(memories).values(
            id=str(uuid4()), partition_id=partition_id, memory_key=memory_key,
            category=category, content=content, importance=importance,
            metadata=metadata_value, created_at=datetime.now(timezone.utc),
            expires_at=expires_at,
        ).on_conflict_do_update(
            index_elements=["partition_id", "memory_key"],
            set_={
                "category": category, "content": content, "importance": importance,
                "metadata": metadata_value, "expires_at": expires_at,
            },
        )
        async with self.engine.begin() as connection:
            await connection.execute(statement)
            row = (await connection.execute(
                select(memories).where(
                    memories.c.partition_id == partition_id,
                    memories.c.memory_key == memory_key,
                )
            )).mappings().one()
        return dict(row)

    async def get_memory(self, partition_id: str, memory_key: str) -> dict | None:
        now = datetime.now(timezone.utc)
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                select(memories).where(
                    memories.c.partition_id == partition_id,
                    memories.c.memory_key == memory_key,
                    or_(memories.c.expires_at.is_(None), memories.c.expires_at > now),
                )
            )).mappings().first()
        return dict(row) if row else None

    async def search_memories(self, partition_id: str, query: str, limit: int) -> list[dict]:
        now = datetime.now(timezone.utc)
        statement = select(memories).where(
            memories.c.partition_id == partition_id,
            or_(memories.c.expires_at.is_(None), memories.c.expires_at > now),
        )
        terms = [term for term in query.split() if term]
        if terms:
            statement = statement.where(or_(*[
                or_(
                    memories.c.content.ilike(f"%{term}%"),
                    memories.c.memory_key.ilike(f"%{term}%"),
                    memories.c.category.ilike(f"%{term}%"),
                ) for term in terms
            ]))
        statement = statement.order_by(memories.c.importance.desc(), memories.c.created_at.desc()).limit(limit)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [dict(row) for row in rows]

    async def upsert_node(
        self, partition_id: str, node_type: str, label: str, properties: dict,
    ) -> dict:
        node_id = uuid5(NAMESPACE_URL, f"ensemble-graph:{partition_id}:{node_type}:{label}")
        statement = self._insert(nodes).values(
            id=str(node_id), partition_id=partition_id, node_type=node_type,
            label=label, properties=properties, created_at=datetime.now(timezone.utc),
        ).on_conflict_do_update(
            index_elements=["partition_id", "node_type", "label"],
            set_={"properties": properties},
        )
        async with self.engine.begin() as connection:
            await connection.execute(statement)
            row = (await connection.execute(
                select(nodes).where(
                    nodes.c.partition_id == partition_id,
                    nodes.c.node_type == node_type,
                    nodes.c.label == label,
                )
            )).mappings().one()
        return dict(row)

    async def upsert_edge(
        self, partition_id: str, source_node_id: str, target_node_id: str,
        relation: str, properties: dict,
    ) -> dict:
        async with self.engine.begin() as connection:
            found = (await connection.execute(
                select(nodes.c.id).where(
                    nodes.c.partition_id == partition_id,
                    nodes.c.id.in_([source_node_id, target_node_id]),
                )
            )).scalars().all()
            if len(set(found)) != 2:
                raise ValueError("both graph edge endpoints must exist in the active partition")
            edge_id = uuid5(
                NAMESPACE_URL,
                f"ensemble-edge:{partition_id}:{source_node_id}:{target_node_id}:{relation}",
            )
            statement = self._insert(edges).values(
                id=str(edge_id), partition_id=partition_id,
                source_node_id=source_node_id, target_node_id=target_node_id,
                relation=relation, properties=properties,
                created_at=datetime.now(timezone.utc),
            ).on_conflict_do_update(
                index_elements=["partition_id", "source_node_id", "target_node_id", "relation"],
                set_={"properties": properties},
            )
            await connection.execute(statement)
            row = (await connection.execute(
                select(edges).where(edges.c.id == str(edge_id))
            )).mappings().one()
        return dict(row)

    async def traverse(self, partition_id: str, node_id: str, depth: int) -> dict:
        async with self.engine.connect() as connection:
            root = (await connection.execute(
                select(nodes).where(
                    nodes.c.partition_id == partition_id,
                    nodes.c.id == node_id,
                )
            )).mappings().first()
            if root is None:
                raise KeyError("graph node not found in the active partition")
            seen = {node_id}
            frontier = {node_id}
            found_edges: dict[str, dict] = {}
            for _ in range(depth):
                if not frontier:
                    break
                rows = (await connection.execute(
                    select(edges).where(
                        edges.c.partition_id == partition_id,
                        or_(
                            edges.c.source_node_id.in_(frontier),
                            edges.c.target_node_id.in_(frontier),
                        ),
                    )
                )).mappings().all()
                next_frontier: set[str] = set()
                for row in rows:
                    found_edges[row["id"]] = dict(row)
                    for candidate in (row["source_node_id"], row["target_node_id"]):
                        if candidate not in seen:
                            seen.add(candidate)
                            next_frontier.add(candidate)
                frontier = next_frontier
            node_rows = (await connection.execute(
                select(nodes).where(
                    nodes.c.partition_id == partition_id,
                    nodes.c.id.in_(seen),
                )
            )).mappings().all()
        return {"nodes": [dict(row) for row in node_rows], "edges": list(found_edges.values())}
