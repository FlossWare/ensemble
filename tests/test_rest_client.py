from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException, Request

from ensemble.partitioning import RequestScope, reset_request_scope, set_request_scope
from ensemble.rest_client import InternalRestClient


def test_internal_rest_client_forwards_authenticated_partition_scope():
    app = FastAPI()

    @app.get("/api/v1/graph/probe")
    async def probe(request: Request):
        return {
            "authorization": request.headers.get("authorization"),
            "partition": request.headers.get("x-ensemble-partition"),
            "correlation_id": request.headers.get("x-correlation-id"),
        }

    token = set_request_scope(RequestScope(
        principal_id="integration-test",
        partition_id="tenant-blue",
        authorization="Bearer test-credential",
        correlation_id="trace-123",
    ))
    try:
        result = asyncio.run(InternalRestClient(app).request("GET", "/api/v1/graph/probe"))
    finally:
        reset_request_scope(token)

    assert result == {
        "authorization": "Bearer test-credential",
        "partition": "tenant-blue",
        "correlation_id": "trace-123",
    }


def test_internal_rest_client_preserves_capability_http_error():
    app = FastAPI()

    @app.get("/api/v1/index/probe")
    async def probe():
        raise HTTPException(status_code=409, detail={"code": "revision_conflict"})

    with pytest.raises(HTTPException) as raised:
        asyncio.run(InternalRestClient(app).request("GET", "/api/v1/index/probe"))

    assert raised.value.status_code == 409
    assert raised.value.detail == {"code": "revision_conflict"}


def test_internal_rest_client_returns_none_for_empty_success_response():
    app = FastAPI()

    @app.delete("/api/v1/memory/probe", status_code=204)
    async def probe():
        return None

    result = asyncio.run(InternalRestClient(app).request("DELETE", "/api/v1/memory/probe"))

    assert result is None
