from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Protocol

from sqlalchemy import Column, ForeignKey, JSON, MetaData, String, Table, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url

from .partitioning import PartitionedAsyncEngine


graph_metadata = MetaData()
nodes = Table(
    "ensemble_graph_nodes", graph_metadata,
    Column("id", String(256), primary_key=True),
    Column("node_type", String(256), nullable=False),
    Column("properties", JSON, nullable=False),
)
edges = Table(
    "ensemble_graph_edges", graph_metadata,
    Column("id", String(256), primary_key=True),
    Column("source_id", String(256), ForeignKey("ensemble_graph_nodes.id", ondelete="CASCADE"), nullable=False),
    Column("target_id", String(256), ForeignKey("ensemble_graph_nodes.id", ondelete="CASCADE"), nullable=False),
    Column("edge_type", String(256), nullable=False),
    Column("properties", JSON, nullable=False),
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_id(prefix: str, value: Any) -> str:
    return prefix + "_" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class GraphStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def add_node(self, node_type: str, properties: dict[str, Any], node_id: str | None = None) -> dict[str, Any]: ...
    async def add_edge(self, source: str, target: str, edge_type: str, properties: dict[str, Any], edge_id: str | None = None) -> dict[str, Any]: ...
    async def get_node(self, node_id: str) -> dict[str, Any] | None: ...
    async def reclaim_source(self, source_id: str) -> int: ...
    async def stats(self) -> dict[str, Any]: ...
    async def query_nodes(self, node_type: str | None, properties: dict[str, Any]) -> list[dict[str, Any]]: ...
    async def query_edges(self, from_type: str | None = None, to_type: str | None = None, relationship: str | None = None) -> list[dict[str, Any]]: ...
    async def traverse(self, start: str, direction: str = "out", max_depth: int = 1, edge_type: str | None = None) -> list[dict[str, Any]]: ...


class SqliteGraphStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble graph persistence supports SQLite only")
        self.engine = PartitionedAsyncEngine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            existing = await connection.run_sync(lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names())
        if not {"ensemble_graph_nodes", "ensemble_graph_edges"}.issubset(existing):
            raise RuntimeError("graph schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def add_node(self, node_type: str, properties: dict[str, Any], node_id: str | None = None) -> dict[str, Any]:
        if not isinstance(node_type, str) or not node_type or len(node_type) > 256:
            raise ValueError("type must be a non-empty string of at most 256 characters")
        if not isinstance(properties, dict):
            raise ValueError("properties must be an object")
        identity = {"type": node_type, "properties": properties}
        identifier = node_id or _stable_id("node", identity)
        if not isinstance(identifier, str) or not identifier or len(identifier) > 256:
            raise ValueError("id must be a non-empty string of at most 256 characters")
        async with self.engine.begin() as connection:
            existing = (await connection.execute(select(nodes).where(nodes.c.id == identifier))).mappings().first()
            record = {"id": identifier, "type": node_type, "properties": properties}
            if existing:
                if existing["node_type"] != node_type or existing["properties"] != properties:
                    raise ValueError("node id already exists with different content")
                return record
            await connection.execute(nodes.insert().values(id=identifier, node_type=node_type, properties=properties))
        return record

    async def add_edge(self, source: str, target: str, edge_type: str, properties: dict[str, Any], edge_id: str | None = None) -> dict[str, Any]:
        if not all(isinstance(value, str) and value for value in (source, target, edge_type)):
            raise ValueError("source, target and type must be non-empty strings")
        if not isinstance(properties, dict):
            raise ValueError("properties must be an object")
        identity = {"source": source, "target": target, "type": edge_type, "properties": properties}
        identifier = edge_id or _stable_id("edge", identity)
        if len(identifier) > 256:
            raise ValueError("id must be at most 256 characters")
        async with self.engine.begin() as connection:
            found = set((await connection.execute(select(nodes.c.id).where(nodes.c.id.in_({source, target})))).scalars().all())
            if found != {source, target}:
                raise KeyError("source and target nodes must exist")
            existing = (await connection.execute(select(edges).where(edges.c.id == identifier))).mappings().first()
            record = {"id": identifier, "source": source, "target": target, "type": edge_type, "properties": properties}
            if existing:
                if (existing["source_id"], existing["target_id"], existing["edge_type"], existing["properties"]) != (source, target, edge_type, properties):
                    raise ValueError("edge id already exists with different content")
                return record
            await connection.execute(edges.insert().values(id=identifier, source_id=source, target_id=target, edge_type=edge_type, properties=properties))
        return record

    async def reclaim_source(self, source_id: str) -> int:
        """Remove source-owned graph edges and source nodes, retaining shared entities."""
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise ValueError("source_id must contain 1-512 characters")
        source_node_ids = select(nodes.c.id).where(
            nodes.c.node_type == "source",
            func.json_extract(nodes.c.properties, "$.source_id") == source_id,
        )
        async with self.engine.begin() as connection:
            edge_result = await connection.execute(delete(edges).where(or_(
                edges.c.source_id.in_(source_node_ids),
                edges.c.target_id.in_(source_node_ids),
                func.json_extract(edges.c.properties, "$.source_id") == source_id,
            )))
            node_result = await connection.execute(delete(nodes).where(
                nodes.c.id.in_(source_node_ids),
            ))
        return int(edge_result.rowcount or 0) + int(node_result.rowcount or 0)

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(nodes).where(nodes.c.id == node_id))).mappings().first()
        return {"id": row["id"], "type": row["node_type"], "properties": row["properties"]} if row else None

    async def stats(self) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            node_rows = (await connection.execute(
                select(nodes.c.node_type, func.count()).group_by(nodes.c.node_type).order_by(nodes.c.node_type)
            )).all()
            edge_rows = (await connection.execute(
                select(edges.c.edge_type, func.count()).group_by(edges.c.edge_type).order_by(edges.c.edge_type)
            )).all()
        nodes_by_type = {name: int(count) for name, count in node_rows}
        edges_by_relationship = {name: int(count) for name, count in edge_rows}
        return {
            "node_count": sum(nodes_by_type.values()),
            "edge_count": sum(edges_by_relationship.values()),
            "nodes_by_type": nodes_by_type,
            "edges_by_relationship": edges_by_relationship,
        }

    async def query_nodes(self, node_type: str | None, properties: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(properties, dict):
            raise ValueError("properties must be an object")
        async with self.engine.connect() as connection:
            query = select(nodes)
            if node_type is not None:
                query = query.where(nodes.c.node_type == node_type)
            rows = (await connection.execute(query.order_by(nodes.c.id))).mappings().all()
        return [
            {"id": row["id"], "type": row["node_type"], "properties": row["properties"]}
            for row in rows if all(row["properties"].get(key) == value for key, value in properties.items())
        ]

    async def query_edges(self, from_type: str | None = None, to_type: str | None = None, relationship: str | None = None) -> list[dict[str, Any]]:
        source_node, target_node = nodes.alias("source_node"), nodes.alias("target_node")
        query = select(edges, source_node.c.node_type.label("from_type"), target_node.c.node_type.label("to_type")).join(source_node, edges.c.source_id == source_node.c.id).join(target_node, edges.c.target_id == target_node.c.id)
        if from_type is not None:
            query = query.where(source_node.c.node_type == from_type)
        if to_type is not None:
            query = query.where(target_node.c.node_type == to_type)
        if relationship is not None:
            query = query.where(edges.c.edge_type == relationship)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query.order_by(edges.c.id))).mappings().all()
        return [{"id": row["id"], "from_id": row["source_id"], "to_id": row["target_id"], "relationship": row["edge_type"], "properties": row["properties"]} for row in rows]

    async def traverse(self, start: str, direction: str = "out", max_depth: int = 1, edge_type: str | None = None) -> list[dict[str, Any]]:
        if direction not in {"out", "in", "both"}:
            raise ValueError("direction must be out, in, or both")
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 1 <= max_depth <= 100:
            raise ValueError("max_depth must be an integer from 1 to 100")
        if await self.get_node(start) is None:
            raise KeyError("node not found: " + start)
        visited, frontier, result = {start}, {start}, []
        async with self.engine.connect() as connection:
            all_edges = (await connection.execute(select(edges).order_by(edges.c.id))).mappings().all()
        for depth in range(1, max_depth + 1):
            next_frontier = set()
            for edge in all_edges:
                if edge_type is not None and edge["edge_type"] != edge_type:
                    continue
                neighbors = []
                if direction in {"out", "both"} and edge["source_id"] in frontier:
                    neighbors.append(edge["target_id"])
                if direction in {"in", "both"} and edge["target_id"] in frontier:
                    neighbors.append(edge["source_id"])
                for neighbor in neighbors:
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    next_frontier.add(neighbor)
                    node = await self.get_node(neighbor)
                    via = {"id": edge["id"], "source": edge["source_id"], "target": edge["target_id"], "type": edge["edge_type"], "properties": edge["properties"]}
                    result.append({"depth": depth, "node": node, "via": via})
            frontier = next_frontier
            if not frontier:
                break
        return result
