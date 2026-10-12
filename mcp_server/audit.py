"""Audit middleware: one ``mcp_tool_calls`` row per ``tools/call``.

Runs as an SDK ``ServerMiddleware`` so no tool has to log itself. Inserting is
best-effort: a failure is logged and never changes the tool result.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anyio

from mcp_server.auth import actor_from_headers
from mcp_server.redact import redact
from mcp_server.serialize import to_jsonable

logger = logging.getLogger(__name__)

ARGS_JSON_MAX_BYTES = 8 * 1024
ERROR_MESSAGE_MAX = 500

OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_REFUSED = "refused"


def _headers_from_ctx(ctx: Any) -> Any:
    request = getattr(ctx, "request", None)
    headers = getattr(request, "headers", None) if request is not None else None
    if headers is None:
        transport = getattr(ctx, "transport", None)
        headers = getattr(transport, "headers", None) if transport else None
    return headers


def _transport_kind(headers: Any) -> str:
    return "http" if headers is not None else "stdio"


def redacted_args(arguments: Any) -> dict[str, Any] | None:
    """Arguments as stored: PII masked, secrets dropped, size-capped."""
    if not isinstance(arguments, dict):
        return None
    safe = redact(to_jsonable(arguments), include_pii=False)
    encoded = json.dumps(safe, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > ARGS_JSON_MAX_BYTES:
        return {"_truncated": True, "keys": sorted(str(k) for k in safe)}
    return safe


def _field(result: Any, *names: str) -> Any:
    """Read a field from a wire-form dict (camelCase) or a result model."""
    if isinstance(result, dict):
        for name in names:
            if name in result:
                return result[name]
        return None
    for name in names:
        if hasattr(result, name):
            return getattr(result, name)
    return None


def _text_blocks(result: Any) -> list[str]:
    out: list[str] = []
    for block in _field(result, "content") or []:
        text = (
            block.get("text")
            if isinstance(block, dict)
            else getattr(block, "text", None)
        )
        if isinstance(text, str):
            out.append(text)
    return out


def outcome_for_result(result: Any) -> str:
    """ok / error / refused from a tools/call result (refused = dry run of a write)."""
    if result is None:
        return OUTCOME_OK
    if _field(result, "isError", "is_error"):
        return OUTCOME_ERROR
    structured = _field(result, "structuredContent", "structured_content")
    if isinstance(structured, dict):
        payload = structured.get("result", structured)
        if isinstance(payload, dict) and payload.get("refused") is True:
            return OUTCOME_REFUSED
        return OUTCOME_OK
    for text in _text_blocks(result):
        if '"refused": true' in text:
            return OUTCOME_REFUSED
    return OUTCOME_OK


def _write_row(row: dict[str, Any]) -> None:
    from db.models import McpToolCall
    from mcp_server.db import write_session

    with write_session() as session:
        session.add(McpToolCall(**row))


def record_tool_call(row: dict[str, Any]) -> None:
    try:
        _write_row(row)
    except Exception:  # noqa: BLE001 - audit must never break a tool call
        logger.warning("mcp audit: failed to record %s", row.get("tool"), exc_info=True)


class AuditMiddleware:
    """``ServerMiddleware`` that records every ``tools/call``."""

    def __init__(self, write_tool_names: frozenset[str] | None = None):
        if write_tool_names is None:
            from mcp_server.tools import WRITE_TOOL_NAMES

            write_tool_names = WRITE_TOOL_NAMES
        self.write_tool_names = write_tool_names

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        if getattr(ctx, "method", None) != "tools/call":
            return await call_next(ctx)
        params = getattr(ctx, "params", None) or {}
        name = str(params.get("name") or "?")[:64]
        headers = _headers_from_ctx(ctx)
        actor = actor_from_headers(headers)[:64]
        started = time.perf_counter()
        outcome = OUTCOME_ERROR
        error_message: str | None = None
        result: Any = None
        try:
            result = await call_next(ctx)
            outcome = outcome_for_result(result)
            if outcome == OUTCOME_ERROR:
                error_message = _error_text(result)
            return result
        except Exception as exc:
            error_message = str(exc)[:ERROR_MESSAGE_MAX]
            raise
        finally:
            row = {
                "actor": actor,
                "tool": name,
                "transport": _transport_kind(headers),
                "is_write": name in self.write_tool_names,
                "args_json": redacted_args(params.get("arguments")),
                "outcome": outcome,
                "error_message": error_message,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
            await anyio.to_thread.run_sync(record_tool_call, row)


def _error_text(result: Any) -> str | None:
    for text in _text_blocks(result):
        return text[:ERROR_MESSAGE_MAX]
    return None
