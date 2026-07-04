"""HTTP client for aon-beta early-rakeback API."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

import httpx


class AonBetaConfigError(Exception):
    """Raised when aon-beta env vars are missing or invalid."""


def aon_beta_base_url() -> Optional[str]:
    raw = os.getenv("AON_BETA_BASE_URL")
    if not raw or not str(raw).strip():
        return None
    return str(raw).strip().rstrip("/")


def aon_beta_api_key() -> Optional[str]:
    raw = os.getenv("AON_BETA_INTERNAL_API_KEY")
    if not raw or not str(raw).strip():
        return None
    return str(raw).strip()


def _require_config() -> tuple[str, str]:
    base = aon_beta_base_url()
    key = aon_beta_api_key()
    if not base or not key:
        raise AonBetaConfigError(
            "AON_BETA_BASE_URL and AON_BETA_INTERNAL_API_KEY must be set"
        )
    return base, key


def fetch_early_rakeback_entries(
    club_slug: str,
    from_utc: datetime,
    to_utc: datetime,
    *,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """GET /{club_slug}/early-rakeback?from=&to= filtered by record timestamps."""
    base, key = _require_config()
    slug = club_slug.strip().lower()
    params = {
        "from": from_utc.isoformat().replace("+00:00", "Z"),
        "to": to_utc.isoformat().replace("+00:00", "Z"),
    }
    headers = {"X-Internal-Api-Key": key}
    with httpx.Client(timeout=timeout) as client:
        res = client.get(
            f"{base}/{slug}/early-rakeback",
            params=params,
            headers=headers,
        )
    res.raise_for_status()
    data = res.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected early-rakeback response type: {type(data)!r}")
    return [row for row in data if isinstance(row, dict)]


def fetch_early_rakeback_archives(
    club_slug: str,
    *,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """GET /{club_slug}/early-rakeback/archives (reset history)."""
    base, key = _require_config()
    slug = club_slug.strip().lower()
    headers = {"X-Internal-Api-Key": key}
    with httpx.Client(timeout=timeout) as client:
        res = client.get(
            f"{base}/{slug}/early-rakeback/archives",
            headers=headers,
        )
    res.raise_for_status()
    data = res.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected early-rakeback archives type: {type(data)!r}")
    return [row for row in data if isinstance(row, dict)]
