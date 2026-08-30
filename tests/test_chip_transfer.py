"""Chip transfer orchestration: plan resolution and the two-leg failure matrix."""

import contextlib
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.services import chip_transfer as ct

MOD = "bot.services.chip_transfer"
UNIONS_MOD = "bot.services.round_table_unions"
RT_ID = 2
CC_ID = 3
GTO_ID = 4
CHAT_ID = -100999

_CLUBS = {
    RT_ID: SimpleNamespace(id=RT_ID, name="Round Table"),
    CC_ID: SimpleNamespace(id=CC_ID, name="Creator Club"),
    GTO_ID: SimpleNamespace(id=GTO_ID, name="ClubGTO"),
}


def _lookup(club_id):
    return _CLUBS.get(int(club_id))


@contextlib.contextmanager
def _patched_clubs():
    """Both modules resolve clubs independently, so both need stubbing."""
    with patch(f"{MOD}.get_club_by_id", side_effect=_lookup), patch(
        f"{UNIONS_MOD}.get_club_by_id", side_effect=_lookup
    ):
        yield


def _claim(ok=True, status="success", reason=""):
    return SimpleNamespace(ok=ok, status=status, reason=reason)


class BuildPlanTests(unittest.TestCase):
    def _plan(self, club_id, dest):
        with _patched_clubs():
            return ct.build_transfer_plan(
                club_id=club_id, chat_id=CHAT_ID, destination_shorthand=dest
            )

    def test_round_table_to_aces(self):
        plan = self._plan(RT_ID, "AT")
        self.assertEqual(plan.source_shorthand, "RT")
        self.assertEqual(plan.destination_shorthand, "AT")
        self.assertEqual(plan.source_clubgg, "Round Table")
        self.assertEqual(plan.destination_clubgg, "Aces Table")

    def test_aces_to_round_table(self):
        plan = self._plan(RT_ID, "RT")
        self.assertEqual(plan.source_shorthand, "AT")
        self.assertEqual(plan.source_clubgg, "Aces Table")
        self.assertEqual(plan.destination_clubgg, "Round Table")

    def test_creator_club_to_aces(self):
        plan = self._plan(CC_ID, "AT")
        self.assertEqual(plan.source_shorthand, "CC")
        self.assertEqual(plan.source_clubgg, "Creator Club")
        self.assertEqual(plan.destination_clubgg, "Aces Table")

    def test_aces_to_creator_club(self):
        plan = self._plan(CC_ID, "CC")
        self.assertEqual(plan.source_shorthand, "AT")
        self.assertEqual(plan.source_clubgg, "Aces Table")
        self.assertEqual(plan.destination_clubgg, "Creator Club")

    def test_club_without_unions_has_no_plan(self):
        self.assertIsNone(self._plan(GTO_ID, "AT"))

    def test_destination_outside_the_clubs_pair_is_rejected(self):
        """A Creator Club player must not be able to target the RT union."""
        self.assertIsNone(self._plan(CC_ID, "RT"))

    def test_unknown_destination_is_rejected(self):
        self.assertIsNone(self._plan(RT_ID, "ZZ"))

    def test_lowercase_destination_is_accepted(self):
        self.assertEqual(self._plan(RT_ID, "at").destination_shorthand, "AT")


class RunTransferTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with _patched_clubs():
            self.plan = ct.build_transfer_plan(
                club_id=RT_ID, chat_id=CHAT_ID, destination_shorthand="AT"
            )

    async def _run(self, *, claim, add=None, on_claimed=None):
        with patch(f"{MOD}.run_auto_claim", AsyncMock(**claim)) as mock_claim, patch(
            f"{MOD}.run_auto_chip_add", AsyncMock(**(add or {}))
        ) as mock_add:
            result = await ct.run_transfer(
                plan=self.plan,
                amount=Decimal("200"),
                transfer_key="k1",
                group_title="RT AT / 1234-5678 / Player",
                on_claimed=on_claimed,
            )
        return result, mock_claim, mock_add

    async def test_success_runs_both_legs_with_pinned_unions(self):
        result, mock_claim, mock_add = await self._run(
            claim={"return_value": _claim()}, add={"return_value": (True, "success")}
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.chips_in_limbo)
        self.assertEqual(mock_claim.await_args.kwargs["union_shorthand"], "RT")
        self.assertEqual(mock_add.await_args.kwargs["union_shorthand"], "AT")

    async def test_legs_use_distinct_request_ids(self):
        _r, mock_claim, mock_add = await self._run(
            claim={"return_value": _claim()}, add={"return_value": (True, "success")}
        )
        claim_id = mock_claim.await_args.kwargs["request_id"]
        add_id = mock_add.await_args.kwargs["request_id"]
        self.assertNotEqual(claim_id, add_id)
        self.assertIn("k1", claim_id)
        self.assertIn("k1", add_id)

    async def test_claim_failure_never_runs_the_add(self):
        result, _c, mock_add = await self._run(
            claim={"return_value": _claim(ok=False, status="fail", reason="no chips")}
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_leg, ct.LEG_CLAIM)
        mock_add.assert_not_awaited()
        # Nothing left the source club, so nothing is owed.
        self.assertIsNone(result.claimed_amount)
        self.assertFalse(result.chips_in_limbo)

    async def test_claim_uncertain_never_runs_the_add_but_flags_limbo(self):
        result, _c, mock_add = await self._run(
            claim={"return_value": _claim(ok=False, status="uncertain", reason="ocr")}
        )
        self.assertFalse(result.ok)
        mock_add.assert_not_awaited()
        self.assertEqual(result.claimed_amount, Decimal("200"))
        self.assertTrue(result.chips_in_limbo)
        self.assertIn("do not re-claim", result.reason.lower())

    async def test_claim_crash_is_contained(self):
        result, _c, mock_add = await self._run(
            claim={"side_effect": RuntimeError("boom")}
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_leg, ct.LEG_CLAIM)
        self.assertIsNone(result.claimed_amount)
        mock_add.assert_not_awaited()

    async def test_add_failure_reports_chips_in_limbo(self):
        result, _c, _a = await self._run(
            claim={"return_value": _claim()}, add={"return_value": (False, "fail")}
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_leg, ct.LEG_ADD)
        self.assertEqual(result.claimed_amount, Decimal("200"))
        self.assertTrue(result.chips_in_limbo)
        self.assertIn("Aces Table", result.reason)

    async def test_add_uncertain_warns_before_re_adding(self):
        result, _c, _a = await self._run(
            claim={"return_value": _claim()}, add={"return_value": (False, "uncertain")}
        )
        self.assertTrue(result.chips_in_limbo)
        self.assertIn("verify on clubgg", result.reason.lower())

    async def test_add_crash_reports_chips_in_limbo(self):
        result, _c, _a = await self._run(
            claim={"return_value": _claim()}, add={"side_effect": RuntimeError("boom")}
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_leg, ct.LEG_ADD)
        self.assertTrue(result.chips_in_limbo)

    async def test_on_claimed_fires_between_the_legs(self):
        order = []
        on_claimed = AsyncMock(side_effect=lambda: order.append("said"))

        with patch(
            f"{MOD}.run_auto_claim", AsyncMock(return_value=_claim())
        ), patch(
            f"{MOD}.run_auto_chip_add",
            AsyncMock(side_effect=lambda **_kw: order.append("added") or (True, "ok")),
        ):
            await ct.run_transfer(
                plan=self.plan,
                amount=Decimal("50"),
                transfer_key="k2",
                on_claimed=on_claimed,
            )
        self.assertEqual(order, ["said", "added"])

    async def test_on_claimed_failure_does_not_abort_the_add(self):
        result, _c, mock_add = await self._run(
            claim={"return_value": _claim()},
            add={"return_value": (True, "success")},
            on_claimed=AsyncMock(side_effect=RuntimeError("telegram down")),
        )
        self.assertTrue(result.ok)
        mock_add.assert_awaited_once()

    async def test_on_claimed_never_fires_when_the_claim_failed(self):
        on_claimed = AsyncMock()
        await self._run(
            claim={"return_value": _claim(ok=False, status="fail")},
            on_claimed=on_claimed,
        )
        on_claimed.assert_not_awaited()


class BlockedReasonTests(unittest.TestCase):
    def test_missing_api_config_blocks(self):
        with patch(f"{MOD}.load_config", return_value=None):
            self.assertIn("deposit API", ct.transfer_blocked_reason(RT_ID, "RT / 1-2 /"))

    def test_auto_claim_disabled_blocks(self):
        with patch(f"{MOD}.load_config", return_value=object()), patch(
            f"{MOD}.get_auto_claim_enabled", return_value=False
        ):
            self.assertIn("auto claim", ct.transfer_blocked_reason(RT_ID, "x"))

    def test_unreadable_title_blocks(self):
        with patch(f"{MOD}.load_config", return_value=object()), patch(
            f"{MOD}.get_auto_claim_enabled", return_value=True
        ):
            self.assertIn(
                "player id", ct.transfer_blocked_reason(RT_ID, "no ids here")
            )

    def test_ready_group_is_not_blocked(self):
        with patch(f"{MOD}.load_config", return_value=object()), patch(
            f"{MOD}.get_auto_claim_enabled", return_value=True
        ):
            self.assertIsNone(
                ct.transfer_blocked_reason(RT_ID, "RT AT / 1234-5678 / Player")
            )


class TransferKeyTests(unittest.TestCase):
    def test_keys_are_unique_per_transfer(self):
        self.assertNotEqual(ct.new_transfer_key(CHAT_ID), ct.new_transfer_key(CHAT_ID))


if __name__ == "__main__":
    unittest.main()
