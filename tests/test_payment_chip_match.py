"""Unit tests for payment chip-add ↔ notification matching."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import payment_chip_match as pcm


def _cand(
    *,
    method: str = "crypto",
    payment_id: int = 1,
    amount_cents: int = 50100,
    ref: datetime | None = None,
    title: str = "GTO / 5662-4970 / Ncann",
    tx: str | None = None,
) -> pcm.PaymentCandidate:
    return pcm.PaymentCandidate(
        method_slug=method,
        payment_id=payment_id,
        amount_cents=amount_cents,
        telegram_chat_id=-1001,
        club_id=4,
        group_title=title,
        reference_at=ref or datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc),
        transaction_hash=tx,
    )


class AmountToleranceTestCase(unittest.TestCase):
    def test_within_one_dollar(self) -> None:
        self.assertTrue(pcm.amount_within_tolerance(50100, 50050))
        self.assertTrue(pcm.amount_within_tolerance(50100, 50000))
        self.assertTrue(pcm.amount_within_tolerance(50100, 50200))

    def test_outside_one_dollar(self) -> None:
        self.assertFalse(pcm.amount_within_tolerance(50100, 50201))
        self.assertFalse(pcm.amount_within_tolerance(50100, 49900))


class ParseReferenceAtTestCase(unittest.TestCase):
    def test_paid_at_z(self) -> None:
        dt = pcm.parse_payment_reference_at(
            paid_at="2026-08-09T06:02:33Z",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(dt, datetime(2026, 8, 9, 6, 2, 33, tzinfo=timezone.utc))

    def test_falls_back_to_created(self) -> None:
        created = datetime(2026, 8, 9, 7, 0, tzinfo=timezone.utc)
        dt = pcm.parse_payment_reference_at(paid_at="not-a-date", created_at=created)
        self.assertEqual(dt, created)


class PickBestCandidateTestCase(unittest.TestCase):
    def test_prefers_closest_then_newest(self) -> None:
        older = _cand(
            payment_id=1,
            amount_cents=50000,
            ref=datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc),
        )
        newer_farther = _cand(
            payment_id=2,
            amount_cents=50200,
            ref=datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc),
        )
        newer_closer = _cand(
            payment_id=3,
            amount_cents=50050,
            ref=datetime(2026, 8, 9, 5, 30, tzinfo=timezone.utc),
        )
        picked = pcm.pick_best_candidate(
            [older, newer_farther, newer_closer],
            amount_cents=50100,
        )
        assert picked is not None
        self.assertEqual(picked.payment_id, 3)

    def test_same_distance_prefers_newest(self) -> None:
        older = _cand(
            payment_id=1,
            amount_cents=50100,
            ref=datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc),
        )
        newer = _cand(
            payment_id=2,
            amount_cents=50100,
            ref=datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc),
        )
        picked = pcm.pick_best_candidate([older, newer], amount_cents=50100)
        assert picked is not None
        self.assertEqual(picked.payment_id, 2)

    def test_prefer_explicit_payment_when_in_tolerance(self) -> None:
        other = _cand(payment_id=1, amount_cents=50100, method="venmo")
        preferred = _cand(payment_id=99, amount_cents=50050, method="crypto")
        picked = pcm.pick_best_candidate(
            [other, preferred],
            amount_cents=50100,
            prefer_method_slug="crypto",
            prefer_payment_id=99,
        )
        assert picked is not None
        self.assertEqual(picked.payment_id, 99)

    def test_out_of_tolerance_returns_none(self) -> None:
        cand = _cand(amount_cents=40000)
        self.assertIsNone(pcm.pick_best_candidate([cand], amount_cents=50100))


class TopUnmatchedTestCase(unittest.TestCase):
    def test_newest_first_limit_three(self) -> None:
        cands = [
            _cand(payment_id=i, ref=datetime(2026, 8, 9, i, tzinfo=timezone.utc))
            for i in range(1, 6)
        ]
        top = pcm.top_unmatched_for_chat(cands, limit=3)
        self.assertEqual([c.payment_id for c in top], [5, 4, 3])


class FormatMessageTestCase(unittest.TestCase):
    def test_matched(self) -> None:
        text = pcm.format_match_message(
            via=pcm.VIA_ADD,
            amount_cents=50100,
            matched=_cand(payment_id=693, amount_cents=50050),
            recent_unmatched=[],
        )
        self.assertIn("Payment match — /add $501", text)
        self.assertIn("crypto #693", text)
        self.assertIn("GTO / 5662-4970 / Ncann", text)

    def test_unmatched_lists_recent(self) -> None:
        text = pcm.format_match_message(
            via=pcm.VIA_AUTO_DEPOSIT,
            amount_cents=50100,
            matched=None,
            recent_unmatched=[
                _cand(method="crypto", payment_id=692, amount_cents=50000),
                _cand(method="venmo", payment_id=88, amount_cents=50100),
            ],
        )
        self.assertIn("auto-deposit $501 · no match", text)
        self.assertIn("crypto $500", text)
        self.assertIn("(#692)", text)
        self.assertIn("venmo $501", text)


class WindowBoundaryTestCase(unittest.TestCase):
    def test_lookback_is_two_hours(self) -> None:
        self.assertEqual(pcm.LOOKBACK, timedelta(hours=2))


class AmountCentsHelperTestCase(unittest.TestCase):
    def test_decimal(self) -> None:
        self.assertEqual(pcm.amount_decimal_to_cents(Decimal("501")), 50100)
        self.assertEqual(pcm.amount_decimal_to_cents(Decimal("500.50")), 50050)


class RunMatchFailSafeTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_run_swallows_errors(self) -> None:
        with patch.object(
            pcm,
            "match_chip_add_sync",
            side_effect=RuntimeError("boom"),
        ):
            await pcm.run_payment_chip_match(
                telegram_chat_id=-1,
                amount_cents=100,
                via=pcm.VIA_ADD,
            )

    async def test_run_notifies_on_success(self) -> None:
        with patch.object(
            pcm,
            "match_chip_add_sync",
            return_value=(None, [], "Payment match — /add $1 · no match"),
        ), patch.object(
            pcm, "notify_payment_chip_match", new_callable=AsyncMock
        ) as notify:
            await pcm.run_payment_chip_match(
                telegram_chat_id=-1,
                amount_cents=100,
                via=pcm.VIA_ADD,
            )
            notify.assert_awaited_once()


class PersistCryptoMetadataTestCase(unittest.TestCase):
    def test_crypto_metadata_includes_tx_hash(self) -> None:
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = None
        nested = MagicMock()
        nested.__enter__ = MagicMock(return_value=None)
        nested.__exit__ = MagicMock(return_value=False)
        session.begin_nested.return_value = nested

        cand = _cand(tx="0xabc123")
        row = pcm._persist_match(
            session,
            candidate=cand,
            amount_cents=50100,
            via=pcm.VIA_ADD,
            actor_telegram_user_id=1,
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.metadata_json, {"transaction_hash": "0xabc123"})


if __name__ == "__main__":
    unittest.main()
