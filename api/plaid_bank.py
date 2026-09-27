"""Plaid Link, Item status, and Zelle transaction sync.

The dashboard never receives an access token.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from db.connection import get_db
from db.models import PlaidItem, PlaidTransaction

logger = logging.getLogger(__name__)

SLOT_CAP = 10
ZELLE_PAGE_SIZE = 50
_SYNC_STALE = timedelta(minutes=15)

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
        body["transactions"] = {"days_requested": 730}
    webhook = plaid_webhook_url()
    if webhook:
        body["webhook"] = webhook
    return body


def plaid_webhook_url() -> Optional[str]:
    base = (os.getenv("DASHBOARD_PUBLIC_URL") or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/api/plaid/webhook"


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


def is_zelle_transaction(txn: dict) -> bool:
    meta = txn.get("payment_meta") if isinstance(txn.get("payment_meta"), dict) else {}
    parts: list[str] = []
    for value in (
        txn.get("name"),
        txn.get("merchant_name"),
        txn.get("original_description"),
        txn.get("payment_channel"),
        meta.get("payment_method"),
        meta.get("payment_processor"),
        meta.get("reason"),
        meta.get("payer"),
        meta.get("payee"),
    ):
        if value:
            parts.append(str(value))
    for party in txn.get("counterparties") or []:
        if isinstance(party, dict):
            if party.get("name"):
                parts.append(str(party["name"]))
            if party.get("type"):
                parts.append(str(party["type"]))
    return "zelle" in " ".join(parts).lower()


def _text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _detail_json(txn: dict) -> str:
    meta = txn.get("payment_meta") if isinstance(txn.get("payment_meta"), dict) else {}
    payload = {
        "payment_meta": meta,
        "counterparties": txn.get("counterparties"),
        "personal_finance_category": txn.get("personal_finance_category"),
        "payment_channel": txn.get("payment_channel"),
        "transaction_code": txn.get("transaction_code"),
    }
    return json.dumps(payload, default=str)


def _apply_txn(row: PlaidTransaction, item_id: int, txn: dict) -> None:
    meta = txn.get("payment_meta") if isinstance(txn.get("payment_meta"), dict) else {}
    raw_date = _text(txn.get("date"))
    row.plaid_item_id = item_id
    row.transaction_id = str(txn["transaction_id"])
    row.account_id = _text(txn.get("account_id"))
    row.amount = Decimal(str(txn.get("amount") or "0")).quantize(Decimal("0.01"))
    row.iso_currency_code = _text(txn.get("iso_currency_code"))
    row.txn_date = date.fromisoformat(raw_date) if raw_date else date.today()
    row.name = _text(txn.get("name"))
    row.merchant_name = _text(txn.get("merchant_name"))
    row.original_description = _text(txn.get("original_description"))
    row.pending = bool(txn.get("pending"))
    row.payment_channel = _text(txn.get("payment_channel"))
    row.payer = _text(meta.get("payer"))
    row.payee = _text(meta.get("payee"))
    row.memo = _text(meta.get("reason"))
    row.payment_method = _text(meta.get("payment_method"))
    row.is_zelle = is_zelle_transaction(txn)
    row.detail_json = _detail_json(txn)


def apply_transaction_updates(
    db: Session,
    item: PlaidItem,
    added: list[dict],
    modified: list[dict],
    removed: list[dict],
    next_cursor: Optional[str],
    detected_at: Optional[datetime] = None,
) -> None:
    had_cursor = bool(item.transactions_cursor)
    stamp = detected_at or datetime.now(timezone.utc)
    for txn in list(added) + list(modified):
        transaction_id = txn.get("transaction_id")
        if not transaction_id:
            continue
        row = (
            db.query(PlaidTransaction)
            .filter(PlaidTransaction.transaction_id == str(transaction_id))
            .one_or_none()
        )
        is_new = row is None
        if is_new:
            row = PlaidTransaction(
                plaid_item_id=item.id, transaction_id=str(transaction_id)
            )
            db.add(row)
        _apply_txn(row, item.id, txn)
        if is_new and had_cursor:
            row.first_seen_at = stamp
            logger.info(
                "plaid transaction first_seen_at=%s item_id=%s institution=%s "
                "transaction_id=%s amount=%s is_zelle=%s",
                stamp.isoformat(),
                item.item_id,
                item.institution_name,
                row.transaction_id,
                row.amount,
                row.is_zelle,
            )
    for removed_txn in removed:
        transaction_id = (
            removed_txn.get("transaction_id") if isinstance(removed_txn, dict) else None
        )
        if not transaction_id:
            continue
        (
            db.query(PlaidTransaction)
            .filter(
                PlaidTransaction.plaid_item_id == item.id,
                PlaidTransaction.transaction_id == str(transaction_id),
            )
            .delete(synchronize_session=False)
        )
    if next_cursor:
        item.transactions_cursor = next_cursor
    db.flush()


def pull_transaction_updates(
    access_token: str, cursor: Optional[str]
) -> tuple[list[dict], list[dict], list[dict], Optional[str]]:
    """Walk /transactions/sync from cursor. Restart once if a page mutates."""
    start = cursor or None
    last_error: Optional[PlaidError] = None
    for attempt in range(2):
        page_cursor = start
        added: list[dict] = []
        modified: list[dict] = []
        removed: list[dict] = []
        next_cursor = start
        try:
            has_more = True
            while has_more:
                body: dict = {
                    "access_token": access_token,
                    "count": 500,
                    "options": {"include_original_description": True},
                }
                if page_cursor:
                    body["cursor"] = page_cursor
                data = plaid_post("/transactions/sync", body)
                added.extend(data.get("added") or [])
                modified.extend(data.get("modified") or [])
                removed.extend(data.get("removed") or [])
                next_cursor = data.get("next_cursor") or next_cursor
                has_more = bool(data.get("has_more"))
                page_cursor = data.get("next_cursor")
                if has_more and not page_cursor:
                    break
            return added, modified, removed, next_cursor
        except PlaidError as exc:
            last_error = exc
            if (
                exc.error_code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
                or attempt == 1
            ):
                raise
    if last_error is not None:
        raise last_error
    return [], [], [], start


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def club_sync_state(db: Session, club_id: int) -> tuple[bool, Optional[str]]:
    now = datetime.now(timezone.utc)
    syncing = False
    error = None
    rows = (
        db.query(PlaidItem.sync_status, PlaidItem.sync_started_at, PlaidItem.sync_error)
        .filter(PlaidItem.club_id == club_id)
        .all()
    )
    for status, started, sync_error in rows:
        started_utc = _as_utc(started)
        if (
            status == "running"
            and started_utc is not None
            and now - started_utc < _SYNC_STALE
        ):
            syncing = True
        elif sync_error and error is None:
            error = str(sync_error)
    return syncing, error


def list_zelle(
    db: Session, club_id: int, offset: int, limit: int
) -> tuple[int, list[tuple[PlaidTransaction, str]]]:
    query = (
        db.query(PlaidTransaction, PlaidItem.institution_name)
        .join(PlaidItem, PlaidTransaction.plaid_item_id == PlaidItem.id)
        .filter(PlaidItem.club_id == club_id, PlaidTransaction.is_zelle.is_(True))
        .order_by(PlaidTransaction.txn_date.desc(), PlaidTransaction.id.desc())
    )
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return total, rows


def _lock_item(db: Session, item_id: int) -> bool:
    now = datetime.now(timezone.utc)
    stale_before = now - _SYNC_STALE
    updated = (
        db.query(PlaidItem)
        .filter(PlaidItem.id == item_id)
        .filter(
            (PlaidItem.sync_status != "running")
            | (PlaidItem.sync_started_at.is_(None))
            | (PlaidItem.sync_started_at < stale_before)
        )
        .update(
            {"sync_status": "running", "sync_started_at": now, "sync_error": None},
            synchronize_session=False,
        )
    )
    db.commit()
    return updated == 1


def _finish_sync(item_id: int, error: Optional[str]) -> None:
    with get_db() as db:
        row = db.query(PlaidItem).filter(PlaidItem.id == item_id).one_or_none()
        if row is None:
            return
        row.sync_status = "idle"
        row.last_synced_at = datetime.now(timezone.utc)
        row.sync_error = error


def sync_item_transactions(
    item_id: int, detected_at: Optional[datetime] = None
) -> None:
    with get_db() as db:
        locked = _lock_item(db, item_id)
    if not locked:
        return
    error: Optional[str] = None
    try:
        with get_db() as db:
            row = db.query(PlaidItem).filter(PlaidItem.id == item_id).one()
            access_token = decrypt_access_token(row.access_token_encrypted)
            cursor = row.transactions_cursor
        added, modified, removed, next_cursor = pull_transaction_updates(
            access_token, cursor
        )
        with get_db() as db:
            row = db.query(PlaidItem).filter(PlaidItem.id == item_id).one()
            apply_transaction_updates(
                db, row, added, modified, removed, next_cursor, detected_at=detected_at
            )
    except PlaidError as exc:
        logger.info(
            "plaid sync failed item_row=%s code=%s",
            item_id,
            exc.error_code,
        )
        error = exc.error_message
    except PlaidConfigError:
        logger.info("plaid sync failed item_row=%s config", item_id)
        error = "Could not read the saved bank login"
    except Exception:
        logger.exception("plaid sync failed item_row=%s", item_id)
        error = "Could not refresh transactions"
    _finish_sync(item_id, error)


def sync_for_webhook(plaid_item_id: str, detected_at: datetime) -> None:
    with get_db() as db:
        row = (
            db.query(PlaidItem).filter(PlaidItem.item_id == plaid_item_id).one_or_none()
        )
        if row is None:
            logger.info("plaid webhook unknown item_id=%s", plaid_item_id)
            return
        item_row_id = row.id
    sync_item_transactions(item_row_id, detected_at=detected_at)


def register_saved_item_webhooks() -> None:
    """Point every saved login at this app. Safe to run more than once."""
    webhook = plaid_webhook_url()
    if not webhook:
        logger.info("plaid webhook skipped: DASHBOARD_PUBLIC_URL unset")
        return
    try:
        require_plaid_config()
    except PlaidConfigError:
        logger.info("plaid webhook skipped: Plaid config missing")
        return
    try:
        with get_db() as db:
            rows = [
                (row.id, decrypt_access_token(row.access_token_encrypted))
                for row in db.query(PlaidItem).all()
            ]
    except Exception:
        logger.exception("plaid webhook register failed to read logins")
        return
    for item_row_id, access_token in rows:
        try:
            plaid_post(
                "/item/webhook/update",
                {"access_token": access_token, "webhook": webhook},
            )
            logger.info("plaid webhook registered item_row=%s", item_row_id)
        except PlaidError as exc:
            logger.info(
                "plaid webhook register failed item_row=%s code=%s",
                item_row_id,
                exc.error_code,
            )
        except PlaidConfigError:
            logger.info("plaid webhook register failed item_row=%s config", item_row_id)


def sync_club_transactions(club_id: int) -> None:
    with get_db() as db:
        ids = [
            row_id
            for (row_id,) in db.query(PlaidItem.id)
            .filter(PlaidItem.club_id == club_id)
            .all()
        ]
    for item_id in ids:
        sync_item_transactions(item_id)
