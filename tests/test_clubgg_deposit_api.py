import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.services import clubgg_deposit_api as api
from bot.services.clubgg_deposit_api import (
    _resolve_round_table_union_for_auto_chip_add,
    _resolve_round_table_union_shorthand,
    _resolve_union_for_auto_chip_add,
    resolve_clubgg_club_name,
)
from bot.services.player_details import (
    merge_union_prefix,
    shorthand_tokens_for_club_resolve,
)
from bot.services.round_table_unions import (
    CREATOR_CLUB_DEPOSIT_UNIONS,
    home_union_for_club_name,
    union_label_for_shorthand,
    union_shorthands_for_club_name,
)


class TestRoundTableUnionResolution(unittest.TestCase):
    def test_defaults_rt_when_missing(self) -> None:
        self.assertEqual(_resolve_round_table_union_shorthand(None), "RT")
        self.assertEqual(_resolve_round_table_union_shorthand(""), "RT")
        self.assertEqual(_resolve_round_table_union_shorthand("  "), "RT")

    def test_uses_stored_rt_or_at(self) -> None:
        self.assertEqual(_resolve_round_table_union_shorthand("RT"), "RT")
        self.assertEqual(_resolve_round_table_union_shorthand("rt"), "RT")
        self.assertEqual(_resolve_round_table_union_shorthand("AT"), "AT")
        self.assertEqual(_resolve_round_table_union_shorthand(" at "), "AT")

    def test_invalid_stored_union_defaults_rt(self) -> None:
        self.assertEqual(_resolve_round_table_union_shorthand("XX"), "RT")

    def test_title_at_only_routes_to_aces(self) -> None:
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "AT / 1234-5678 / Player", None
            ),
            "AT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "AT / 1234-5678 / Player", "RT"
            ),
            "AT",
        )

    def test_title_rt_only_routes_to_round_table(self) -> None:
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT / 1234-5678 / Player", None
            ),
            "RT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT / 1234-5678 / Player", "AT"
            ),
            "RT",
        )

    def test_title_both_uses_deposit_union(self) -> None:
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT AT / 1234-5678 / Player", "AT"
            ),
            "AT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT AT / 8190-5287 / ThePirate343", "RT"
            ),
            "RT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT AT / 1234-5678 / Player", None
            ),
            "RT",
        )

    def test_no_title_unions_falls_back_to_deposit_union(self) -> None:
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(None, "AT"),
            "AT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "GTO / 1234-5678 / Player", "AT"
            ),
            "AT",
        )

    def test_resolve_clubgg_club_name_round_table(self) -> None:
        self.assertEqual(resolve_clubgg_club_name("Round Table", "RT"), "Round Table")
        self.assertEqual(resolve_clubgg_club_name("Round Table", "AT"), "Aces Table")
        self.assertIsNone(resolve_clubgg_club_name("Round Table", None))

    def test_resolve_clubgg_club_name_non_union_clubs(self) -> None:
        self.assertEqual(resolve_clubgg_club_name("ClubGTO", None), "ClubGTO")
        self.assertEqual(resolve_clubgg_club_name("Creator Club", None), "Creator Club")


class TestCreatorClubAcesUnionResolution(unittest.TestCase):
    """Creator Club players may route chips to Aces Table (Massiv)."""

    def test_resolve_clubgg_club_name_creator_club(self) -> None:
        self.assertEqual(resolve_clubgg_club_name("Creator Club", "AT"), "Aces Table")
        self.assertEqual(resolve_clubgg_club_name("Creator Club", "CC"), "Creator Club")
        # Unknown/garbage union must never silently reroute to Aces.
        self.assertEqual(resolve_clubgg_club_name("Creator Club", "XX"), "Creator Club")

    def test_cc_only_title_stays_creator_club(self) -> None:
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC / 1234-5678 / Player", None
            ),
            "CC",
        )

    def test_cc_at_title_uses_recorded_choice(self) -> None:
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC AT / 1234-5678 / Player", "AT"
            ),
            "AT",
        )
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC AT / 1234-5678 / Player", "CC"
            ),
            "CC",
        )

    def test_legacy_cc_at_title_without_choice_defaults_creator_club(self) -> None:
        """Groups renamed CC AT for audit must keep behaving as they do today."""
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC AT / 1234-5678 / Player", None
            ),
            "CC",
        )

    def test_rt_union_never_leaks_into_creator_club(self) -> None:
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC AT / 1234-5678 / Player", "RT"
            ),
            "CC",
        )

    def test_clubs_without_unions_return_none(self) -> None:
        self.assertIsNone(
            _resolve_union_for_auto_chip_add(
                "ClubGTO", "GTO / 1234-5678 / Player", "AT"
            )
        )

    def test_round_table_behaviour_unchanged(self) -> None:
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Round Table", "RT AT / 1234-5678 / Player", None
            ),
            "RT",
        )
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Round Table", "AT / 1234-5678 / Player", "RT"
            ),
            "AT",
        )


class TestCreatorClubUnionConfig(unittest.TestCase):
    def test_creator_club_offers_cc_and_at(self) -> None:
        self.assertEqual(
            [u["shorthand"] for u in CREATOR_CLUB_DEPOSIT_UNIONS], ["CC", "AT"]
        )
        self.assertEqual(
            union_shorthands_for_club_name("Creator Club"), frozenset({"CC", "AT"})
        )
        self.assertEqual(
            union_shorthands_for_club_name("Round Table"), frozenset({"RT", "AT"})
        )
        self.assertEqual(union_shorthands_for_club_name("ClubGTO"), frozenset())

    def test_home_unions(self) -> None:
        self.assertEqual(home_union_for_club_name("Creator Club"), "CC")
        self.assertEqual(home_union_for_club_name("Round Table"), "RT")
        self.assertIsNone(home_union_for_club_name("ClubGTO"))

    def test_labels(self) -> None:
        self.assertEqual(union_label_for_shorthand("CC"), "Creator Club (TMT Union)")
        self.assertEqual(union_label_for_shorthand("AT"), "Aces Table (Massiv Union)")
        self.assertEqual(union_label_for_shorthand("RT"), "Round Table (TMT Union)")
        self.assertIsNone(union_label_for_shorthand("XX"))


class TestCreatorClubAcesTitleMerge(unittest.TestCase):
    """The group title must pick up the AT tag exactly like RT does."""

    def test_cc_title_gains_at_tag(self) -> None:
        self.assertEqual(
            merge_union_prefix("CC / 8272-5942 / @player", "AT"),
            "CC AT / 8272-5942 / @player",
        )

    def test_choosing_creator_club_does_not_rename(self) -> None:
        self.assertIsNone(merge_union_prefix("CC / 8272-5942 / @player", "CC"))

    def test_already_tagged_title_is_left_alone(self) -> None:
        self.assertIsNone(merge_union_prefix("CC AT / 8272-5942 / @player", "AT"))

    def test_cc_at_title_still_resolves_to_creator_club(self) -> None:
        self.assertEqual(shorthand_tokens_for_club_resolve("CC AT"), ["CC", "AT"])
        self.assertEqual(shorthand_tokens_for_club_resolve("AT CC"), ["CC", "AT"])


class TestChipAddUnionOverride(unittest.IsolatedAsyncioTestCase):
    """`/transfer` pins the destination union; deposit callers must not change.

    Each case stops the run at ``club_mapping_failed`` so the union that reached
    ``resolve_clubgg_club_name`` can be inspected without any HTTP.
    """

    async def _union_used(self, *, title, stored_union, **kwargs):
        cfg = SimpleNamespace(union_max_age_hours=24.0)
        with (
            patch.object(api, "load_config", return_value=cfg),
            patch.object(api, "_claim_request", return_value=True),
            patch.object(
                api, "get_club_by_id", return_value=SimpleNamespace(name="Round Table")
            ),
            patch.object(
                api, "get_last_deposit_union", return_value=(stored_union, None)
            ),
            patch.object(
                api, "resolve_clubgg_club_name", return_value=None
            ) as mock_resolve,
            patch.object(api, "_send_alert", AsyncMock()),
            patch.object(api, "_maybe_notify_rpa_deposit_failed", AsyncMock()),
        ):
            ok, status = await api.run_auto_chip_add(
                club_id=2,
                chat_id=-100,
                amount=Decimal("100"),
                request_id="test-req",
                group_title=title,
                **kwargs,
            )
        self.assertFalse(ok)
        self.assertEqual(status, "club_mapping_failed")
        return mock_resolve.call_args.args[1]

    async def test_pinned_union_overrides_title_and_stored_union(self) -> None:
        used = await self._union_used(
            title="RT AT / 1234-5678 / Player",
            stored_union="RT",
            union_shorthand="AT",
        )
        self.assertEqual(used, "AT")

    async def test_pinned_union_is_normalized(self) -> None:
        used = await self._union_used(
            title="RT / 1234-5678 / Player", stored_union=None, union_shorthand=" at "
        )
        self.assertEqual(used, "AT")

    async def test_omitted_param_still_resolves_from_title(self) -> None:
        used = await self._union_used(
            title="AT / 1234-5678 / Player", stored_union="RT"
        )
        self.assertEqual(used, "AT")

    async def test_omitted_param_still_resolves_from_stored_union(self) -> None:
        used = await self._union_used(
            title="RT AT / 1234-5678 / Player", stored_union="AT"
        )
        self.assertEqual(used, "AT")

    async def test_omitted_param_still_falls_back_to_home_union(self) -> None:
        used = await self._union_used(
            title="RT AT / 1234-5678 / Player", stored_union=None
        )
        self.assertEqual(used, "RT")


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    """httpx.AsyncClient stand-in with one scripted POST and queued GETs."""

    def __init__(self, *, post_response, get_responses=()) -> None:
        self._post_response = post_response
        self._get_responses = list(get_responses)
        self.get_urls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, **_kwargs):
        return self._post_response

    async def get(self, url, **_kwargs):
        self.get_urls.append(url)
        return self._get_responses.pop(0)


