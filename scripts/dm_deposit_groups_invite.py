"""DM players invite links to their support groups (no direct re-add).

Reads ``gc_active_migrated_invite_targets.csv`` by default when present (from
``migrated_groups_activity_report.py``), else ``gc_deposits_migrated_groups.csv``,
``gc_deposits_by_group.csv``, or ``--from-db``. Resolves each player from
``support_group_chats.player_telegram_user_id`` and sends
``PLAYER_MIGRATION_UPGRADE_INVITE_MESSAGE`` via the club MTProto session.

Progress is tracked in ``backups/dm_deposit_groups_invite_tracker.csv`` (override with
``--tracker-csv``). Re-runs skip chats/players already recorded as ``dm_sent``. Each
successful send is appended immediately with a monotonic ``dm_seq`` and timestamp so
you can see who was DM'd first. Use ``--force`` to ignore the tracker.

Players who could not be DM'd are written to ``--failed-csv-out`` (default:
``backups/dm_deposit_groups_invite_failed_<ts>.csv``) with ``player_username``,
``invite_link``, and ``error`` for manual follow-up.

Dry-run by default; pass ``--apply`` to send DMs. Optionally refresh invite links
with ``--export-invite-links`` (and ``--update-invite-links`` to write Postgres).

Environment: DATABASE_URL, TG_API_ID, TG_API_HASH (same as other MTProto scripts).

Operational: do not run while the Heroku worker holds the same club Telethon
session. Set ``GC_MTPROTO_ENABLED=false`` (or ``GC_DM_GC_LISTENER_ENABLED=false``)
on the worker and restart before running; re-enable after.

Usage:
  python scripts/dm_deposit_groups_invite.py
  python scripts/dm_deposit_groups_invite.py --club-key clubgto --limit 5
  python scripts/dm_deposit_groups_invite.py --apply --club-key clubgto --dm-delay 3
  python scripts/dm_deposit_groups_invite.py --apply --limit 20
  python scripts/dm_deposit_groups_invite.py --apply --club-key round_table --dm-delay 3
  python scripts/dm_deposit_groups_invite.py --input-csv gc_active_migrated_invite_targets.csv --apply
  python scripts/dm_deposit_groups_invite.py --from-db --apply --export-invite-links
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

logger = logging.getLogger("dm_deposit_groups_invite")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from scripts.backfill_support_group_invite_links import (  # noqa: E402
    CLUB_KEYS,
    _configure_logging,
    _gc_display_name,
)
from scripts.readd_migrated_group_members import (  # noqa: E402
    _call_with_flood_retry,
    _export_invite_link,
    _load_player_rows_by_chat,
)


@dataclass(frozen=True)
class DepositGroupTarget:
    telegram_chat_id: int
    gc_title: str
    club_key: str
    club_name: str
    club_id: int | None = None
    gg_player_id: str | None = None


@dataclass
class DmResult:
    telegram_chat_id: int
    gc_title: str
    club_key: str
    player_telegram_user_id: int | None
    player_username: str | None
    invite_link: str | None
    status: str
    error: str | None = None
    dm_seq: int | None = None
    dm_sent_at: str | None = None


@dataclass
class DmSummary:
    apply_mode: bool
    targets: int
    processed: int
    dm_sent: int
    dm_would_send: int
    no_player_id: int
    no_invite_link: int
    skipped_already_dm: int
    skipped_player_already_dm: int
    errors: int
    tracker_path: str
    tracker_dm_sent_total: int
    failed_csv_path: str
    failed_csv_rows: int


FAILED_DM_STATUSES = frozenset(
    {"dm_failed", "no_player_id", "no_invite_link", "export_failed"}
)

FAILED_CSV_FIELDS = [
    "player_username",
    "invite_link",
    "telegram_chat_id",
    "gc_title",
    "club_key",
    "player_telegram_user_id",
    "status",
    "error",
]

TRACKER_FIELDS = [
    "dm_seq",
    "dm_sent_at",
    "telegram_chat_id",
    "player_telegram_user_id",
    "player_username",
    "gc_title",
    "club_key",
    "status",
    "error",
    "invite_link",
]


class DmTracker:
    """Append-only log of who was DM'd, in send order."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._rows: list[dict[str, str]] = []
        self._sent_chat_ids: set[int] = set()
        self._sent_player_ids: set[int] = set()
        self._next_seq = 1
        self._file: TextIO | None = None

    @property
    def dm_sent_total(self) -> int:
        return len(self._rows)

    def load(self) -> None:
        if not self.path.is_file():
            return
        with self.path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self._rows.append(row)
                try:
                    seq = int(row.get("dm_seq") or 0)
                except ValueError:
                    seq = 0
                if seq >= self._next_seq:
                    self._next_seq = seq + 1
                status = (row.get("status") or "").strip()
                if status != "dm_sent":
                    continue
                try:
                    chat_id = int(row["telegram_chat_id"])
                    self._sent_chat_ids.add(chat_id)
                except (KeyError, TypeError, ValueError):
                    pass
                player_raw = (row.get("player_telegram_user_id") or "").strip()
                if player_raw.isdigit():
                    self._sent_player_ids.add(int(player_raw))

    def open_for_append(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.path.is_file() or self.path.stat().st_size == 0
        self._file = self.path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=TRACKER_FIELDS)
        if write_header:
            self._writer.writeheader()
            self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def chat_already_sent(self, chat_id: int) -> bool:
        return int(chat_id) in self._sent_chat_ids

    def player_already_sent(self, player_id: int) -> bool:
        return int(player_id) in self._sent_player_ids

    def record(self, result: DmResult) -> DmResult:
        """Append a tracker row for apply-mode outcomes; return result with dm_seq set."""
        if self._file is None:
            return result
        now = datetime.now(timezone.utc).isoformat()
        seq = self._next_seq
        self._next_seq += 1
        row = {
            "dm_seq": str(seq),
            "dm_sent_at": now,
            "telegram_chat_id": str(result.telegram_chat_id),
            "player_telegram_user_id": str(result.player_telegram_user_id or ""),
            "player_username": result.player_username or "",
            "gc_title": result.gc_title,
            "club_key": result.club_key,
            "status": result.status,
            "error": result.error or "",
            "invite_link": result.invite_link or "",
        }
        self._writer.writerow(row)
        self._file.flush()
        self._rows.append(row)
        if result.status == "dm_sent":
            self._sent_chat_ids.add(int(result.telegram_chat_id))
            if result.player_telegram_user_id is not None:
                self._sent_player_ids.add(int(result.player_telegram_user_id))
        return DmResult(
            telegram_chat_id=result.telegram_chat_id,
            gc_title=result.gc_title,
            club_key=result.club_key,
            player_telegram_user_id=result.player_telegram_user_id,
            player_username=result.player_username,
            invite_link=result.invite_link,
            status=result.status,
            error=result.error,
            dm_seq=seq,
            dm_sent_at=now,
        )


