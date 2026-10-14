"""Tick choice for the contact restore job. No Telegram, no database."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon.errors import FloodWaitError
from telethon.tl.functions.contacts import AddContactRequest
from telethon.tl.types import User

from bot.services.contact_restore import (
    MIN_BOOT_DELAY_SEC,
    PEER_FLOOD_PAUSE_SEC,
    ClubSnapshot,
    choose_action,
    compute_contact_restore_first_delay_sec,
    flood_resume_deadline,
    parse_contact_restore_csv,
    should_auto_disable,
    tick_async,
    tick_club,
)


def _now() -> datetime:
    return datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)


def _snap(**overrides) -> ClubSnapshot:
    base = dict(
        dialogs_filled=True,
        flood_resume_at=None,
        ready_row_id=None,
        username_row_id=None,
        unscanned_group_id=None,
        pending_count=3,
        total_count=3,
    )
    base.update(overrides)
    return ClubSnapshot(**base)


class ChooseActionTests(unittest.TestCase):
    def test_flood_pause_skips_a_ready_row(self) -> None:
        snap = _snap(
            flood_resume_at=_now() + timedelta(minutes=5),
            ready_row_id=4,
        )
        self.assertEqual(choose_action(snap, _now()), "flood")

    def test_ready_hash_is_one_add(self) -> None:
        snap = _snap(ready_row_id=4, username_row_id=9)
        self.assertEqual(choose_action(snap, _now()), "add")

    def test_username_resolve_is_not_an_add(self) -> None:
        snap = _snap(username_row_id=9)
        self.assertEqual(choose_action(snap, _now()), "username")

    def test_dialogs_come_before_adds(self) -> None:
        snap = _snap(dialogs_filled=False, ready_row_id=4)
        self.assertEqual(choose_action(snap, _now()), "dialogs")

    def test_unscanned_group_after_usernames(self) -> None:
        snap = _snap(unscanned_group_id=2)
        self.assertEqual(choose_action(snap, _now()), "group")

    def test_drained_when_nothing_left_to_do(self) -> None:
        snap = _snap(pending_count=1)
        self.assertEqual(choose_action(snap, _now()), "drained")

    def test_empty_queue_is_not_drained(self) -> None:
        snap = _snap(pending_count=0, total_count=0, dialogs_filled=False)
        self.assertEqual(choose_action(snap, _now()), "empty")


class AutoDisableTests(unittest.TestCase):
    def test_all_drained_stops_the_job(self) -> None:
        self.assertTrue(should_auto_disable(["drained", "drained", "drained"]))

    def test_a_pause_keeps_the_job(self) -> None:
        self.assertFalse(should_auto_disable(["drained", "flood"]))

    def test_empty_queue_keeps_the_job(self) -> None:
        self.assertFalse(should_auto_disable(["empty", "empty"]))

    def test_no_clubs_does_not_disable(self) -> None:
        self.assertFalse(should_auto_disable([]))


class DeadlineTests(unittest.TestCase):
    def test_flood_wait_adds_a_minute(self) -> None:
        deadline = flood_resume_deadline(_now(), 417, peer=False)
        self.assertEqual(deadline, _now() + timedelta(seconds=477))

    def test_peer_flood_pauses_one_hour(self) -> None:
        deadline = flood_resume_deadline(_now(), 10, peer=True)
        self.assertEqual(deadline, _now() + timedelta(seconds=PEER_FLOOD_PAUSE_SEC))

    def test_first_tick_waits_for_the_listener(self) -> None:
        delay = compute_contact_restore_first_delay_sec(
            last_tick=None, now=_now(), interval_sec=90
        )
        self.assertEqual(delay, MIN_BOOT_DELAY_SEC)

    def test_recent_tick_waits_out_the_interval(self) -> None:
        delay = compute_contact_restore_first_delay_sec(
            last_tick=_now() - timedelta(seconds=30),
            now=_now(),
            interval_sec=90,
        )
        self.assertEqual(delay, 60)

    def test_elapsed_interval_still_waits_for_boot(self) -> None:
        delay = compute_contact_restore_first_delay_sec(
            last_tick=_now() - timedelta(hours=2),
            now=_now(),
            interval_sec=90,
        )
        self.assertEqual(delay, MIN_BOOT_DELAY_SEC)


class CsvParseTests(unittest.TestCase):
    def test_phone_column_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contacts.csv"
            path.write_text(
                "club_key,contact_user_id,username,first_name,last_name,phone\n"
                "round_table,42,ada,Ada,Lovelace,5550001212\n"
                "round_table,43,,Only,,5550003434\n"
                "clubgto,7,gto,,Empty,5550009999\n"
                "elevate_admin,8,nope,Nope,Nope,5550008888\n"
                "round_table,42,ada2,Second,Row,5550007777\n",
                encoding="utf-8",
            )
            rows = parse_contact_restore_csv(path)
        self.assertEqual(len(rows), 3)
        encoded = json.dumps(rows)
        self.assertNotIn("555", encoded)
        by_id = {row["contact_user_id"]: row for row in rows}
        self.assertEqual(
            set(by_id[42]),
            {
                "club_key",
                "contact_user_id",
                "username",
                "first_name",
                "last_name",
                "status",
            },
        )
        self.assertEqual(by_id[42]["username"], "ada")
        self.assertEqual(by_id[42]["status"], "pending")
        self.assertEqual(by_id[7]["status"], "skipped")
        self.assertNotIn(8, by_id)

    def test_club_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contacts.csv"
            path.write_text(
                "club_key,contact_user_id,username,first_name,last_name,phone\n"
                "round_table,1,,Ada,,1\n"
                "clubgto,2,,Bee,,2\n",
                encoding="utf-8",
            )
            rows = parse_contact_restore_csv(path, club_key="clubgto")
        self.assertEqual([row["contact_user_id"] for row in rows], [2])


class TickClubTests(unittest.IsolatedAsyncioTestCase):
    def _patch_counts(self):
        return patch(
            "bot.services.contact_restore.club_counts",
            return_value=(10, 4),
        )

    async def test_flood_pause_does_not_call_telegram(self) -> None:
        client = AsyncMock()
        snap = _snap(
            flood_resume_at=_now() + timedelta(minutes=5),
            ready_row_id=4,
        )
        with (
            patch("bot.services.contact_restore.load_snapshot", return_value=snap),
            self._patch_counts(),
        ):
            action = await tick_club("round_table", client, _now())
        self.assertEqual(action, "flood")
        client.assert_not_awaited()
        client.get_entity.assert_not_awaited()

    async def test_add_sends_one_add_contact(self) -> None:
        client = AsyncMock()
        row = SimpleNamespace(
            id=4,
            contact_user_id=42,
            access_hash=99,
            first_name="Ada",
            last_name="Lovelace",
            username="ada",
        )
        snap = _snap(ready_row_id=4)
        with (
            patch("bot.services.contact_restore.load_snapshot", return_value=snap),
            patch("bot.services.contact_restore._load_row", return_value=row),
            patch("bot.services.contact_restore._set_status") as set_status,
            self._patch_counts(),
        ):
            action = await tick_club("round_table", client, _now())
        self.assertEqual(action, "add")
        self.assertEqual(client.await_count, 1)
        request = client.await_args.args[0]
        self.assertIsInstance(request, AddContactRequest)
        self.assertEqual(request.phone, "")
        self.assertFalse(request.add_phone_privacy_exception)
        self.assertEqual(request.first_name, "Ada")
        client.get_entity.assert_not_awaited()
        set_status.assert_called_once_with(4, "added")

    async def test_username_resolve_does_not_add(self) -> None:
        client = AsyncMock()
        client.get_entity.return_value = User(id=42, access_hash=99)
        row = SimpleNamespace(
            id=9,
            contact_user_id=42,
            access_hash=None,
            first_name="Ada",
            last_name="",
            username="ada",
        )
        snap = _snap(username_row_id=9)
        with (
            patch("bot.services.contact_restore.load_snapshot", return_value=snap),
            patch("bot.services.contact_restore._load_row", return_value=row),
            patch("bot.services.contact_restore._save_access_hash") as save_hash,
            self._patch_counts(),
        ):
            action = await tick_club("round_table", client, _now())
        self.assertEqual(action, "username")
        client.assert_not_awaited()
        client.get_entity.assert_awaited_once_with("ada")
        save_hash.assert_called_once_with(9, 99, "username")

    async def test_flood_wait_records_pause_and_returns(self) -> None:
        client = AsyncMock(side_effect=FloodWaitError(None, capture=417))
        row = SimpleNamespace(
            id=4,
            contact_user_id=42,
            access_hash=99,
            first_name="Ada",
            last_name="",
            username="",
        )
        snap = _snap(ready_row_id=4)
        started = datetime.now(timezone.utc)
        with (
            patch("bot.services.contact_restore.load_snapshot", return_value=snap),
            patch("bot.services.contact_restore._load_row", return_value=row),
            patch("bot.services.contact_restore._set_status") as set_status,
            patch("bot.services.contact_restore._set_flood_resume") as pause,
            self._patch_counts(),
        ):
            action = await tick_club("round_table", client, _now())
        self.assertEqual(action, "flood")
        self.assertLess((datetime.now(timezone.utc) - started).total_seconds(), 1)
        set_status.assert_not_called()
        resume_at = pause.call_args.args[1]
        self.assertEqual(resume_at, _now() + timedelta(seconds=477))


class TickAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_drained_clubs_stop_the_job(self) -> None:
        async def _drained(club: str, now: datetime) -> str:
            return "drained"

        with (
            patch(
                "bot.services.contact_restore.is_contact_restore_auto_disabled",
                return_value=False,
            ),
            patch("bot.services.contact_restore.record_contact_restore_tick"),
            patch(
                "bot.services.contact_restore.contact_restore_active_club_keys",
                return_value=("round_table", "creator_club"),
            ),
            patch(
                "bot.services.contact_restore._tick_club_locked",
                side_effect=_drained,
            ),
            patch("bot.services.contact_restore.mark_auto_disabled") as mark,
            patch("bot.services.contact_restore.remove_contact_restore_job") as remove,
        ):
            await tick_async()
        mark.assert_called_once()
        remove.assert_called_once()

    async def test_one_flood_does_not_stop_the_job(self) -> None:
        async def _mixed(club: str, now: datetime) -> str:
            return "flood" if club == "creator_club" else "drained"

        with (
            patch(
                "bot.services.contact_restore.is_contact_restore_auto_disabled",
                return_value=False,
            ),
            patch("bot.services.contact_restore.record_contact_restore_tick"),
            patch(
                "bot.services.contact_restore.contact_restore_active_club_keys",
                return_value=("round_table", "creator_club"),
            ),
            patch(
                "bot.services.contact_restore._tick_club_locked",
                side_effect=_mixed,
            ),
            patch("bot.services.contact_restore.mark_auto_disabled") as mark,
            patch("bot.services.contact_restore.remove_contact_restore_job") as remove,
        ):
            await tick_async()
        mark.assert_not_called()
        remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
