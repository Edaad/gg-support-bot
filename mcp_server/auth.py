"""Static bearer-token auth for the HTTP transport and actor attribution.

Mirrors the ``X-Audit-Token`` check in ``api/routes/response_audit.py``: tokens come
from ``MCP_API_TOKENS`` and are compared with ``hmac.compare_digest``. The SDK's own
``token_verifier`` requires OAuth metadata, which Claude Code clients do not need.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from mcp_server.settings import load_settings

logger = logging.getLogger(__name__)

UNKNOWN_ACTOR = "unknown"


def _headers_to_mapping(headers: Any) -> Mapping[str, str]:
    if headers is None:
        return {}
    if isinstance(headers, Mapping):
        return {str(k).lower(): str(v) for k, v in headers.items()}
    out: dict[str, str] = {}
    for key, value in headers:  # ASGI raw headers: list[tuple[bytes, bytes]]
        k = key.decode("latin-1") if isinstance(key, bytes) else str(key)
        v = value.decode("latin-1") if isinstance(value, bytes) else str(value)
        out[k.lower()] = v
    return out


# Header names Claude's connector dialog offers for a fixed credential. The
# token may arrive as ``Authorization: Bearer <t>``, a bare ``Authorization: <t>``
# (prefix omitted in the dialog), ``X-API-Key: <t>`` or ``X-Auth-Token: <t>``.
CREDENTIAL_HEADERS: tuple[str, ...] = ("authorization", "x-api-key", "x-auth-token")


def extract_bearer(headers: Any) -> str | None:
    """Return the credential carried by the request, whichever header holds it."""
    mapping = _headers_to_mapping(headers)
    raw = (mapping.get("authorization") or "").strip()
    if raw:
        scheme, sep, token = raw.partition(" ")
        if scheme.lower() == "bearer":
            return _clean(token)
        if sep or scheme.lower() == "basic":
            return None  # Basic/other schemes are not accepted
        if raw.lower().startswith("bearer"):
            # "Bearer<token>": the space was dropped when the value was entered.
            return _clean(raw[len("bearer") :])
        return _clean(raw)  # bare token without a scheme
    for name in CREDENTIAL_HEADERS[1:]:
        value = _clean(mapping.get(name))
        if value:
            return value
    return None


def _clean(value: str | None) -> str | None:
    """Strip whitespace and surrounding quotes that get pasted along with tokens."""
    text = (value or "").strip().strip("\"'").strip()
    return text or None


def fingerprint(token: str | None) -> str:
    """Length and short SHA-256 prefix of a credential, for log comparison only."""
    if not token:
        return "len=0"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
    return f"len={len(token)} sha256={digest}"


def describe_headers(headers: Any) -> str:
    """Header names and a credential fingerprint (never values), for diagnostics."""
    mapping = _headers_to_mapping(headers)
    names = sorted(mapping)
    present = [n for n in CREDENTIAL_HEADERS if n in names]
    raw = (mapping.get("authorization") or "").strip()
    scheme = (
        raw.partition(" ")[0].lower() if " " in raw else ("bare" if raw else "none")
    )
    token = extract_bearer(headers)
    return (
        f"credential_headers={present or 'none'} scheme={scheme} "
        f"presented={fingerprint(token)} all={names}"
    )


def actor_for_token(token: str | None, tokens: Mapping[str, str]) -> str | None:
    """Return the actor name whose token matches, comparing every entry."""
    if not token:
        return None
    supplied = token.encode("utf-8")
    matched: str | None = None
    for name, expected in tokens.items():
        if hmac.compare_digest(supplied, expected.encode("utf-8")) and matched is None:
            matched = name
    return matched


def actor_from_headers(headers: Any) -> str:
    """Actor for a tool call: token name over HTTP, ``MCP_ACTOR`` over stdio."""
    settings = load_settings()
    if headers is None:
        return settings.actor
    name = actor_for_token(extract_bearer(headers), settings.tokens)
    return name or UNKNOWN_ACTOR


def actor_from_context(ctx: Any) -> str:
    headers = getattr(ctx, "headers", None) if ctx is not None else None
    return actor_from_headers(headers)


class BearerAuthASGIMiddleware:
    """ASGI wrapper that rejects HTTP requests without a configured bearer token."""

    def __init__(self, app: Any, tokens: Mapping[str, str]):
        self.app = app
        self.tokens = dict(tokens)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not self.tokens:
            await _send_json(
                send, 503, {"error": "MCP_API_TOKENS is not configured on the server"}
            )
            return
        headers = scope.get("headers")
        actor = actor_for_token(extract_bearer(headers), self.tokens)
        if actor is None:
            logger.warning(
                "mcp auth: rejected %s %s from %s; %s",
                scope.get("method"),
                scope.get("path"),
                (scope.get("client") or ("?",))[0],
                describe_headers(headers),
            )
            await _send_json(
                send,
                401,
                {"error": "unauthorized"},
                extra_headers=[(b"www-authenticate", b"Bearer")],
            )
            return
        state = scope.setdefault("state", {})
        state["mcp_actor"] = actor
        await self.app(scope, receive, send)


async def _send_json(
    send,
    status: int,
    body: dict[str, Any],
    *,
    extra_headers: Iterable[tuple[bytes, bytes]] = (),
) -> None:
    payload = json.dumps(body).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode("ascii")),
        *extra_headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})