def _default_input_csv() -> Path:
    invite_targets = _REPO_ROOT / "gc_active_migrated_invite_targets.csv"
    if invite_targets.is_file():
        return invite_targets
    migrated = _REPO_ROOT / "gc_deposits_migrated_groups.csv"
    if migrated.is_file():
        return migrated
    return _REPO_ROOT / "gc_deposits_by_group.csv"


def _default_results_csv() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"dm_deposit_groups_invite_{stamp}.csv"


def _default_failed_csv() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"dm_deposit_groups_invite_failed_{stamp}.csv"


def _default_tracker_csv() -> Path:
    return _REPO_ROOT / "backups" / "dm_deposit_groups_invite_tracker.csv"


def _load_targets_from_csv(
    path: Path,
    *,
    club_key_filter: str | None,
    chat_id_filter: int | None,
) -> list[DepositGroupTarget]:
    if not path.is_file():
        raise SystemExit(f"Input CSV not found: {path}")

    targets: list[DepositGroupTarget] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            chat_raw = (row.get("telegram_chat_id") or row.get("current_chat_id") or "").strip()
            try:
                chat_id = int(chat_raw)
            except (TypeError, ValueError):
                continue
            if chat_id_filter is not None and chat_id != int(chat_id_filter):
                continue
            club_key = (row.get("club_key") or "").strip()
            if club_key_filter and club_key != club_key_filter:
                continue
            club_id_raw = (row.get("club_id") or "").strip()
            club_id = int(club_id_raw) if club_id_raw.isdigit() else None
            title = (
                row.get("gc_title")
                or row.get("group_title")
                or row.get("support_group_chat_title")
                or ""
            ).strip()
            targets.append(
                DepositGroupTarget(
                    telegram_chat_id=chat_id,
                    gc_title=title,
                    club_key=club_key,
                    club_name=(row.get("club_name") or "").strip(),
                    club_id=club_id,
                    gg_player_id=(row.get("gg_player_id") or "").strip() or None,
                )
            )
    return targets


