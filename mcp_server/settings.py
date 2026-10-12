"""Environment configuration for the MCP server."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_STATEMENT_TIMEOUT_MS = 15_000
MIN_TOKEN_LENGTH = 24

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TRUE = {"1", "true", "yes", "on"}


def env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in _TRUE


def parse_tokens(raw: str | None) -> dict[str, str]:
    """Parse ``MCP_API_TOKENS`` (``name:token,name2:token2``) into name -> token.

    Names identify the actor in ``mcp_tool_calls``; tokens are the bearer secret.
    Raises ``ValueError`` on malformed entries or duplicate names.
    """
    tokens: dict[str, str] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, token = entry.partition(":")
        name = name.strip()
        token = token.strip()
        if not sep or not name or not token:
            raise ValueError(
                "MCP_API_TOKENS entries must look like name:token (comma separated)"
            )
        if not _NAME_RE.match(name):
            raise ValueError(f"MCP_API_TOKENS: invalid actor name {name!r}")
        if name in tokens:
            raise ValueError(f"MCP_API_TOKENS: duplicate actor name {name!r}")
        if len(token) < MIN_TOKEN_LENGTH:
            logger.warning(
                "MCP_API_TOKENS: token for %r is shorter than %d characters",
                name,
                MIN_TOKEN_LENGTH,
            )
        tokens[name] = token
    return tokens


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    tokens: dict[str, str] = field(default_factory=dict)
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS
    actor: str = "stdio"


def load_settings() -> Settings:
    timeout_raw = (os.getenv("MCP_STATEMENT_TIMEOUT_MS") or "").strip()
    try:
        timeout_ms = int(timeout_raw) if timeout_raw else DEFAULT_STATEMENT_TIMEOUT_MS
    except ValueError:
        logger.warning("MCP_STATEMENT_TIMEOUT_MS=%r is not an integer", timeout_raw)
        timeout_ms = DEFAULT_STATEMENT_TIMEOUT_MS
    return Settings(
        enabled=env_bool("MCP_ENABLED", False),
        tokens=parse_tokens(os.getenv("MCP_API_TOKENS")),
        statement_timeout_ms=max(1_000, timeout_ms),
        actor=(os.getenv("MCP_ACTOR") or "stdio").strip() or "stdio",
    )
