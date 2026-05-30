"""HTTP client for gg-computer player-details (Mongo nicknames)."""

from __future__ import annotations

import os
import re
from typing import Any, List, Optional, Tuple

import httpx

_GG_RE = re.compile(r"^[0-9]{1,48}-[0-9]{1,48}$")
_BATCH_MAX = 200


def gg_computer_base_url() -> Optional[str]:
    raw = os.getenv("GG_COMPUTER_BASE_URL") or os.getenv("VITE_WEEKLY_STATS_BASE_URL")
    if not raw or not str(raw).strip():
        return None
    return str(raw).strip().rstrip("/")


def _validate_gg_id(gg_id: str) -> str:
    s = (gg_id or "").strip()
    if not _GG_RE.match(s):
        raise ValueError(f"Invalid gg_id format: {gg_id!r}")
    return s


def fetch_player_details(
    gg_id: str,
    *,
    club_slug: Optional[str] = None,
    timeout: float = 30.0,
) -> Optional[dict[str, Any]]:
    """GET /player-details. Returns parsed JSON on 200, None on 404 or if gg-computer unset."""
    base = gg_computer_base_url()
    if not base:
        return None
    gid = _validate_gg_id(gg_id)
    params: dict[str, str] = {"ggId": gid}
    if club_slug:
        params["clubId"] = club_slug.strip().lower()
    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.get(f"{base}/player-details", params=params)
    except httpx.RequestError:
        return None
    if res.status_code == 404:
        return None
    res.raise_for_status()
    data = res.json()
    return data if isinstance(data, dict) else None


def batch_player_details(
    club_slug: str,
    gg_ids: List[str],
    *,
    timeout: float = 120.0,
) -> Tuple[List[dict[str, Any]], List[str]]:
    """POST /player-details/batch. Returns (found rows, missing ids). Empty if gg-computer unset."""
    base = gg_computer_base_url()
    if not base:
        return [], list(gg_ids)
    slug = club_slug.strip().lower()
    ids = [_validate_gg_id(x) for x in gg_ids if (x or "").strip()]
    if not ids:
        return [], []
    found: List[dict[str, Any]] = []
    missing: List[str] = []
    with httpx.Client(timeout=timeout) as client:
        for i in range(0, len(ids), _BATCH_MAX):
            chunk = ids[i : i + _BATCH_MAX]
            res = client.post(
                f"{base}/player-details/batch",
                json={"clubId": slug, "gg_ids": chunk},
            )
            res.raise_for_status()
            body = res.json()
            if not isinstance(body, dict):
                continue
            for row in body.get("found") or []:
                if isinstance(row, dict):
                    found.append(row)
            for mid in body.get("missing") or []:
                if isinstance(mid, str):
                    missing.append(mid.strip())
    return found, missing


def nickname_from_player_details_payload(data: dict[str, Any]) -> Optional[str]:
    """Extract nickname from single-club GET /player-details response."""
    nick = data.get("nickname")
    if isinstance(nick, str) and nick.strip():
        return nick.strip()
    return None
