import asyncio

from fastapi.testclient import TestClient

from ensemble import api
from ensemble.api import create_app
from ensemble.index_memory import InMemoryIndexStore
from ensemble.knowledge_store import InMemoryKnowledgeStore
from ensemble.store import InMemoryDocumentStore
from ensemble.vector_memory import InMemoryVectorStore


def test_control_panel_is_served_by_the_api():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        response = client.get("/control")
        assert response.status_code == 200
        assert "Ensemble Control Center" in response.text
        assert "Genetic Algorithm tuning" in response.text
        assert "Start GA run" in response.text
        assert "Thompson Router" in response.text
        assert "Learning" in response.text
        assert "Reset Learning outcomes" in response.text
        script = client.get("/control.js")
        assert script.status_code == 200
        assert "/api/v1/ga/runs" in script.text
        assert "Approve for export" in script.text
        assert "/api/v1/thompson/state" in script.text
        assert 'textContent="Reset"' in script.text


def test_scrape_ingestion_flows_through_document_index_and_knowledge(monkeypatch):
    async def fake_preview(uri):
        assert uri == "https://example.org/guide"
        return {
            "ok": True, "requested_uri": uri, "final_uri": uri,
            "title": "Example Guide", "media_type": "text/markdown",
            "text": "A useful guide about Ensemble.", "characters": 31,
            "links": [], "truncated": False, "preview_only": True,
        }

    monkeypatch.setattr(api, "preview_uri", fake_preview)
    document_store = InMemoryDocumentStore()
    index_store = InMemoryIndexStore()
    knowledge_store = InMemoryKnowledgeStore()
    vector_store = InMemoryVectorStore()
    app = create_app(
        store=document_store,
        index_store=index_store,
        knowledge_store=knowledge_store,
        vector_store=vector_store,
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/scrape/ingest", json={"uri": "https://example.org/guide"})
        assert response.status_code == 201
        result = response.json()
        assert result["document"]["source_id"] == "https://example.org/guide"
        assert result["document"]["chunk_count"] == 1
        index = asyncio.run(index_store.get_source("https://example.org/guide"))
        knowledge = asyncio.run(knowledge_store.get("https://example.org/guide"))
        assert index["high_sequence"] == 1
        assert knowledge["origin"] == "web"
        assert knowledge["content"] == "A useful guide about Ensemble."
