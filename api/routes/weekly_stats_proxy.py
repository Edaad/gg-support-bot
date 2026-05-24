"""Proxy /weekly-stats/* to gg-computer (production equivalent of Vite dev proxy)."""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(tags=["weekly-stats-proxy"])

_HOP_BY_HOP = frozenset(
    {"connection", "content-encoding", "content-length", "keep-alive", "transfer-encoding"}
)


def _gg_computer_base() -> str:
    raw = os.getenv("GG_COMPUTER_BASE_URL") or os.getenv("VITE_WEEKLY_STATS_BASE_URL")
    if not raw or not str(raw).strip():
        raise HTTPException(
            503,
            "GG_COMPUTER_BASE_URL is not configured on the server",
        )
    return str(raw).strip().rstrip("/")


def _upstream_timeout(path: str, method: str) -> httpx.Timeout:
    if method.upper() == "POST" and "sync" in path:
        return httpx.Timeout(300.0, connect=10.0)
    return httpx.Timeout(60.0, connect=10.0)


@router.api_route(
    "/weekly-stats/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_weekly_stats(path: str, request: Request) -> Response:
    base = _gg_computer_base()
    url = f"{base}/{path.lstrip('/')}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers: dict[str, str] = {}
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type

    body = await request.body()
    timeout = _upstream_timeout(path, request.method)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.request(
                request.method,
                url,
                content=body if body else None,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(502, f"gg-computer unreachable: {exc}") from exc

    resp_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _HOP_BY_HOP
    }
    media_type = upstream.headers.get("content-type")
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=media_type,
    )
