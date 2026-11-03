from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, Request


class InternalRestClient:
    """Dispatch capability calls through Ensemble's HTTP contracts.

    The default transport is ASGI-local so the first deployment remains one
    application and one listener. A configured base URL can replace that
    transport when a capability is moved to another process or host.
    """

    def __init__(self, app: FastAPI, base_url: str | None = None) -> None:
        self._app = app
        self._base_url = base_url.rstrip("/") if base_url else "http://ensemble"
        self._remote = bool(base_url)

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
        headers = self._forwarded_headers(request)
        transport = None if self._remote else httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(
            base_url=self._base_url,
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=3.0),
        ) as client:
            response = await client.request(
                method,
                path,
                headers=headers,
                json=json,
                params=params,
            )
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()
