"""CRUD helpers for Payments-page quick-access links."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.auth import ROLE_GTO
from db.models import Club, PaymentQuickLink

ALLOWED_METHODS = frozenset(
    {"stripe", "venmo", "zelle", "cashapp", "paypal", "crypto", "applepay"}
)


def normalize_method(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    slug = str(raw).strip().lower()
    if slug in ("", "all"):
        return None
    if slug not in ALLOWED_METHODS:
        raise ValueError("Invalid method")
    return slug


def normalize_url(raw: str) -> str:
    url = (raw or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must start with http:// or https://")
    return url


def normalize_title(raw: str) -> str:
    title = (raw or "").strip()
    if not title:
        raise ValueError("Title is required")
    if len(title) > 120:
        raise ValueError("Title is too long")
    return title


def link_visible(
    *,
    link_method: Optional[str],
    link_club_id: Optional[int],
    filter_method: str,
    filter_club_id: Optional[int],
) -> bool:
    """True when a link should show for the current Payments filters.

    Null method/club on the link means "all" (no restriction on that axis).
    """
    method_ok = link_method in (None, "", "all") or link_method == filter_method
    club_ok = link_club_id is None or link_club_id == filter_club_id
    return bool(method_ok and club_ok)


def _to_dict(row: PaymentQuickLink, club_name: Optional[str] = None) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "title": row.title,
        "url": row.url,
        "method": row.method,
        "club_id": int(row.club_id) if row.club_id is not None else None,
        "club_name": club_name,
        "sort_order": int(row.sort_order or 0),
    }


def _club_name(db: Session, club_id: Optional[int]) -> Optional[str]:
    if club_id is None:
        return None
    club = db.get(Club, int(club_id))
    return str(club.name) if club is not None else None


def _resolve_club_id(db: Session, club_id: Optional[int]) -> Optional[int]:
    if club_id is None:
        return None
    club = db.get(Club, int(club_id))
    if club is None:
        raise ValueError("Club not found")
    return int(club.id)


def list_quick_links(
    db: Session,
    *,
    role: str,
    gto_club_id: Optional[int],
) -> list[dict[str, Any]]:
    rows = (
        db.query(PaymentQuickLink)
        .order_by(PaymentQuickLink.sort_order.asc(), PaymentQuickLink.id.asc())
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        club_id = int(row.club_id) if row.club_id is not None else None
        if role == ROLE_GTO:
            if gto_club_id is None:
                if club_id is not None:
                    continue
            elif club_id is not None and club_id != gto_club_id:
                continue
        out.append(_to_dict(row, _club_name(db, club_id)))
    return out


def create_quick_link(
    db: Session,
    *,
    title: str,
    url: str,
    method: Optional[str],
    club_id: Optional[int],
) -> dict[str, Any]:
    title = normalize_title(title)
    url = normalize_url(url)
    method = normalize_method(method)
    club_id = _resolve_club_id(db, club_id)
    max_order = db.query(func.coalesce(func.max(PaymentQuickLink.sort_order), -1)).scalar()
    row = PaymentQuickLink(
        title=title,
        url=url,
        method=method,
        club_id=club_id,
        sort_order=int(max_order) + 1,
    )
    db.add(row)
    db.flush()
    return _to_dict(row, _club_name(db, club_id))


def update_quick_link(
    db: Session,
    link_id: int,
    *,
    title: Optional[str] = None,
    url: Optional[str] = None,
    method: Any = ...,
    club_id: Any = ...,
) -> Optional[dict[str, Any]]:
    row = db.get(PaymentQuickLink, int(link_id))
    if row is None:
        return None
    if title is not None:
        row.title = normalize_title(title)
    if url is not None:
        row.url = normalize_url(url)
    if method is not ...:
        row.method = normalize_method(method)
    if club_id is not ...:
        row.club_id = _resolve_club_id(db, club_id)
    db.flush()
    resolved_club = int(row.club_id) if row.club_id is not None else None
    return _to_dict(row, _club_name(db, resolved_club))


def delete_quick_link(db: Session, link_id: int) -> bool:
    row = db.get(PaymentQuickLink, int(link_id))
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True