def _load_targets_from_db(
    *,
    club_key_filter: str | None,
    chat_id_filter: int | None,
) -> list[DepositGroupTarget]:
    from collections import defaultdict

    from club_gc_settings import CLUB_GC_CONFIG
    from db.connection import get_db, init_engine
    from db.models import (
        CashAppPayment,
        Club,
        CryptoPayment,
        StripeCheckoutSession,
        VenmoPayment,
        ZellePayment,
    )
    from api.payments_helpers import resolve_group_title

    init_engine()
    club_key_by_id = {
        int(cfg.link_club_id): key for key, cfg in CLUB_GC_CONFIG.items()
    }
    with get_db() as session:
        clubs = session.query(Club).all()
        club_name_by_id = {int(c.id): c.name for c in clubs}

        agg: dict[int, dict[str, Any]] = defaultdict(lambda: {"club_ids": set()})
        sources = [
            session.query(
                StripeCheckoutSession.telegram_chat_id,
                StripeCheckoutSession.club_id,
            ).filter(StripeCheckoutSession.status == "complete"),
            session.query(
                VenmoPayment.telegram_chat_id,
                VenmoPayment.club_id,
            ).filter(VenmoPayment.telegram_chat_id.isnot(None), VenmoPayment.is_test.is_(False)),
            session.query(
                CashAppPayment.telegram_chat_id,
                CashAppPayment.club_id,
            ).filter(CashAppPayment.telegram_chat_id.isnot(None), CashAppPayment.is_test.is_(False)),
            session.query(
                ZellePayment.telegram_chat_id,
                ZellePayment.club_id,
            ).filter(ZellePayment.telegram_chat_id.isnot(None), ZellePayment.is_test.is_(False)),
            session.query(
                CryptoPayment.telegram_chat_id,
                CryptoPayment.club_id,
            ).filter(CryptoPayment.telegram_chat_id.isnot(None), CryptoPayment.is_test.is_(False)),
        ]
        for q in sources:
            for chat_id, club_id in q.all():
                if chat_id is None:
                    continue
                cid = int(chat_id)
                if club_id is not None:
                    agg[cid]["club_ids"].add(int(club_id))

        targets: list[DepositGroupTarget] = []
        for chat_id in sorted(agg.keys()):
            if chat_id_filter is not None and chat_id != int(chat_id_filter):
                continue
            title, gg_player_id = resolve_group_title(session, chat_id)
            club_id = sorted(agg[chat_id]["club_ids"])[0] if agg[chat_id]["club_ids"] else None
            club_key = club_key_by_id.get(club_id or -1, "") if club_id is not None else ""
            if club_key_filter and club_key != club_key_filter:
                continue
            targets.append(
                DepositGroupTarget(
                    telegram_chat_id=chat_id,
                    gc_title=title or "",
                    club_key=club_key,
                    club_name=club_name_by_id.get(club_id or -1, "") if club_id else "",
                    club_id=club_id,
                    gg_player_id=gg_player_id,
                )
            )
    return targets


async def _send_player_dm(client, player_user_id: int, text: str) -> tuple[bool, str | None]:
    try:
        player = await _call_with_flood_retry(
            lambda: client.get_entity(int(player_user_id)),
            label="get_entity(player)",
        )
        await _call_with_flood_retry(
            lambda: client.send_message(player, text),
            label="send_message(player)",
        )
        return True, None
    except Exception as e:
        logger.warning("player DM failed user_id=%s: %s", player_user_id, type(e).__name__)
        return False, type(e).__name__


