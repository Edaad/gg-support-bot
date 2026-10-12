#!/usr/bin/env python
"""Entry point for the MCP server over stdio (local Claude Code / Claude Desktop).

Production serves the same server over HTTP from the web dyno at ``/mcp``; this
process is for a laptop with ``DATABASE_URL`` set. stdout is the protocol channel,
so all logging goes to stderr.
"""

import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def _configure_logging() -> None:
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run_mcp_stdio() -> None:
    _configure_logging()
    from db.connection import init_engine
    from db.schema_guard import SchemaNotAtHeadError, ensure_schema_at_head
    from mcp_server.server import build_server

    engine = init_engine()
    try:
        ensure_schema_at_head(engine)
    except SchemaNotAtHeadError as exc:
        logging.getLogger(__name__).warning(
            "database schema is not at the Alembic head; some tools may fail: %s", exc
        )
    build_server().run(transport="stdio")


if __name__ == "__main__":
    run_mcp_stdio()
