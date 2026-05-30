"""Agent debug session logging (stderr for Heroku + optional local NDJSON file)."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

_SESSION_ID = "3fb332"
_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    ".cursor",
    f"debug-{_SESSION_ID}.log",
)
_logger = logging.getLogger("agent.debug")


def agent_debug_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    run_id: str = "run1",
) -> None:
    payload = {
        "sessionId": _SESSION_ID,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
        "runId": run_id,
    }
    _logger.warning("agent-dbg %s", json.dumps(payload, default=str))
    try:
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass
