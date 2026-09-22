"""End-to-end (mocked) tests for the automated /earlyrb orchestration."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from bot.services import early_rakeback_auto as auto
from bot.services import elevate_early_rakeback_api as elevate
from bot.services.clubgg_deposit_api import RakeOutcome


def _fee(
    *,
    rake_overall="1200",
    rake_filtered="400",
    pnl_overall="-900",
    pnl_filtered="-250",
    ok=True,
    status="success",
    reason="",
    has_upline=False,
):
    def dec(v):
        return None if v is None else Decimal(v)

    return RakeOutcome(
        ok=ok,
        status=status,
        reason=reason,
        player_id="8272-5942",
        clubgg_club="Round Table",
        rake_overall=dec(rake_overall),
        rake_filtered=dec(rake_filtered),
        pnl_overall=dec(pnl_overall),
        pnl_filtered=dec(pnl_filtered),
        range_start="2026-09-14",
        range_end="2026-09-17",
        has_upline=has_upline,
        job_id="job-1",
    )


def _quote(
    *,
    remaining="240",
    eligible=True,
    reason="ok",
    below_minimum=False,
    minimum="50",
    places=2,
    warnings=(),
    already_given="0",
):
    return elevate.Quote(
        eligible=eligible,
        reason=reason,
        remaining=Decimal(remaining),
        below_minimum=below_minimum,
        minimum_threshold=Decimal(minimum) if minimum is not None else None,
        display_decimal_places=places,
        gg_id="82725942",
        display_id="8272-5942",
        nickname="SomePlayer",
        member_type="player",
        source="custom_player",
        deal_type="flat",
        percentage=Decimal("60"),
        total_already_given=(None if already_given is None else Decimal(already_given)),
        warnings=tuple(warnings),
    )


_TARGET = auto.ClubTarget(
    clubgg_club="Round Table", elevate_slug="round-table", union_shorthand="RT"
)


class SlugMappingTests(unittest.TestCase):
    def test_every_club_and_union_pair(self) -> None:
        cases = [
            ("Round Table", "RT", "Round Table", "round-table"),
            ("Round Table", "AT", "Aces Table", "aces-table"),
            ("Creator Club", "CC", "Creator Club", "creator-club"),
            ("Creator Club", "AT", "Aces Table", "aces-table"),
            ("ClubGTO", None, "ClubGTO", "clubgto"),
        ]
        for club, union, clubgg, slug in cases:
            target = auto.resolve_club_target(club, union)
            self.assertIsNotNone(target, (club, union))
            self.assertEqual(target.clubgg_club, clubgg)
            self.assertEqual(target.elevate_slug, slug)

    def test_round_table_without_a_union_is_unmapped(self) -> None:
        self.assertIsNone(auto.resolve_club_target("Round Table", None))

    def test_unknown_club_is_unmapped(self) -> None:
        self.assertIsNone(auto.resolve_club_target("Not A Club", "RT"))


class FilterSanityTests(unittest.TestCase):
    def test_different_figures_are_fine(self) -> None:
        self.assertFalse(auto.date_filter_is_suspect(_fee()))

    def test_identical_non_zero_figures_are_suspect(self) -> None:
        fee = _fee(
            rake_overall="1200",
            rake_filtered="1200",
            pnl_overall="-900",
            pnl_filtered="-900",
        )
        self.assertTrue(auto.date_filter_is_suspect(fee))

    def test_all_zero_is_not_suspect(self) -> None:
        fee = _fee(
            rake_overall="0", rake_filtered="0", pnl_overall="0", pnl_filtered="0"
        )
        self.assertFalse(auto.date_filter_is_suspect(fee))

    def test_matching_rake_but_differing_pnl_is_fine(self) -> None:
        fee = _fee(
            rake_overall="400",
            rake_filtered="400",
            pnl_overall="-900",
            pnl_filtered="-250",
        )
        self.assertFalse(auto.date_filter_is_suspect(fee))

    def test_zero_rake_with_non_zero_matching_pnl_is_suspect(self) -> None:
        fee = _fee(
            rake_overall="0", rake_filtered="0", pnl_overall="-900", pnl_filtered="-900"
        )
        self.assertTrue(auto.date_filter_is_suspect(fee))


class AmountFormattingTests(unittest.TestCase):
    def test_two_decimal_places(self) -> None:
        self.assertEqual(auto.format_feeback_amount(Decimal("240.5")), "$240.50")

    def test_does_not_round_to_whole_dollars(self) -> None:
        self.assertEqual(auto.format_feeback_amount(Decimal("19.5")), "$19.50")
        self.assertEqual(auto.format_feeback_amount(Decimal("19.4")), "$19.40")

    def test_thousands_separator(self) -> None:
        self.assertEqual(
            auto.format_feeback_amount(Decimal("12345.6")), "$12,345.60"
        )

    def test_sub_cent_quantizes_to_cents(self) -> None:
        self.assertEqual(auto.format_feeback_amount(Decimal("240.004")), "$240.00")


class ClaimPromptTests(unittest.TestCase):
    def test_prompt_shows_amount_and_the_cashout_timer_notice(self) -> None:
        prompt = auto.format_claim_prompt(_quote(remaining="240.5"))

        self.assertIn("Your total remaining feeback for this week is: $240.50", prompt)
        self.assertIn("Would you like to claim?", prompt)
        self.assertIn(
            "Early feeback counts as a deposit and will reset the cashout timer",
            prompt,
        )

    def test_prompt_never_mentions_a_daily_limit(self) -> None:
        prompt = auto.format_claim_prompt(_quote()).lower()

        self.assertNotIn("24 hour", prompt)
        self.assertNotIn("once every", prompt)

    def test_prompt_includes_already_claimed_when_elevate_has_a_total(self) -> None:
        prompt = auto.format_claim_prompt(_quote(already_given="80"))

        self.assertIn("You've already claimed $80.00 of feeback this week.", prompt)
        self.assertIn("Your total remaining feeback for this week is: $240.00", prompt)

    def test_prompt_omits_already_claimed_when_nothing_has_been_taken(self) -> None:
        prompt = auto.format_claim_prompt(_quote(already_given="0"))
        self.assertNotIn("already claimed", prompt)

    def test_prompt_omits_already_claimed_when_elevate_did_not_send_it(self) -> None:
        prompt = auto.format_claim_prompt(_quote(already_given=None))
        self.assertNotIn("already claimed", prompt)


class IdempotencyKeyTests(unittest.TestCase):
    def test_key_is_chat_scoped_and_unique_per_press(self) -> None:
        first = auto.new_idempotency_key(-1001234)
        second = auto.new_idempotency_key(-1001234)
        self.assertTrue(first.startswith("tg:-1001234:"))
        self.assertNotEqual(first, second)


class CheckFeeTests(unittest.IsolatedAsyncioTestCase):
    async def _check(self, fee):
        with patch.object(auto, "run_rake_check", AsyncMock(return_value=fee)):
            return await auto.check_fee(
                club_id=1,
                chat_id=-100,
                group_title="RT / 8272-5942 / P",
                union_shorthand="RT",
            )

    async def test_happy_path(self) -> None:
        stage = await self._check(_fee())
        self.assertEqual(stage.kind, "ok")

    async def test_failed_job_escalates(self) -> None:
        stage = await self._check(
            _fee(ok=False, status="fail", reason="range mismatch")
        )
        self.assertEqual(stage.kind, "escalate")
        self.assertIn("range mismatch", stage.detail)

    async def test_missing_filtered_fee_escalates(self) -> None:
        stage = await self._check(_fee(rake_filtered=None))
        self.assertEqual(stage.kind, "escalate")

    async def test_suspect_filter_escalates_with_range(self) -> None:
        stage = await self._check(
            _fee(
                rake_overall="400",
                rake_filtered="400",
                pnl_overall="-9",
                pnl_filtered="-9",
            )
        )
        self.assertEqual(stage.kind, "escalate")
        self.assertIn("2026-09-14", stage.detail)

    async def test_zero_fee_is_no_fee_not_a_quote(self) -> None:
        stage = await self._check(
            _fee(
                rake_overall="5", rake_filtered="0", pnl_overall="-9", pnl_filtered="-1"
            )
        )
        self.assertEqual(stage.kind, "no_fee")

    async def test_member_with_an_upline_is_ineligible(self) -> None:
        stage = await self._check(_fee(has_upline=True))
        self.assertEqual(stage.kind, "has_upline")

    async def test_upline_outranks_the_weeks_numbers(self) -> None:
        # Ineligible is ineligible: an agency member is told so rather than
        # "no fee this week" or an admin escalation over the date filter.
        for fee in (
            _fee(has_upline=True, rake_filtered="0"),
            _fee(
                has_upline=True,
                rake_overall="400",
                rake_filtered="400",
                pnl_overall="-9",
                pnl_filtered="-9",
            ),
        ):
            with self.subTest(rake_filtered=fee.rake_filtered):
                stage = await self._check(fee)
                self.assertEqual(stage.kind, "has_upline")

    async def test_unreadable_upline_escalates(self) -> None:
        stage = await self._check(_fee(has_upline=None))
        self.assertEqual(stage.kind, "escalate")
        self.assertIn("has_upline", stage.detail)


class QuoteStageTests(unittest.IsolatedAsyncioTestCase):
    async def _quote_stage(self, quote_result, *, max_amount=None):
        with (
            patch.object(
                auto.elevate,
                "quote_early_rakeback",
                AsyncMock(return_value=quote_result),
            ),
            patch.object(
                auto, "get_early_rakeback_max_auto_amount", return_value=max_amount
            ),
        ):
            return await auto.quote_feeback(
                club_id=1, target=_TARGET, gg_player_id="8272-5942", fee=_fee()
            )

    async def test_eligible(self) -> None:
        stage = await self._quote_stage(elevate.QuoteResult(True, quote=_quote()))
        self.assertEqual(stage.kind, "eligible")

    async def test_pl_is_sent_from_the_filtered_figures(self) -> None:
        mock = AsyncMock(return_value=elevate.QuoteResult(True, quote=_quote()))
        with (
            patch.object(auto.elevate, "quote_early_rakeback", mock),
            patch.object(auto, "get_early_rakeback_max_auto_amount", return_value=None),
        ):
            await auto.quote_feeback(
                club_id=1, target=_TARGET, gg_player_id="8272-5942", fee=_fee()
            )
        kwargs = mock.call_args.kwargs
        self.assertEqual(kwargs["rake"], Decimal("400"))
        self.assertEqual(kwargs["pl"], Decimal("-250"))
        self.assertEqual(kwargs["club_slug"], "round-table")

    async def test_nothing_remaining(self) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(
                True,
                quote=_quote(
                    eligible=False,
                    reason=elevate.REASON_NOTHING_REMAINING,
                    remaining="0",
                ),
            )
        )
        self.assertEqual(stage.kind, "nothing_remaining")
        self.assertEqual(stage.player_message, auto.NOTHING_REMAINING_COPY)

    async def test_nothing_remaining_includes_already_claimed(self) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(
                True,
                quote=_quote(
                    eligible=False,
                    reason=elevate.REASON_NOTHING_REMAINING,
                    remaining="0",
                    already_given="80",
                ),
            )
        )
        self.assertEqual(stage.kind, "nothing_remaining")
        self.assertEqual(
            stage.player_message,
            "You've already claimed all of your feeback for this week ($80.00).",
        )

    async def test_not_listed_escalates(self) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(
                True,
                quote=_quote(
                    eligible=False,
                    reason=elevate.REASON_NOT_LISTED_STANDARD_DISABLED,
                    remaining="0",
                ),
            )
        )
        self.assertEqual(stage.kind, "escalate")

    async def test_below_minimum_copy_uses_threshold_and_decimals(self) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(
                True, quote=_quote(remaining="12.5", below_minimum=True, minimum="50")
            )
        )
        self.assertEqual(stage.kind, "below_minimum")
        self.assertIn("($12.50)", stage.player_message)
        self.assertIn("below the $50.00 minimum", stage.player_message)
        self.assertIn("over $50.00", stage.player_message)

    async def test_below_minimum_shows_cents_even_when_elevate_displays_dollars(
        self,
    ) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(
                True,
                quote=_quote(
                    remaining="19.5",
                    below_minimum=True,
                    minimum="20",
                    places=0,
                ),
            )
        )
        self.assertIn("($19.50)", stage.player_message)
        self.assertIn("below the $20.00 minimum", stage.player_message)
        self.assertNotIn("($20)", stage.player_message)

    async def test_over_max_is_gated(self) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(True, quote=_quote(remaining="500")),
            max_amount=Decimal("300"),
        )
        self.assertEqual(stage.kind, "over_max")
        self.assertEqual(stage.player_message, auto.ADMIN_SHORTLY_COPY)

    async def test_exactly_at_max_still_auto_claims(self) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(True, quote=_quote(remaining="300")),
            max_amount=Decimal("300"),
        )
        self.assertEqual(stage.kind, "eligible")

    async def test_no_max_never_gates(self) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(True, quote=_quote(remaining="99999"))
        )
        self.assertEqual(stage.kind, "eligible")

    async def test_warnings_do_not_block(self) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(
                True, quote=_quote(warnings=("excluded_manual", "multiple_lists"))
            )
        )
        self.assertEqual(stage.kind, "eligible")

    async def test_quote_error_escalates(self) -> None:
        stage = await self._quote_stage(
            elevate.QuoteResult(False, "club_not_found", "Unknown club")
        )
        self.assertEqual(stage.kind, "escalate")
        self.assertIn("club_not_found", stage.detail)


class ClaimTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.record = AsyncMock()
        self.delete = AsyncMock(
            return_value=elevate.DeleteResult(True, "ok", amount_removed=Decimal("240"))
        )
        self.chip_add = AsyncMock(return_value=(True, "success"))
        self.timer = patch.object(auto, "reset_cashout_timer").start()
        self.updates = patch.object(auto, "update_claim_row").start()
        self.notify_failed = patch.object(
            auto, "notify_earlyrb_auto_failed", AsyncMock()
        ).start()
        self.notify_chips = patch.object(
            auto, "notify_earlyrb_chips_not_added", AsyncMock()
        ).start()
        self.notify_added = patch.object(
            auto, "notify_earlyrb_auto_added", AsyncMock()
        ).start()
        self.escalate_after = patch.object(
            auto, "get_escalate_auto_early_rakeback", return_value=False
        ).start()
        self.addCleanup(patch.stopall)

    async def _claim(self, *, dry_run=False, allow_requote=True):
        with (
            patch.object(auto.elevate, "record_early_rakeback", self.record),
            patch.object(auto.elevate, "delete_early_rakeback", self.delete),
            patch.object(auto, "run_auto_chip_add", self.chip_add),
            patch.object(auto, "deposit_api_dry_run", return_value=dry_run),
            patch.object(auto.asyncio, "sleep", AsyncMock()),
        ):
            return await auto.claim_feeback(
                club_id=1,
                chat_id=-100,
                user_id=555,
                group_title="RT / 8272-5942 / P",
                gg_player_id="8272-5942",
                nickname="SomePlayer",
                target=_TARGET,
                fee=_fee(),
                quote=_quote(),
                idempotency_key="tg:-100:abc",
                claim_id=7,
                allow_requote=allow_requote,
            )

    async def test_happy_path_records_then_adds_chips(self) -> None:
        self.record.return_value = elevate.RecordResult(
            True, "ok", amount_recorded=Decimal("240"), record_id="r1", entry_id="e1"
        )
        stage = await self._claim()

        self.assertEqual(stage.kind, "added")
        self.assertEqual(stage.amount, Decimal("240"))
        self.assertEqual(stage.player_message, "$240.00 feeback added to your account!")
        self.timer.assert_called_once_with(1, -100, 555)
        self.record.assert_awaited_once()
        self.assertEqual(self.record.call_args.kwargs["idempotency_key"], "tg:-100:abc")
        self.assertEqual(self.chip_add.call_args.kwargs["amount"], Decimal("240"))
        self.assertEqual(self.chip_add.call_args.kwargs["union_shorthand"], "RT")
        self.assertEqual(self.chip_add.call_args.kwargs["label"], "Early Feeback")
        self.delete.assert_not_awaited()
        self.notify_added.assert_not_awaited()

    async def test_success_escalates_when_verify_toggle_is_on(self) -> None:
        self.record.return_value = elevate.RecordResult(
            True, "ok", amount_recorded=Decimal("240")
        )
        self.escalate_after.return_value = True
        stage = await self._claim()

        self.assertEqual(stage.kind, "added")
        self.assertEqual(stage.player_message, "$240.00 feeback added to your account!")
        self.notify_added.assert_awaited_once()
        kwargs = self.notify_added.call_args.kwargs
        self.assertEqual(kwargs["amount"], Decimal("240"))
        self.assertEqual(kwargs["gg_player_id"], "8272-5942")
        self.assertEqual(kwargs["clubgg_club"], "Round Table")

    async def test_records_the_amount_elevate_returns_not_the_quote(self) -> None:
        self.record.return_value = elevate.RecordResult(
            True, "ok", amount_recorded=Decimal("199.99")
        )
        stage = await self._claim()
        self.assertEqual(stage.amount, Decimal("199.99"))
        self.assertEqual(self.chip_add.call_args.kwargs["amount"], Decimal("199.99"))

    async def test_duplicate_replay_is_treated_as_recorded(self) -> None:
        self.record.return_value = elevate.RecordResult(
            True,
            elevate.CODE_ALREADY_RECORDED,
            duplicate=True,
            amount_recorded=Decimal("240"),
        )
        stage = await self._claim()
        self.assertEqual(stage.kind, "added")
        self.timer.assert_called_once()

    async def test_record_failed_never_resets_timer_or_adds_chips(self) -> None:
        self.record.return_value = elevate.RecordResult(
            False, "club_not_found", "Unknown club"
        )
        stage = await self._claim()

        self.assertEqual(stage.kind, "escalate")
        self.assertEqual(stage.player_message, auto.ADMIN_SHORTLY_COPY)
        self.timer.assert_not_called()
        self.chip_add.assert_not_awaited()
        self.notify_failed.assert_awaited_once()
        self.delete.assert_not_awaited()

    async def test_amount_changed_returns_the_fresh_quote(self) -> None:
        fresh = _quote(remaining="310")
        self.record.return_value = elevate.RecordResult(
            False, elevate.CODE_AMOUNT_CHANGED, "Amount changed", quote=fresh
        )
        stage = await self._claim()

        self.assertEqual(stage.kind, "amount_changed")
        self.assertEqual(stage.amount, Decimal("310"))
        self.timer.assert_not_called()
        self.chip_add.assert_not_awaited()

    async def test_second_amount_mismatch_escalates(self) -> None:
        self.record.return_value = elevate.RecordResult(
            False,
            elevate.CODE_AMOUNT_CHANGED,
            "Amount changed",
            quote=_quote(remaining="310"),
        )
        stage = await self._claim(allow_requote=False)

        self.assertEqual(stage.kind, "escalate")
        self.notify_failed.assert_awaited_once()

    async def test_record_in_progress_retries_the_same_key(self) -> None:
        self.record.side_effect = [
            elevate.RecordResult(False, elevate.CODE_RECORD_IN_PROGRESS, "busy"),
            elevate.RecordResult(True, "ok", amount_recorded=Decimal("240")),
        ]
        stage = await self._claim()

        self.assertEqual(stage.kind, "added")
        self.assertEqual(self.record.await_count, 2)
        keys = {c.kwargs["idempotency_key"] for c in self.record.call_args_list}
        self.assertEqual(keys, {"tg:-100:abc"})

    async def test_record_ok_but_chips_failed_rolls_back_elevate(self) -> None:
        self.record.return_value = elevate.RecordResult(
            True, "ok", amount_recorded=Decimal("240"), record_id="r1"
        )
        self.chip_add.return_value = (False, "uncertain")
        stage = await self._claim()

        self.assertEqual(stage.kind, "chips_failed")
        self.assertEqual(stage.player_message, auto.ADMIN_SHORTLY_COPY)
        self.delete.assert_awaited_once()
        self.assertEqual(self.delete.call_args.kwargs["idempotency_key"], "tg:-100:abc")
        self.assertEqual(self.delete.call_args.kwargs["record_id"], "r1")
        # Rolled back: not a deposit, and not the "add chips by hand" alert.
        self.timer.assert_not_called()
        self.notify_chips.assert_not_awaited()
        self.notify_failed.assert_awaited_once()
        self.assertIn("rolled back", self.notify_failed.call_args.kwargs["detail"])
        statuses = [c.kwargs.get("status") for c in self.updates.call_args_list]
        self.assertIn("rolled_back", statuses)

    async def test_already_deleted_counts_as_rolled_back(self) -> None:
        self.record.return_value = elevate.RecordResult(
            True, "ok", amount_recorded=Decimal("240")
        )
        self.chip_add.return_value = (False, "fail")
        self.delete.return_value = elevate.DeleteResult(
            True, elevate.CODE_ALREADY_DELETED
        )
        stage = await self._claim()

        self.assertEqual(stage.kind, "chips_failed")
        self.timer.assert_not_called()
        self.notify_chips.assert_not_awaited()

    async def test_chips_failed_and_rollback_failed_alerts_loudly(self) -> None:
        self.record.return_value = elevate.RecordResult(
            True, "ok", amount_recorded=Decimal("240")
        )
        self.chip_add.return_value = (False, "uncertain")
        self.delete.return_value = elevate.DeleteResult(
            False, "request_failed", "request failed: TimeoutException"
        )
        stage = await self._claim()

        self.assertEqual(stage.kind, "chips_failed")
        self.assertEqual(stage.player_message, auto.ADMIN_SHORTLY_COPY)
        # Still on the ledger, so it counts as a deposit and needs a human to add chips.
        self.timer.assert_called_once()
        self.notify_chips.assert_awaited_once()
        kwargs = self.notify_chips.call_args.kwargs
        self.assertEqual(kwargs["amount"], Decimal("240"))
        self.assertEqual(kwargs["gg_player_id"], "8272-5942")
        self.assertIn("rollback failed", kwargs["detail"])
        self.notify_failed.assert_not_awaited()

    async def test_delete_retries_the_same_key_on_transport_failure(self) -> None:
        self.record.return_value = elevate.RecordResult(
            True, "ok", amount_recorded=Decimal("240")
        )
        self.chip_add.return_value = (False, "fail")
        self.delete.side_effect = [
            elevate.DeleteResult(False, "request_failed", "timeout"),
            elevate.DeleteResult(True, "ok"),
        ]
        stage = await self._claim()

        self.assertEqual(stage.kind, "chips_failed")
        self.assertEqual(self.delete.await_count, 2)
        self.timer.assert_not_called()
        self.notify_chips.assert_not_awaited()

    async def test_dry_run_never_touches_elevate(self) -> None:
        stage = await self._claim(dry_run=True)

        self.assertEqual(stage.kind, "added")
        self.record.assert_not_awaited()
        self.delete.assert_not_awaited()
        self.timer.assert_not_called()
        self.notify_failed.assert_awaited_once()
        self.assertIn("DRY RUN", self.notify_failed.call_args.kwargs["detail"])


if __name__ == "__main__":
    unittest.main()
