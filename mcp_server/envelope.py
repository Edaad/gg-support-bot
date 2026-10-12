"""Uniform response shapes so the model can rely on one structure everywhere."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mcp_server.dates import utc_now_iso
from mcp_server.redact import redact
from mcp_server.serialize import to_jsonable


def page(
    items: Iterable[Any],
    *,
    total: int | None,
    limit: int,
    offset: int,
    include_pii: bool = False,
    notes: Iterable[str] = (),
) -> dict[str, Any]:
    rows = list(items)
    safe = redact(to_jsonable(rows), include_pii=include_pii)
    truncated = bool(total is not None and offset + len(rows) < total)
    if total is None:
        truncated = len(rows) >= limit
    return {
        "items": safe,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(rows),
        "truncated": truncated,
        "pii_masked": not include_pii,
        "notes": list(notes),
        "as_of": utc_now_iso(),
    }


def single(
    item: Any, *, include_pii: bool = False, notes: Iterable[str] = ()
) -> dict[str, Any]:
    return {
        "item": redact(to_jsonable(item), include_pii=include_pii),
        "pii_masked": not include_pii,
        "notes": list(notes),
        "as_of": utc_now_iso(),
    }


def dry_run(
    *,
    actor: str,
    current: Any,
    proposed: Any,
    message: str,
    notes: Iterable[str] = (),
) -> dict[str, Any]:
    """Returned when a write tool is called without ``confirm=true``."""
    return {
        "ok": False,
        "applied": False,
        "refused": True,
        "reason": "confirm=false; call again with confirm=true to apply",
        "actor": actor,
        "current": redact(to_jsonable(current), include_pii=False),
        "proposed": redact(to_jsonable(proposed), include_pii=False),
        "message": message,
        "notes": list(notes),
        "as_of": utc_now_iso(),
    }


def write_result(
    *,
    actor: str,
    before: Any,
    after: Any,
    message: str,
    notes: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "ok": True,
        "applied": True,
        "refused": False,
        "actor": actor,
        "before": redact(to_jsonable(before), include_pii=False),
        "after": redact(to_jsonable(after), include_pii=False),
        "message": message,
        "notes": list(notes),
        "as_of": utc_now_iso(),
    }