_RAKE_DATA = {
    "rake": {"overall": 1234.56, "filtered": 42.10},
    "pnl": {"overall": -318.0, "filtered": 12.34},
    "range": {"start": "2026-09-14", "end": "2026-09-16"},
    "role": "player",
    "has_upline": False,
}


class TestRunRakeCheck(unittest.IsolatedAsyncioTestCase):
    def _cfg(self):
        return SimpleNamespace(
            base_url="https://tunnel.test",
            token="tok",
            expected_host=None,
            expected_profile=None,
            union_max_age_hours=24.0,
            timeout_sec=10.0,
            poll_interval_sec=0.0,
            poll_timeout_sec=180.0,
            rake_poll_timeout_sec=420.0,
        )

    async def _run(self, client, *, title="RT / 8272-5942 / Player", **kwargs):
        with (
            patch.object(api, "load_config", return_value=self._cfg()),
            patch.object(
                api, "get_club_by_id", return_value=SimpleNamespace(name="Round Table")
            ),
            patch.object(api, "_health_ok", AsyncMock(return_value=(True, "ok"))),
            patch.object(api.httpx, "AsyncClient", return_value=client),
        ):
            return await api.run_rake_check(
                club_id=2,
                chat_id=-100,
                request_id="earlyrb-rake-test",
                group_title=title,
                union_shorthand="RT",
                **kwargs,
            )

    async def test_queued_then_polled_success(self) -> None:
        client = _FakeAsyncClient(
            post_response=_FakeResponse(202, {"job_id": "abc", "status": "queued"}),
            get_responses=[
                _FakeResponse(200, {"status": "success", "data": _RAKE_DATA})
            ],
        )
        outcome = await self._run(client)

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "success")
        self.assertEqual(outcome.player_id, "8272-5942")
        self.assertEqual(outcome.clubgg_club, "Round Table")
        self.assertEqual(outcome.rake_overall, Decimal("1234.56"))
        self.assertEqual(outcome.rake_filtered, Decimal("42.10"))
        self.assertEqual(outcome.pnl_overall, Decimal("-318.0"))
        self.assertEqual(outcome.pnl_filtered, Decimal("12.34"))
        self.assertEqual(outcome.range_start, "2026-09-14")
        self.assertEqual(outcome.range_end, "2026-09-16")
        self.assertIs(outcome.has_upline, False)
        self.assertEqual(client.get_urls, ["https://tunnel.test/rake/abc"])

    async def test_has_upline_is_read_verbatim(self) -> None:
        client = _FakeAsyncClient(
            post_response=_FakeResponse(
                200,
                {
                    "status": "success",
                    "data": dict(_RAKE_DATA, role="agent", has_upline=True),
                },
            )
        )
        outcome = await self._run(client)

        self.assertIs(outcome.has_upline, True)

    async def test_missing_has_upline_stays_none(self) -> None:
        # An older bot that does not report the field must not read as "no upline".
        data = {k: v for k, v in _RAKE_DATA.items() if k != "has_upline"}
        client = _FakeAsyncClient(
            post_response=_FakeResponse(200, {"status": "success", "data": data})
        )
        outcome = await self._run(client)

        self.assertIsNone(outcome.has_upline)

    async def test_non_boolean_has_upline_stays_none(self) -> None:
        client = _FakeAsyncClient(
            post_response=_FakeResponse(
                200,
                {"status": "success", "data": dict(_RAKE_DATA, has_upline="yes")},
            )
        )
        outcome = await self._run(client)

        self.assertIsNone(outcome.has_upline)

    async def test_terminal_in_post_skips_polling(self) -> None:
        client = _FakeAsyncClient(
            post_response=_FakeResponse(
                200, {"job_id": "abc", "status": "success", "data": _RAKE_DATA}
            )
        )
        outcome = await self._run(client)

        self.assertTrue(outcome.ok)
        self.assertEqual(client.get_urls, [])

    async def test_fail_status_is_not_ok(self) -> None:
        client = _FakeAsyncClient(
            post_response=_FakeResponse(202, {"job_id": "abc", "status": "queued"}),
            get_responses=[
                _FakeResponse(
                    200,
                    {"status": "fail", "reason": "date range never matched"},
                )
            ],
        )
        outcome = await self._run(client)

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "fail")
        self.assertEqual(outcome.reason, "date range never matched")
        self.assertIsNone(outcome.rake_filtered)

    async def test_null_numbers_become_none(self) -> None:
        client = _FakeAsyncClient(
            post_response=_FakeResponse(
                200,
                {
                    "status": "success",
                    "data": {
                        "rake": {"overall": None, "filtered": None},
                        "pnl": {"overall": None, "filtered": None},
                        "range": {"start": "2026-09-14", "end": "2026-09-14"},
                    },
                },
            )
        )
        outcome = await self._run(client)

        self.assertTrue(outcome.ok)
        self.assertIsNone(outcome.rake_filtered)
        self.assertIsNone(outcome.pnl_filtered)

    async def test_unknown_club_error_detail(self) -> None:
        client = _FakeAsyncClient(
            post_response=_FakeResponse(422, {"error": "unknown_club", "club": "Nope"})
        )
        outcome = await self._run(client)

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "fail")
        self.assertIn("unknown_club", outcome.reason)

    async def test_title_without_player_id(self) -> None:
        client = _FakeAsyncClient(post_response=_FakeResponse(200, {}))
        outcome = await self._run(client, title="no player id here")

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "no_player_id")

    async def test_not_configured(self) -> None:
        with patch.object(api, "load_config", return_value=None):
            outcome = await api.run_rake_check(
                club_id=2,
                chat_id=-100,
                request_id="r",
                group_title="RT / 8272-5942 / Player",
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "not_configured")


