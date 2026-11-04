from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request


class InternalRestClient:
    """Dispatch capability calls through Ensemble's HTTP contracts.

    The default transport is ASGI-local so the first deployment remains one
    application and one listener. Individual capability base URLs can replace
    that transport when a capability moves to another process or host.
    """

    def __init__(
        self,
        app: FastAPI,
        base_urls: dict[str, str | None] | None = None,
    ) -> None:
        self._app = app
        self._base_urls = {
            name: url.rstrip("/")
            for name, url in (base_urls or {}).items()
            if isinstance(url, str) and url.strip()
        }

    @staticmethod
    def _forwarded_headers(request: Request | None) -> dict[str, str]:
        if request is None:
            return {}
        names = ("authorization", "x-ensemble-partition", "x-correlation-id")
        return {
            name: value
            for name in names
            if (value := request.headers.get(name)) is not None
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        request: Request | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        segments = path.strip("/").split("/")
        capability = segments[2] if len(segments) > 2 and segments[:2] == ["api", "v1"] else ""
        base_url = self._base_urls.get(capability, "http://ensemble")
        remote = capability in self._base_urls
        transport = None if remote else httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=3.0),
        ) as client:
            response = await client.request(
                method,
                path,
                headers=self._forwarded_headers(request),
                json=json,
                params=params,
            )
            if not response.is_success:
                try:
                    payload = response.json()
                    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
                except ValueError:
                    detail = response.text or "internal capability request failed"
                raise HTTPException(status_code=response.status_code, detail=detail)
            if not response.content:
                return None
            return response.json()
