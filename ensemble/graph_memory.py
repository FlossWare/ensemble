from __future__ import annotations

from typing import Any

from .graph_store import _stable_id


class InMemoryGraphStore:
    def __init__(self):
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def add_node(self, node_type, properties, node_id=None):
        if not isinstance(node_type, str) or not node_type or not isinstance(properties, dict):
            raise ValueError("type must be a non-empty string and properties must be an object")
        identifier = node_id or _stable_id("node", {"type": node_type, "properties": properties})
        record = {"id": identifier, "type": node_type, "properties": dict(properties)}
        if identifier in self.nodes and self.nodes[identifier] != record:
            raise ValueError("node id already exists with different content")
        self.nodes[identifier] = record
        return dict(record)

    async def add_edge(self, source, target, edge_type, properties, edge_id=None):
        if source not in self.nodes or target not in self.nodes:
            raise KeyError("source and target nodes must exist")
        if not isinstance(edge_type, str) or not edge_type or not isinstance(properties, dict):
            raise ValueError("type must be a non-empty string and properties must be an object")
        identity = {"source": source, "target": target, "type": edge_type, "properties": properties}
        identifier = edge_id or _stable_id("edge", identity)
        record = {"id": identifier, "source": source, "target": target, "type": edge_type, "properties": dict(properties)}
        if identifier in self.edges and self.edges[identifier] != record:
            raise ValueError("edge id already exists with different content")
        self.edges[identifier] = record
        return dict(record)

    async def get_node(self, node_id):
        record = self.nodes.get(node_id)
        return dict(record) if record else None

    async def query_nodes(self, node_type, properties):
        return [dict(record) for record in sorted(self.nodes.values(), key=lambda item: item["id"])
                if (node_type is None or record["type"] == node_type)
                and all(record["properties"].get(key) == value for key, value in properties.items())]

    async def query_edges(self, from_type=None, to_type=None, relationship=None):
        result = []
        for edge in self.edges.values():
            source, target = self.nodes[edge["source"]], self.nodes[edge["target"]]
            if from_type is not None and source["type"] != from_type:
                continue
            if to_type is not None and target["type"] != to_type:
                continue
            if relationship is not None and edge["type"] != relationship:
                continue
            result.append({"id": edge["id"], "from_id": edge["source"], "to_id": edge["target"],
                           "relationship": edge["type"], "properties": dict(edge["properties"])})
        return sorted(result, key=lambda item: item["id"])

    async def traverse(self, start, direction="out", max_depth=1, edge_type=None):
        if direction not in {"out", "in", "both"}:
            raise ValueError("direction must be out, in, or both")
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 1 <= max_depth <= 100:
            raise ValueError("max_depth must be an integer from 1 to 100")
        if start not in self.nodes:
            raise KeyError("node not found: " + start)
        visited, frontier, results = {start}, {start}, []
        for depth in range(1, max_depth + 1):
            next_frontier = set()
            for edge in self.edges.values():
                if edge_type is not None and edge["type"] != edge_type:
                    continue
                neighbors = []
                if direction in {"out", "both"} and edge["source"] in frontier:
                    neighbors.append(edge["target"])
                if direction in {"in", "both"} and edge["target"] in frontier:
                    neighbors.append(edge["source"])
                for neighbor in neighbors:
                    if neighbor in visited:
                        continue
                    visited.add(neighbor); next_frontier.add(neighbor)
                    results.append({"depth": depth, "node": dict(self.nodes[neighbor]),
                                    "via": {"id": edge["id"], "source": edge["source"], "target": edge["target"],
                                            "type": edge["type"], "properties": dict(edge["properties"])}})
            frontier = next_frontier
            if not frontier:
                break
        return results
