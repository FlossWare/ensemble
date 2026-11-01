from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore


def test_graph_rest_add_query_and_traverse():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        left = client.post("/api/v1/graph/add-node", json={"type": "service", "properties": {"name": "memory"}})
        right = client.post("/api/v1/graph/add-node", json={"type": "service", "properties": {"name": "vector"}})
        assert left.status_code == right.status_code == 200
        left_id, right_id = left.json()["node"]["id"], right.json()["node"]["id"]

        edge = client.post("/api/v1/graph/add-edge", json={
            "source": left_id, "target": right_id, "type": "depends_on", "properties": {"required": True}
        })
        assert edge.status_code == 200
        query = client.post("/api/v1/graph/query", json={"type": "service", "properties": {"name": "memory"}})
        assert [item["id"] for item in query.json()["nodes"]] == [left_id]
        traversal = client.post("/api/v1/graph/traverse", json={"start": left_id, "direction": "out", "max_depth": 2})
        assert traversal.json()["results"][0]["node"]["id"] == right_id
        fetched = client.get("/api/v1/graph/node/" + right_id)
        assert fetched.json()["node"]["properties"]["name"] == "vector"


def test_graph_rejects_missing_edge_endpoints_and_unknown_traversal_start():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        edge = client.post("/api/v1/graph/add-edge", json={
            "source": "missing-a", "target": "missing-b", "type": "relates", "properties": {}
        })
        assert edge.status_code == 404
        traversal = client.post("/api/v1/graph/traverse", json={"start": "missing"})
        assert traversal.status_code == 404