def _precheck_target(
    target: DepositGroupTarget,
    *,
    club_key: str,
    use_tracker: bool,
    tracker: DmTracker,
    player_map: dict[int, tuple[int | None, str | None, str | None]],
) -> DmResult | None:
    """Return a terminal DmResult for skip/no-player cases, else None."""
    title = _gc_display_name(target.gc_title, target.telegram_chat_id)
    player_id, player_username, _club_key = player_map.get(
        target.telegram_chat_id, (None, None, None)
    )

    if use_tracker and tracker.chat_already_sent(target.telegram_chat_id):
        return DmResult(
            telegram_chat_id=target.telegram_chat_id,
            gc_title=title,
            club_key=club_key,
            player_telegram_user_id=player_id,
            player_username=player_username,
            invite_link=None,
            status="skipped_already_dm",
        )

    if player_id is None:
        return DmResult(
            telegram_chat_id=target.telegram_chat_id,
            gc_title=title,
            club_key=club_key,
            player_telegram_user_id=None,
            player_username=player_username,
            invite_link=None,
            status="no_player_id",
        )

    if use_tracker and tracker.player_already_sent(player_id):
        return DmResult(
            telegram_chat_id=target.telegram_chat_id,
            gc_title=title,
            club_key=club_key,
            player_telegram_user_id=player_id,
            player_username=player_username,
            invite_link=None,
            status="skipped_player_already_dm",
        )

    return None


class _ClubClientPool:
    """Lazy Telethon clients per club_key, disconnected on close."""

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}

    async def get(self, cfg):
        from bot.services.mtproto_group_create import get_mtproto_lock, make_client

        key = cfg.club_key
        if key in self._clients:
            return self._clients[key]
        async with get_mtproto_lock(key):
            if key in self._clients:
                return self._clients[key]
            client = make_client(cfg)
            await client.connect()
            self._clients[key] = client
            return client

    async def close_all(self) -> None:
        from bot.services.mtproto_group_create import get_mtproto_lock

        for key, client in list(self._clients.items()):
            try:
                async with get_mtproto_lock(key):
                    await client.disconnect()
            except Exception:
                pass
        self._clients.clear()


def _resolve_club_cfg(target: DepositGroupTarget):
    from club_gc_settings import CLUB_GC_CONFIG, get_club_gc_config_by_link_club_id

    club_key = (target.club_key or "").strip()
    cfg = CLUB_GC_CONFIG.get(club_key)
    if cfg is None and target.club_id is not None:
        cfg = get_club_gc_config_by_link_club_id(int(target.club_id))
    return cfg


def _process_target_dry(
    target: DepositGroupTarget,
    *,
    use_tracker: bool,
    tracker: DmTracker,
    player_map: dict[int, tuple[int | None, str | None, str | None]],
) -> DmResult:
    from bot.services.support_group_chats import fetch_invite_link_for_chat

    cfg = _resolve_club_cfg(target)
    club_key = cfg.club_key if cfg else (target.club_key or "?")
    early = _precheck_target(
        target,
        club_key=club_key,
        use_tracker=use_tracker,
        tracker=tracker,
        player_map=player_map,
    )
    if early is not None:
        return early
    if cfg is None:
        return DmResult(
            telegram_chat_id=target.telegram_chat_id,
            gc_title=_gc_display_name(target.gc_title, target.telegram_chat_id),
            club_key=club_key,
            player_telegram_user_id=player_map[target.telegram_chat_id][0],
            player_username=player_map[target.telegram_chat_id][1],
            invite_link=None,
            status="no_mtproto_config",
        )
    invite_link = fetch_invite_link_for_chat(
        target.telegram_chat_id,
        group_title=target.gc_title or None,
    )
    if not invite_link:
        return DmResult(
            telegram_chat_id=target.telegram_chat_id,
            gc_title=_gc_display_name(target.gc_title, target.telegram_chat_id),
            club_key=club_key,
            player_telegram_user_id=player_map[target.telegram_chat_id][0],
            player_username=player_map[target.telegram_chat_id][1],
            invite_link=None,
            status="no_invite_link",
        )
    return DmResult(
        telegram_chat_id=target.telegram_chat_id,
        gc_title=_gc_display_name(target.gc_title, target.telegram_chat_id),
        club_key=club_key,
        player_telegram_user_id=player_map[target.telegram_chat_id][0],
        player_username=player_map[target.telegram_chat_id][1],
        invite_link=invite_link,
        status="would_dm",
    )


async def _process_target_apply(
    target: DepositGroupTarget,
    *,
    client_pool: _ClubClientPool,
    use_tracker: bool,
    tracker: DmTracker,
    export_invite_links: bool,
    update_invite_links: bool,
    player_map: dict[int, tuple[int | None, str | None, str | None]],
) -> DmResult:
    from bot.services.player_support_dm_messages import PLAYER_MIGRATION_UPGRADE_INVITE_MESSAGE
    from bot.services.support_group_chats import (
        fetch_invite_link_for_chat,
        fetch_support_group_chat_row_for_chat,
        update_support_group_chat_row,
        upsert_support_group_invite_link,
    )

    cfg = _resolve_club_cfg(target)
    club_key = cfg.club_key if cfg else (target.club_key or "?")
    early = _precheck_target(
        target,
        club_key=club_key,
        use_tracker=use_tracker,
        tracker=tracker,
        player_map=player_map,
    )
    if early is not None:
        return early
    if cfg is None:
        return DmResult(
            telegram_chat_id=target.telegram_chat_id,
            gc_title=_gc_display_name(target.gc_title, target.telegram_chat_id),
            club_key=club_key,
            player_telegram_user_id=player_map[target.telegram_chat_id][0],
            player_username=player_map[target.telegram_chat_id][1],
            invite_link=None,
            status="no_mtproto_config",
        )

    title = _gc_display_name(target.gc_title, target.telegram_chat_id)
    player_id = player_map[target.telegram_chat_id][0]
    player_username = player_map[target.telegram_chat_id][1]
    invite_link = fetch_invite_link_for_chat(
        target.telegram_chat_id,
        group_title=target.gc_title or None,
    )

    from bot.services.mtproto_group_create import get_mtproto_lock

    client = await client_pool.get(cfg)
    async with get_mtproto_lock(cfg.club_key):
        if not invite_link or export_invite_links:
            try:
                entity = await _call_with_flood_retry(
                    lambda cid=target.telegram_chat_id: client.get_entity(int(cid)),
                    label="get_entity(group)",
                )
                exported = await _export_invite_link(client, entity)
                if exported:
                    invite_link = exported
                    if update_invite_links:
                        upsert_support_group_invite_link(
                            club_key=cfg.club_key,
                            club_display_name=cfg.club_display_name,
                            telegram_chat_id=int(target.telegram_chat_id),
                            telegram_chat_title=title,
                            invite_link=invite_link,
                            mtproto_session_name=cfg.mtproto_session,
                        )
            except Exception as e:
                return DmResult(
                    telegram_chat_id=target.telegram_chat_id,
                    gc_title=title,
                    club_key=club_key,
                    player_telegram_user_id=player_id,
                    player_username=player_username,
                    invite_link=invite_link,
                    status="export_failed",
                    error=type(e).__name__,
                )

        if not invite_link:
            return DmResult(
                telegram_chat_id=target.telegram_chat_id,
                gc_title=title,
                club_key=club_key,
                player_telegram_user_id=player_id,
                player_username=player_username,
                invite_link=None,
                status="no_invite_link",
            )

        dm_body = PLAYER_MIGRATION_UPGRADE_INVITE_MESSAGE.format(
            invite_link=invite_link.strip()
        )
        dm_ok, dm_err = await _send_player_dm(client, player_id, dm_body)
    result_status = "dm_sent" if dm_ok else "dm_failed"
    dm_status = "migration_invite_dm" + ("_dm_failed" if not dm_ok else "")
    row = fetch_support_group_chat_row_for_chat(
        target.telegram_chat_id,
        group_title=target.gc_title or None,
        club_key=cfg.club_key,
    )
    if row is not None:
        update_support_group_chat_row(
            row.id,
            invite_link=invite_link,
            player_dm_status=dm_status,
            last_error_message=f"player_dm:{dm_err}" if dm_err else "",
        )

    result = DmResult(
        telegram_chat_id=target.telegram_chat_id,
        gc_title=title,
        club_key=club_key,
        player_telegram_user_id=player_id,
        player_username=player_username,
        invite_link=invite_link,
        status=result_status,
        error=dm_err,
    )
    if result_status == "dm_sent":
        return tracker.record(result)
    return result


def _count_toward_send_limit(result: DmResult) -> bool:
    return result.status in ("dm_sent", "dm_failed", "would_dm")