class TestDepositTransactions(unittest.TestCase):
    def test_deposit_only(self) -> None:
        self.assertEqual(
            api._deposit_transactions(Decimal("500"), None),
            [(api.LABEL_DEPOSIT, Decimal("500"), "base")],
        )

    def test_deposit_plus_bonus(self) -> None:
        self.assertEqual(
            api._deposit_transactions(Decimal("500"), Decimal("50")),
            [
                (api.LABEL_DEPOSIT, Decimal("500"), "base"),
                (api.LABEL_BONUS, Decimal("50"), "bonus"),
            ],
        )

    def test_bonus_only_skips_zero_deposit(self) -> None:
        self.assertEqual(
            api._deposit_transactions(Decimal("0"), Decimal("50")),
            [(api.LABEL_BONUS, Decimal("50"), "bonus")],
        )

    def test_empty_when_neither(self) -> None:
        self.assertEqual(api._deposit_transactions(Decimal("0"), None), [])

    def test_deposit_label_override_does_not_change_bonus(self) -> None:
        self.assertEqual(
            api._deposit_transactions(
                Decimal("12"), Decimal("3"), deposit_label=api.LABEL_FEEBACK
            ),
            [
                (api.LABEL_FEEBACK, Decimal("12"), "base"),
                (api.LABEL_BONUS, Decimal("3"), "bonus"),
            ],
        )


