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
    assert sum(route.path == "/control" for route in app.routes) == 1
    with TestClient(app) as client:
        response = client.get("/control")
        assert response.status_code == 200
        assert "Ensemble Control Center" in response.text
        assert 'class="app-header"' in response.text
        assert 'class="app-shell"' in response.text
        assert 'class="sidebar"' in response.text
        assert 'href="#retrieval-explorer"' in response.text
        assert 'href="#graph-workbench"' in response.text
        assert 'href="#pipeline-inspector"' in response.text
        assert 'id="platform-status"' in response.text
        assert 'id="overview-metrics"' in response.text
        assert 'id="refresh-overview"' in response.text
        assert 'class="metric-grid"' in response.text
        assert "@media(max-width:760px)" in response.text
        assert "prefers-reduced-motion" in response.text
        assert "Genetic Algorithm tuning" in response.text
        assert "Start GA run" in response.text
        assert "Thompson Router" in response.text
        assert "Learning" in response.text
        assert "Runtime Configuration" in response.text
        assert "Reset Learning outcomes" in response.text
        assert "Capability Explorer" in response.text
        assert "Retrieval Explorer" in response.text
        assert "Context Assembly" in response.text
        assert "Index and Vector Inspection" in response.text
        assert "Memory and Knowledge Inspection" in response.text
        assert "Tombstone source" in response.text
        assert "Transcript Session Ingestion" in response.text
        assert "Preview same-origin crawl" in response.text
        assert 'id="crawl-pages"' in response.text
        assert 'id="crawl-depth"' in response.text
        assert "Queue durable crawl" in response.text
        assert 'id="crawl-job-results"' in response.text
        assert "Ingest crawled pages" in response.text
        assert 'id="crawl-ingest"' in response.text
        assert "Schedule automatic refresh" in response.text
        assert "Chunking Workbench" in response.text
        assert "Pipeline Inspector" in response.text
        assert "Persisted execution traces" in response.text
        assert 'id="crawl-schedules"' in response.text
        script = client.get("/control.js")
        assert script.status_code == 200
        assert "/api/v1/graph/ready" in script.text
        assert "/api/v1/graph/stats" in script.text
        assert 'id="graph-stats-refresh"' in response.text
        assert "/api/v1/vector/ready" in script.text
        assert "/api/v1/embeddings/embed" in script.text
        assert "/api/v1/vector/collections/" in script.text
        assert 'id="vector-query-form"' in response.text
        assert "/api/v1/memory/ready" in script.text
        assert script.status_code == 200
        assert "/api/v1/ga/runs" in script.text
        assert "Approve for export" in script.text
        assert "/api/v1/thompson/state" in script.text
        assert "Posterior mean" in script.text
        assert "const posteriorMean=alpha/(alpha+beta)" in script.text
        assert 'textContent="Reset"' in script.text
        assert "/api/v1/search/hybrid" in script.text
        assert "renderRetrievalPipeline" in script.text
        assert "RRF contributions" in script.text
        assert "channel-bars" in script.text
        assert "refreshOverview" in script.text
        assert '/api/v1/index/manifest?limit=500' in script.text
        assert "Sources in first 500 manifest entries" in script.text
        assert "/api/v1/context/assemble" in script.text
        assert "/api/v1/index/manifest" in script.text
        assert "/api/v1/memory/search/hybrid" in script.text
        assert "refreshCapabilityTable" in script.text
        assert "/api/v1/index/tombstones" in script.text
        assert "/api/v1/ingest/sessions" in script.text
        assert "/api/v1/ingest/urls/crawl-preview" in script.text
        assert "/api/v1/execution-traces" in script.text
        assert "renderExecutionTrace" in script.text
        assert "Execution stages (" in script.text
        assert "duration_ms" in script.text


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
        response = client.post("/api/v1/ingest/urls", json={"uri": "https://example.org/guide"})
        assert response.status_code == 201
        result = response.json()
        assert result["document"]["source_id"] == "https://example.org/guide"
        assert result["document"]["chunk_count"] == 1
        trace_id = result["document"]["metadata"]["execution_trace_id"]
        trace = client.get(f"/api/v1/execution-traces/{trace_id}").json()["trace"]
        assert trace["operation"] == "document_ingestion"
        assert trace["status"] in {"completed", "degraded"}
        assert [stage["name"] for stage in trace["stages"]] == [
            "index_revision", "chunking", "embedding", "document_persistence",
            "document_generation_mirror", "vector_indexing_and_generation_mirror",
            "document_materialization", "graph_entity_linking",
            "generation_acknowledgement",
        ]
        index = asyncio.run(index_store.get_source("https://example.org/guide"))
        knowledge = asyncio.run(knowledge_store.get("https://example.org/guide"))
        assert index["high_sequence"] == 1
        assert knowledge["origin"] == "web"
        assert knowledge["content"] == "A useful guide about Ensemble."