async def _run(
    *,
    targets: list[DepositGroupTarget],
    apply: bool,
    export_invite_links: bool,
    update_invite_links: bool,
    use_tracker: bool,
    tracker: DmTracker,
    dm_delay_seconds: float,
    send_limit: int | None,
) -> tuple[DmSummary, list[DmResult]]:
    from db.connection import init_engine

    init_engine()

    chat_ids = {t.telegram_chat_id for t in targets}
    player_map = _load_player_rows_by_chat(chat_ids)

    results: list[DmResult] = []
    summary = DmSummary(
        apply_mode=apply,
        targets=len(targets),
        processed=0,
        dm_sent=0,
        dm_would_send=0,
        no_player_id=0,
        no_invite_link=0,
        skipped_already_dm=0,
        skipped_player_already_dm=0,
        errors=0,
        tracker_path=str(tracker.path),
        tracker_dm_sent_total=tracker.dm_sent_total,
        failed_csv_path="",
        failed_csv_rows=0,
    )

    if apply:
        tracker.open_for_append()

    client_pool = _ClubClientPool()
    sends_done = 0
    try:
        for i, target in enumerate(targets):
            if send_limit is not None and sends_done >= send_limit:
                break
            if apply and i > 0 and dm_delay_seconds > 0:
                await asyncio.sleep(dm_delay_seconds)

            if apply:
                result = await _process_target_apply(
                    target,
                    client_pool=client_pool,
                    use_tracker=use_tracker,
                    tracker=tracker,
                    export_invite_links=export_invite_links,
                    update_invite_links=update_invite_links,
                    player_map=player_map,
                )
            else:
                result = _process_target_dry(
                    target,
                    use_tracker=use_tracker,
                    tracker=tracker,
                    player_map=player_map,
                )

            results.append(result)
            summary.processed += 1
            if result.status == "dm_sent":
                summary.dm_sent += 1
            elif result.status == "would_dm":
                summary.dm_would_send += 1
            elif result.status == "no_player_id":
                summary.no_player_id += 1
            elif result.status == "no_invite_link":
                summary.no_invite_link += 1
            elif result.status == "skipped_already_dm":
                summary.skipped_already_dm += 1
            elif result.status == "skipped_player_already_dm":
                summary.skipped_player_already_dm += 1
            elif result.status in ("dm_failed", "export_failed", "no_mtproto_config"):
                summary.errors += 1

            if _count_toward_send_limit(result):
                sends_done += 1
    finally:
        if apply:
            tracker.close()
        await client_pool.close_all()

    summary.tracker_dm_sent_total = tracker.dm_sent_total
    return summary, results


def _failed_dm_results(results: list[DmResult]) -> list[DmResult]:
    return [r for r in results if r.status in FAILED_DM_STATUSES]


