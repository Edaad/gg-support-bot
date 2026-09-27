"""Plaid Link, Item status, and encrypted access-token storage.

The dashboard never receives an access token. Transaction sync is not called here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from db.models import PlaidItem

logger = logging.getLogger(__name__)

SLOT_CAP = 10

_PRODUCTION_HOST = "https://production.plaid.com"
_SANDBOX_HOST = "https://sandbox.plaid.com"


class PlaidConfigError(Exception):
    """Missing or unusable Plaid env (client, secret, or token key)."""


class PlaidError(Exception):
    def __init__(self, error_code: str, error_message: str):
        self.error_code = error_code
        self.error_message = error_message
        super().__init__(error_message)


@dataclass(frozen=True)
class PlaidItemView:
    id: int
    institution_name: str
    institution_id: Optional[str]
    status: str
    error_code: Optional[str]
    created_at: datetime


def plaid_host() -> str:
    env = (os.getenv("PLAID_ENV") or "production").strip().lower()
    if env == "sandbox":
        return _SANDBOX_HOST
    if env == "production":
        return _PRODUCTION_HOST
    raise PlaidConfigError("PLAID_ENV must be production or sandbox")


def _credentials() -> tuple[str, str]:
    client_id = (os.getenv("PLAID_CLIENT_ID") or "").strip()
    secret = (os.getenv("PLAID_SECRET") or "").strip()
    if not client_id or not secret:
        raise PlaidConfigError("PLAID_CLIENT_ID and PLAID_SECRET are required")
    return client_id, secret


def _fernet() -> Fernet:
    raw = (os.getenv("PLAID_TOKEN_KEY") or "").strip()
    if not raw:
        raise PlaidConfigError("PLAID_TOKEN_KEY is required")
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError) as exc:
        raise PlaidConfigError("PLAID_TOKEN_KEY is not a valid Fernet key") from exc


def require_plaid_config() -> None:
    """Fail before any Plaid call or token write when env is incomplete."""
    plaid_host()
    _credentials()
    _fernet()


def encrypt_access_token(access_token: str) -> str:
    return _fernet().encrypt(access_token.encode()).decode()


def decrypt_access_token(blob: str) -> str:
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except InvalidToken as exc:
        raise PlaidConfigError("Could not decrypt the stored bank login") from exc


def status_from_item(item: dict) -> tuple[str, Optional[str]]:
    err = item.get("error")
    if not err:
        return "connected", None
    if isinstance(err, dict) and err.get("error_code"):
        return "needs_sign_in", str(err["error_code"])
    return "needs_sign_in", None


def link_token_body(club_id: int, access_token: Optional[str] = None) -> dict:
    body: dict = {
        "client_name": "GG Support",
        "language": "en",
        "country_codes": ["US"],
        "user": {"client_user_id": f"club-{club_id}"},
    }
    if access_token:
        body["access_token"] = access_token
    else:
        body["products"] = ["transactions"]
    return body


def plaid_post(path: str, body: dict) -> dict:
    client_id, secret = _credentials()
    payload = {"client_id": client_id, "secret": secret, **body}
    try:
        response = httpx.post(
            f"{plaid_host()}{path}",
            json=payload,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise PlaidError("UNAVAILABLE", "Plaid could not be reached") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        raise PlaidError(
            str(data.get("error_code") or "PLAID_ERROR"),
            str(data.get("error_message") or "Plaid request failed"),
        )
    if not isinstance(data, dict):
        raise PlaidError("PLAID_ERROR", "Plaid request failed")
    return data


def _view_for_row(row: PlaidItem) -> PlaidItemView:
    try:
        access_token = decrypt_access_token(row.access_token_encrypted)
        data = plaid_post("/item/get", {"access_token": access_token})
        status, error_code = status_from_item(data.get("item") or {})
    except PlaidError as exc:
        status, error_code = "needs_sign_in", exc.error_code
    return PlaidItemView(
        id=row.id,
        institution_name=row.institution_name,
        institution_id=row.institution_id,
        status=status,
        error_code=error_code,
        created_at=row.created_at,
    )


def list_items(db: Session, club_id: int) -> tuple[int, list[PlaidItemView]]:
    rows = (
        db.query(PlaidItem)
        .filter(PlaidItem.club_id == club_id)
        .order_by(PlaidItem.created_at.desc(), PlaidItem.id.desc())
        .all()
    )
    slots_used = db.query(PlaidItem).count()
    return slots_used, [_view_for_row(row) for row in rows]


def create_link_token(
    db: Session, club_id: int, item_row_id: Optional[int] = None
) -> str:
    access_token = None
    if item_row_id is not None:
        row = (
            db.query(PlaidItem)
            .filter(PlaidItem.id == item_row_id, PlaidItem.club_id == club_id)
            .one_or_none()
        )
        if row is None:
            raise LookupError("Bank login not found")
        access_token = decrypt_access_token(row.access_token_encrypted)
    data = plaid_post("/link/token/create", link_token_body(club_id, access_token))
    link_token = data.get("link_token")
    if not link_token:
        raise PlaidError("PLAID_ERROR", "Plaid did not return a link token")
    return str(link_token)


def exchange_public_token(
    db: Session,
    club_id: int,
    public_token: str,
    institution_id: Optional[str],
    institution_name: Optional[str],
) -> PlaidItem:
    data = plaid_post("/item/public_token/exchange", {"public_token": public_token})
    item_id = data.get("item_id")
    access_token = data.get("access_token")
    if not item_id or not access_token:
        raise PlaidError("PLAID_ERROR", "Plaid did not return a bank login")
    name = (institution_name or "").strip() or "Bank"
    inst_id = (institution_id or "").strip() or None
    encrypted = encrypt_access_token(str(access_token))
    existing = (
        db.query(PlaidItem).filter(PlaidItem.item_id == str(item_id)).one_or_none()
    )
    if existing is not None:
        if int(existing.club_id) != int(club_id):
            raise ValueError("This bank login is already saved on another club")
        existing.institution_id = inst_id or existing.institution_id
        existing.institution_name = (
            name if institution_name else existing.institution_name
        )
        existing.access_token_encrypted = encrypted
        db.flush()
        db.refresh(existing)
        logger.info(
            "plaid item updated club_id=%s item_id=%s institution=%s",
            club_id,
            existing.item_id,
            existing.institution_name,
        )
        return existing
    row = PlaidItem(
        club_id=club_id,
        item_id=str(item_id),
        institution_id=inst_id,
        institution_name=name,
        access_token_encrypted=encrypted,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    logger.info(
        "plaid item saved club_id=%s item_id=%s institution=%s",
        club_id,
        row.item_id,
        row.institution_name,
    )
    return row