class _CapturingClient:
    """httpx stand-in that records POST JSON for label assertions."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url, **kwargs):
        body = kwargs.get("json") or {}
        self.posts.append((url, body))
        return _FakeResponse(
            202, {"job_id": body.get("request_id") or "j", "status": "success"}
        )

    async def get(self, url, **_kwargs):
        return _FakeResponse(200, {"ok": True})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class TestSubmitOperationLabel(unittest.IsolatedAsyncioTestCase):
    def _cfg(self):
        return SimpleNamespace(
            base_url="https://tunnel.test",
            token="tok",
            dry_run=False,
            expected_host=None,
            expected_profile=None,
        )

    async def test_deposit_body_includes_label(self) -> None:
        client = _CapturingClient()
        job_id, status, err = await api._submit_operation(
            self._cfg(),
            client,
            club="Round Table",
            player_id="8272-5942",
            amount="100",
            request_id="r1",
            label=api.LABEL_DEPOSIT,
        )
        self.assertIsNone(err)
        self.assertEqual(status, "success")
        self.assertEqual(job_id, "r1")
        url, body = client.posts[0]
        self.assertEqual(url, "https://tunnel.test/deposit")
        self.assertEqual(body["label"], "Deposit")

    async def test_claim_body_includes_label(self) -> None:
        client = _CapturingClient()
        await api._submit_operation(
            self._cfg(),
            client,
            operation="claim",
            club="Round Table",
            player_id="8272-5942",
            amount="50",
            request_id="c1",
            label=api.LABEL_CASHOUT,
        )
        url, body = client.posts[0]
        self.assertEqual(url, "https://tunnel.test/claim")
        self.assertEqual(body["label"], "Cashout")

    async def test_blank_label_is_omitted(self) -> None:
        client = _CapturingClient()
        await api._submit_operation(
            self._cfg(),
            client,
            club="Round Table",
            player_id="8272-5942",
            amount="100",
            request_id="r2",
            label="  ",
        )
        self.assertNotIn("label", client.posts[0][1])


class TestChipAddPostsLabels(unittest.IsolatedAsyncioTestCase):
    def _cfg(self):
        return SimpleNamespace(
            base_url="https://tunnel.test",
            token="tok",
            dry_run=False,
            expected_host=None,
            expected_profile=None,
            union_max_age_hours=24.0,
            timeout_sec=10.0,
            poll_interval_sec=0.0,
            poll_timeout_sec=180.0,
            rake_poll_timeout_sec=420.0,
            alert_on_success=False,
        )

    async def _run(self, client, **kwargs):
        with (
            patch.object(api, "load_config", return_value=self._cfg()),
            patch.object(api, "_claim_request", return_value=True),
            patch.object(
                api, "get_club_by_id", return_value=SimpleNamespace(name="Round Table")
            ),
            patch.object(api, "get_last_deposit_union", return_value=(None, None)),
            patch.object(api, "_health_ok", AsyncMock(return_value=(True, "ok"))),
            patch.object(api, "_send_alert", AsyncMock()),
            patch.object(api.httpx, "AsyncClient", return_value=client),
        ):
            return await api.run_auto_chip_add(
                club_id=2,
                chat_id=-100,
                amount=kwargs.pop("amount", Decimal("500")),
                request_id="add-1",
                group_title="RT / 8272-5942 / Player",
                **kwargs,
            )

    async def test_add_with_bonus_posts_deposit_then_bonus(self) -> None:
        client = _CapturingClient()
        ok, status = await self._run(client, bonus=Decimal("50"))
        self.assertTrue(ok)
        self.assertEqual(status, "success")
        self.assertEqual(
            [body["label"] for _url, body in client.posts], ["Deposit", "Bonus"]
        )
        self.assertEqual(client.posts[0][1]["amount"], "500")
        self.assertEqual(client.posts[1][1]["amount"], "50")

    async def test_feeback_override_is_posted(self) -> None:
        client = _CapturingClient()
        ok, status = await self._run(
            client, amount=Decimal("12"), label=api.LABEL_FEEBACK
        )
        self.assertTrue(ok)
        self.assertEqual(status, "success")
        self.assertEqual(client.posts[0][1]["label"], "Early Feeback")


class TestClaimPostsCashoutLabel(unittest.IsolatedAsyncioTestCase):
    async def test_default_claim_label_is_cashout(self) -> None:
        client = _CapturingClient()
        cfg = SimpleNamespace(
            base_url="https://tunnel.test",
            token="tok",
            dry_run=False,
            expected_host=None,
            expected_profile=None,
            timeout_sec=10.0,
            poll_interval_sec=0.0,
            poll_timeout_sec=180.0,
        )
        with (
            patch.object(api, "load_config", return_value=cfg),
            patch.object(api, "get_auto_claim_enabled", return_value=True),
            patch.object(
                api, "get_club_by_id", return_value=SimpleNamespace(name="Round Table")
            ),
            patch.object(api, "_health_ok", AsyncMock(return_value=(True, "ok"))),
            patch.object(api.httpx, "AsyncClient", return_value=client),
        ):
            outcome = await api.run_auto_claim(
                club_id=2,
                chat_id=-100,
                job_id=9,
                amount=Decimal("80"),
                group_title="RT / 8272-5942 / Player",
                union_shorthand="RT",
            )
        self.assertTrue(outcome.ok)
        self.assertEqual(client.posts[0][0], "https://tunnel.test/claim")
        self.assertEqual(client.posts[0][1]["label"], "Cashout")


if __name__ == "__main__":
    unittest.main()