def _write_failed_dm_csv(path: Path, results: list[DmResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FAILED_CSV_FIELDS)
        writer.writeheader()
        for row in _failed_dm_results(results):
            writer.writerow(
                {
                    "player_username": row.player_username or "",
                    "invite_link": row.invite_link or "",
                    "telegram_chat_id": row.telegram_chat_id,
                    "gc_title": row.gc_title,
                    "club_key": row.club_key,
                    "player_telegram_user_id": row.player_telegram_user_id or "",
                    "status": row.status,
                    "error": row.error or "",
                }
            )


def _write_results_csv(path: Path, results: list[DmResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "dm_seq",
        "dm_sent_at",
        "telegram_chat_id",
        "gc_title",
        "club_key",
        "player_telegram_user_id",
        "player_username",
        "invite_link",
        "status",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow(asdict(row))


def _print_human(
    summary: DmSummary,
    results: list[DmResult],
    csv_path: Path,
    failed_csv_path: Path,
) -> None:
    mode = "APPLY" if summary.apply_mode else "DRY-RUN"
    print(f"\nDM deposit groups invite ({mode}) — summary")
    print(f"Targets: {summary.targets} | processed: {summary.processed}")
    print(
        f"DM sent: {summary.dm_sent} | would send: {summary.dm_would_send} | "
        f"no player id: {summary.no_player_id} | no invite link: {summary.no_invite_link}"
    )
    print(
        f"Skipped chat: {summary.skipped_already_dm} | "
        f"skipped player (dup): {summary.skipped_player_already_dm} | "
        f"errors: {summary.errors}"
    )
    print(
        f"Tracker: {summary.tracker_path} ({summary.tracker_dm_sent_total} dm_sent total)"
    )
    print(f"Results CSV: {csv_path}")
    print(f"Failed DM CSV: {failed_csv_path} ({summary.failed_csv_rows} rows)")

    failed = _failed_dm_results(results)
    if failed:
        print(f"\n--- Failed to DM ({min(10, len(failed))} of {len(failed)}) ---")
        for r in failed[:10]:
            user = r.player_username or r.player_telegram_user_id or "?"
            print(f"  {r.gc_title!r} user={user} status={r.status}")
            if r.error:
                print(f"    error: {r.error}")

    next_up = [r for r in results if r.status == "would_dm"][:5]
    if next_up:
        print("\n--- Next to DM (CSV order) ---")
        for r in next_up:
            print(f"  {r.gc_title!r} player={r.player_telegram_user_id}")

    interesting = [
        r
        for r in results
        if r.status not in ("skipped_already_dm", "skipped_player_already_dm")
    ]
    if interesting:
        print(f"\n--- Sample ({min(20, len(interesting))} of {len(interesting)}) ---")
        for r in interesting[:20]:
            player = r.player_telegram_user_id or "?"
            seq = f"#{r.dm_seq} " if r.dm_seq else ""
            print(f"  {seq}{r.gc_title!r} player={player} status={r.status}")
            if r.invite_link:
                print(f"    link: {r.invite_link[:60]}...")
            if r.error:
                print(f"    error: {r.error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=_default_input_csv(),
        help="Target list CSV (default: gc_active_migrated_invite_targets.csv when present).",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Build target list from live payment tables instead of CSV.",
    )
    parser.add_argument(
        "--club-key",
        choices=CLUB_KEYS,
        help="Limit to one /gc MTProto club profile.",
    )
    parser.add_argument("--chat-id", type=int, help="Limit to one telegram chat id.")
    parser.add_argument(
        "--limit",
        type=int,
        help="Send/would-send at most N players this run (CSV order, after skips).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Send DMs and optionally export links (default: dry-run).",
    )
    parser.add_argument(
        "--export-invite-links",
        action="store_true",
        help="With --apply, export a fresh invite link via Telethon before DM.",
    )
    parser.add_argument(
        "--update-invite-links",
        action="store_true",
        help="With --apply --export-invite-links, upsert invite_link to Postgres.",
    )
    parser.add_argument(
        "--tracker-csv",
        type=Path,
        default=_default_tracker_csv(),
        help="Append-only log of who was DM'd, in send order (default: backups/…_tracker.csv).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore tracker and resend even if already dm_sent.",
    )
    parser.add_argument(
        "--dm-delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Pause between groups per club session (default: 2).",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        help="Write per-group results here (default: backups/dm_deposit_groups_invite_<ts>.csv).",
    )
    parser.add_argument(
        "--failed-csv-out",
        type=Path,
        help="Write could-not-DM rows here (default: backups/dm_deposit_groups_invite_failed_<ts>.csv).",
    )
    parser.add_argument("--json", action="store_true", help="JSON summary to stdout.")
    parser.add_argument("--quiet", action="store_true", help="Only warnings/errors on stderr.")
    args = parser.parse_args()

    if not args.json:
        _configure_logging(quiet=args.quiet)

    if args.from_db:
        targets = _load_targets_from_db(
            club_key_filter=args.club_key,
            chat_id_filter=args.chat_id,
        )
    else:
        targets = _load_targets_from_csv(
            args.input_csv,
            club_key_filter=args.club_key,
            chat_id_filter=args.chat_id,
        )

    if not targets:
        raise SystemExit("No targets matched filters.")

    tracker = DmTracker(args.tracker_csv.resolve())
    tracker.load()
    use_tracker = not bool(args.force)

    summary, results = asyncio.run(
        _run(
            targets=targets,
            apply=bool(args.apply),
            export_invite_links=bool(args.export_invite_links),
            update_invite_links=bool(args.update_invite_links),
            use_tracker=use_tracker,
            tracker=tracker,
            dm_delay_seconds=max(0.0, float(args.dm_delay)),
            send_limit=args.limit,
        )
    )

    csv_path = args.csv_out or _default_results_csv()
    failed_csv_path = args.failed_csv_out or _default_failed_csv()
    _write_results_csv(csv_path, results)
    _write_failed_dm_csv(failed_csv_path, results)
    summary.failed_csv_path = str(failed_csv_path)
    summary.failed_csv_rows = len(_failed_dm_results(results))

    if args.json:
        print(
            json.dumps(
                {
                    "summary": asdict(summary),
                    "results_csv": str(csv_path),
                    "failed_csv": str(failed_csv_path),
                    "failed_rows": [asdict(r) for r in _failed_dm_results(results)],
                    "groups": [asdict(r) for r in results],
                },
                indent=2,
            )
        )
    else:
        _print_human(summary, results, csv_path, failed_csv_path)

    if summary.errors and args.apply:
        sys.exit(2)


if __name__ == "__main__":
    main()
