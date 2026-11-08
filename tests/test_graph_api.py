import asyncio

from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore
from ensemble.graph_memory import InMemoryGraphStore


def test_graph_rest_add_query_and_traverse():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        left = client.post("/api/v1/graph/nodes", json={"type": "service", "properties": {"name": "memory"}})
        right = client.post("/api/v1/graph/nodes", json={"type": "service", "properties": {"name": "vector"}})
        assert left.status_code == right.status_code == 200
        left_id, right_id = left.json()["node"]["id"], right.json()["node"]["id"]

        edge = client.post("/api/v1/graph/edges", json={
            "source": left_id, "target": right_id, "type": "depends_on", "properties": {"required": True}
        })
        assert edge.status_code == 200
        query = client.post("/api/v1/graph/query", json={"type": "service", "properties": {"name": "memory"}})
        assert [item["id"] for item in query.json()["nodes"]] == [left_id]
        traversal = client.post("/api/v1/graph/traverse", json={"start": left_id, "direction": "out", "max_depth": 2})
        assert traversal.json()["results"][0]["node"]["id"] == right_id
        fetched = client.get("/api/v1/graph/nodes/" + right_id)
        assert fetched.json()["node"]["properties"]["name"] == "vector"


def test_graph_rejects_missing_edge_endpoints_and_unknown_traversal_start():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        edge = client.post("/api/v1/graph/edges", json={
            "source": "missing-a", "target": "missing-b", "type": "relates", "properties": {}
        })
        assert edge.status_code == 404
        traversal = client.post("/api/v1/graph/traverse", json={"start": "missing"})
        assert traversal.status_code == 404


def test_in_memory_graph_source_reclamation_preserves_shared_entities():
    async def scenario():
        graph = InMemoryGraphStore()
        source = await graph.add_node(
            "source", {"source_id": "source-to-purge", "revision": 2}, "source-node",
        )
        entity = await graph.add_node(
            "entity", {"entity_type": "concept", "normalized": "shared"}, "shared-entity",
        )
        await graph.add_edge(
            source["id"], entity["id"], "mentions",
            {"source_id": "source-to-purge", "revision": 2}, "owned-mention",
        )
        reclaimed = await graph.reclaim_source("source-to-purge")
        assert reclaimed == 2
        assert await graph.get_node("source-node") is None
        assert await graph.get_node("shared-entity") is not None
        assert await graph.query_edges() == []

    asyncio.run(scenario())


def test_graph_readiness_checks_persistent_store():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        response = client.get("/api/v1/graph/ready")
        assert response.status_code == 200
        assert response.json()["ready"] is True



def test_graph_stats_reports_node_and_relationship_distributions():
    with TestClient(create_app(store=InMemoryDocumentStore())) as client:
        first = client.post("/api/v1/graph/nodes", json={
            "id": "stats-source-1", "type": "source", "properties": {"source_id": "one"},
        })
        second = client.post("/api/v1/graph/nodes", json={
            "id": "stats-entity-1", "type": "entity", "properties": {"name": "A"},
        })
        third = client.post("/api/v1/graph/nodes", json={
            "id": "stats-entity-2", "type": "entity", "properties": {"name": "B"},
        })
        assert all(response.status_code == 200 for response in (first, second, third))
        edge = client.post("/api/v1/graph/edges", json={
            "source": "stats-source-1", "target": "stats-entity-1",
            "type": "mentions", "properties": {},
        })
        assert edge.status_code == 200

        stats = client.get("/api/v1/graph/stats")
        assert stats.status_code == 200
        assert stats.json()["node_count"] == 3
        assert stats.json()["edge_count"] == 1
        assert stats.json()["nodes_by_type"] == {"entity": 2, "source": 1}
        assert stats.json()["edges_by_relationship"] == {"mentions": 1}
