"""All-time rake hold: classify, park, and the background check."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.cashout_rake_hold import (
    FLAG_TITLE,
    RAKE_HOLD_REASON,
    classify_all_time_rake,
    format_flag_details,
    format_unverified_dm,
    note_with_rake_reason,
    park_rake_hold,
    run_cashout_rake_check,
    schedule_cashout_rake_check,
)
from bot.services.clubgg_deposit_api import RakeOutcome
from bot.services.staff_cashout_records import _HOLD_NOTE_HEADER, hold_note_for_reason


def _ctx(**overrides):
    data = {
        "record_id": 9,
        "club_id": 2,
        "chat_id": -100,
        "group_title": "RT / 2427-3267 / Samin",
        "amount": Decimal("200"),
    }
    data.update(overrides)
    return data


def _outcome(**overrides) -> RakeOutcome:
    data = {
        "ok": True,
        "status": "success",
        "rake_overall": Decimal("0"),
        "pnl_overall": Decimal("150"),
    }
    data.update(overrides)
    return RakeOutcome(**data)


class ClassifyTestCase(unittest.TestCase):
    def test_zero_rake_and_win_flags(self) -> None:
        self.assertEqual(classify_all_time_rake(Decimal("0"), Decimal("150")), "flag")

    def test_rake_does_not_flag(self) -> None:
        self.assertEqual(classify_all_time_rake(Decimal("1"), Decimal("150")), "clear")

    def test_zero_rake_without_a_win_does_not_flag(self) -> None:
        self.assertEqual(classify_all_time_rake(Decimal("0"), Decimal("0")), "clear")
        self.assertEqual(classify_all_time_rake(Decimal("0"), Decimal("-20")), "clear")

    def test_missing_numbers_are_unverified(self) -> None:
        self.assertEqual(classify_all_time_rake(None, Decimal("10")), "unverified")
        self.assertEqual(classify_all_time_rake(Decimal("0"), None), "unverified")


class CopyTestCase(unittest.TestCase):
    def test_unverified_dm_names_the_group(self) -> None:
        self.assertEqual(
            format_unverified_dm("RT / 2427-3267 / Samin"),
            "Rake couldn't be checked.\nGroup: RT / 2427-3267 / Samin",
        )

    def test_flag_details_when_parked(self) -> None:
        self.assertEqual(
            format_flag_details(
                rake=Decimal("0"),
                pnl=Decimal("150"),
                cashout=Decimal("200"),
                parked=True,
            ),
            "All-time rake $0.00. All-time PnL +$150.00. "
            "Cashout $200.00 set to do not send.",
        )

    def test_note_starts_the_hold(self) -> None:
        self.assertEqual(
            note_with_rake_reason(None),
            f"{_HOLD_NOTE_HEADER}\n{RAKE_HOLD_REASON}",
        )

    def test_note_keeps_the_no_payment_line(self) -> None:
        existing = hold_note_for_reason("no_bound_payment")
        self.assertEqual(
            note_with_rake_reason(existing),
            f"{existing}\n{RAKE_HOLD_REASON}",
        )


class ParkTestCase(unittest.TestCase):
    def _session(self, record):
        session = MagicMock()
        session.get.return_value = record

        @contextlib.contextmanager
        def fake_get_db():
            yield session

        return fake_get_db

    def test_parks_an_active_cashout(self) -> None:
        record = SimpleNamespace(
            note=None,
            tracks_money_sent=True,
            amount=Decimal("200"),
            money_sends=[],
            do_not_send=False,
            updated_at=None,
        )
        with patch("bot.services.cashout_rake_hold.get_db", self._session(record)):
            self.assertEqual(park_rake_hold(9), "parked")
        self.assertTrue(record.do_not_send)
        self.assertIn(RAKE_HOLD_REASON, record.note)
        self.assertIn(_HOLD_NOTE_HEADER, record.note)

    def test_appends_when_the_no_payment_hold_is_already_there(self) -> None:
        record = SimpleNamespace(
            note=hold_note_for_reason("no_bound_payment"),
            tracks_money_sent=True,
            amount=Decimal("200"),
            money_sends=[],
            do_not_send=False,
            updated_at=None,
        )
        with patch("bot.services.cashout_rake_hold.get_db", self._session(record)):
            self.assertEqual(park_rake_hold(9), "parked")
        self.assertIn("No bound payment in this group.", record.note)
        self.assertIn(RAKE_HOLD_REASON, record.note)
        self.assertTrue(record.do_not_send)

    def test_leaves_a_sent_cashout_unchanged(self) -> None:
        record = SimpleNamespace(
            note=None,
            tracks_money_sent=True,
            amount=Decimal("200"),
            money_sends=[SimpleNamespace(amount=Decimal("200"))],
            do_not_send=False,
            updated_at=None,
        )
        with patch("bot.services.cashout_rake_hold.get_db", self._session(record)):
            self.assertEqual(park_rake_hold(9), "sent")
        self.assertFalse(record.do_not_send)
        self.assertIsNone(record.note)

    def test_second_pass_does_not_rewrite_the_note(self) -> None:
        record = SimpleNamespace(
            note=f"{_HOLD_NOTE_HEADER}\n{RAKE_HOLD_REASON}",
            tracks_money_sent=True,
            amount=Decimal("200"),
            money_sends=[],
            do_not_send=True,
            updated_at=None,
        )
        with patch("bot.services.cashout_rake_hold.get_db", self._session(record)):
            self.assertEqual(park_rake_hold(9), "already")


class RunTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_flag_parks_and_files_the_report(self) -> None:
        report = AsyncMock()
        with (
            patch(
                "bot.services.cashout_rake_hold.load_cashout_rake_context",
                return_value=_ctx(),
            ),
            patch(
                "bot.services.cashout_rake_hold._read_rake",
                new=AsyncMock(return_value=_outcome()),
            ),
            patch(
                "bot.services.cashout_rake_hold.park_rake_hold",
                return_value="parked",
            ) as park,
            patch(
                "bot.services.issue_reports.create_issue_report",
                report,
            ),
            patch("bot.services.cashout_rake_hold.get_db") as get_db,
        ):
            session = MagicMock()

            @contextlib.contextmanager
            def fake_db():
                yield session

            get_db.side_effect = fake_db
            await run_cashout_rake_check(9)

        park.assert_called_once_with(9)
        report.assert_awaited()
        kwargs = report.await_args.kwargs
        self.assertEqual(kwargs["title"], FLAG_TITLE)
        self.assertEqual(kwargs["category"], "cashout")
        self.assertEqual(kwargs["notify_tags"], ["head_admin"])
        self.assertIn("set to do not send", kwargs["description"])
        self.assertEqual(kwargs["group_title"], "RT / 2427-3267 / Samin")

    async def test_positive_rake_does_not_flag(self) -> None:
        with (
            patch(
                "bot.services.cashout_rake_hold.load_cashout_rake_context",
                return_value=_ctx(),
            ),
            patch(
                "bot.services.cashout_rake_hold._read_rake",
                new=AsyncMock(return_value=_outcome(rake_overall=Decimal("12"))),
            ),
            patch("bot.services.cashout_rake_hold.park_rake_hold") as park,
            patch("bot.services.cashout_rake_hold._dm_unverified") as dm,
            patch("bot.services.cashout_rake_hold._file_report") as report,
        ):
            await run_cashout_rake_check(9)
        park.assert_not_called()
        dm.assert_not_called()
        report.assert_not_called()

    async def test_failed_read_dms_and_does_not_park(self) -> None:
        with (
            patch(
                "bot.services.cashout_rake_hold.load_cashout_rake_context",
                return_value=_ctx(),
            ),
            patch(
                "bot.services.cashout_rake_hold._read_rake",
                new=AsyncMock(return_value=_outcome(ok=False, status="no_machine")),
            ),
            patch("bot.services.cashout_rake_hold.park_rake_hold") as park,
            patch(
                "bot.services.cashout_rake_hold._dm_unverified",
                new=AsyncMock(),
            ) as dm,
        ):
            await run_cashout_rake_check(9)
        park.assert_not_called()
        dm.assert_awaited_once()

    async def test_schedule_returns_before_the_check_finishes(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow(_record_id: int) -> None:
            started.set()
            await release.wait()

        with patch(
            "bot.services.cashout_rake_hold.run_cashout_rake_check",
            slow,
        ):
            schedule_cashout_rake_check(9)
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertTrue(started.is_set())
            release.set()
            await asyncio.sleep(0)
