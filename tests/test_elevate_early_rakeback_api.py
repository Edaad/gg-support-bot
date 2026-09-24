"""Tests for the Elevate (aon-beta) early rakeback quote/record client."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from bot.services import elevate_early_rakeback_api as api

_QUOTE_OK = {
    "eligible": True,
    "reason": "ok",
    "ggId": "82725942",
    "displayId": "8272-5942",
    "nickname": "SomePlayer",
    "nicknameResolved": True,
    "memberType": "player",
    "source": "custom_player",
    "dealType": "flat",
    "percentage": 60,
    "grossAmount": 540,
    "taxRebateAmount": -60,
    "totalAlreadyGiven": 0,
    "remaining": 540,
    "belowMinimum": False,
    "minimumThreshold": 50,
    "onTrial": False,
    "warnings": [],
    "displayDecimalPlaces": 2,
}


class _FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response) -> None:
        self._response = response
        self.calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, {"params": params, "headers": headers}))
        return self._response

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, {"json": json, "headers": headers}))
        return self._response

    async def delete(self, url, params=None, json=None, headers=None):
        self.calls.append(
            ("DELETE", url, {"params": params, "json": json, "headers": headers})
        )
        return self._response


def _cfg():
    return SimpleNamespace(
        base_url="https://aon.test/api", api_key="secret", timeout_sec=30.0
    )


class QuoteTests(unittest.IsolatedAsyncioTestCase):
    async def _quote(self, response, **kwargs):
        client = _FakeAsyncClient(response)
        with (
            patch.object(api, "load_config", return_value=_cfg()),
            patch.object(api.httpx, "AsyncClient", return_value=client),
        ):
            result = await api.quote_early_rakeback(
                club_slug="Round-Table",
                gg_player_id="8272-5942",
                rake=Decimal("1000"),
                pl=Decimal("-400"),
                **kwargs,
            )
        return result, client

    async def test_eligible_quote_is_parsed(self) -> None:
        result, client = await self._quote(_FakeResponse(200, _QUOTE_OK))

        self.assertTrue(result.ok)
        quote = result.quote
        self.assertTrue(quote.eligible)
        self.assertEqual(quote.reason, "ok")
        self.assertEqual(quote.remaining, Decimal("540"))
        self.assertFalse(quote.below_minimum)
        self.assertEqual(quote.minimum_threshold, Decimal("50"))
        self.assertEqual(quote.member_type, "player")
        self.assertEqual(quote.display_decimal_places, 2)
        self.assertEqual(quote.warnings, ())

        method, url, kwargs = client.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(
            url, "https://aon.test/api/round-table/early-rakeback/bot/quote"
        )
        self.assertEqual(kwargs["headers"], {"X-Internal-Api-Key": "secret"})
        self.assertEqual(kwargs["params"]["rake"], "1000.00")
        self.assertEqual(kwargs["params"]["pl"], "-400.00")

    async def test_pl_is_always_sent(self) -> None:
        _result, client = await self._quote(_FakeResponse(200, _QUOTE_OK))
        self.assertIn("pl", client.calls[0][2]["params"])

    async def test_below_minimum_still_succeeds(self) -> None:
        payload = {**_QUOTE_OK, "remaining": 12.5, "belowMinimum": True}
        result, _client = await self._quote(_FakeResponse(200, payload))

        self.assertTrue(result.ok)
        self.assertTrue(result.quote.below_minimum)
        self.assertEqual(result.quote.remaining, Decimal("12.5"))

    async def test_warnings_are_captured_not_blocking(self) -> None:
        payload = {**_QUOTE_OK, "warnings": ["excluded_manual", "multiple_lists"]}
        result, _client = await self._quote(_FakeResponse(200, payload))

        self.assertTrue(result.ok)
        self.assertEqual(result.quote.warnings, ("excluded_manual", "multiple_lists"))

    async def test_ineligible_reasons_are_preserved(self) -> None:
        for reason in (
            api.REASON_NOTHING_REMAINING,
            api.REASON_NOT_LISTED_STANDARD_DISABLED,
            api.REASON_NON_POSITIVE_AMOUNT,
        ):
            payload = {
                **_QUOTE_OK,
                "eligible": False,
                "reason": reason,
                "remaining": 0,
                "source": None,
            }
            result, _client = await self._quote(_FakeResponse(200, payload))
            self.assertTrue(result.ok)
            self.assertFalse(result.quote.eligible)
            self.assertEqual(result.quote.reason, reason)

    async def test_documented_error_codes(self) -> None:
        cases = [
            (400, {"error": "gg_player_id missing", "code": "invalid_player_id"}),
            (400, {"error": "rake must be > 0", "code": "invalid_rake"}),
            (400, {"error": "pl is required", "code": "pl_required"}),
            (404, {"error": "Unknown club", "code": "club_not_found"}),
            (401, {"error": "Invalid internal API key"}),
            (503, {"error": "Internal API not configured"}),
        ]
        for status, payload in cases:
            result, _client = await self._quote(_FakeResponse(status, payload))
            self.assertFalse(result.ok, payload)
            self.assertTrue(result.error_code)
            self.assertTrue(result.detail)

    async def test_invalid_rake_code_surfaces(self) -> None:
        result, _client = await self._quote(
            _FakeResponse(400, {"error": "rake must be > 0", "code": "invalid_rake"})
        )
        self.assertEqual(result.error_code, "invalid_rake")

    async def test_transport_failure_never_raises(self) -> None:
        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("boom")

            async def __aexit__(self, *_exc):
                return False

        with (
            patch.object(api, "load_config", return_value=_cfg()),
            patch.object(api.httpx, "AsyncClient", return_value=_Boom()),
        ):
            result = await api.quote_early_rakeback(
                club_slug="round-table",
                gg_player_id="8272-5942",
                rake=Decimal("100"),
                pl=Decimal("0"),
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "request_failed")

    async def test_not_configured(self) -> None:
        with patch.object(api, "load_config", return_value=None):
            result = await api.quote_early_rakeback(
                club_slug="round-table",
                gg_player_id="8272-5942",
                rake=Decimal("100"),
                pl=Decimal("0"),
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_configured")


class RecordTests(unittest.IsolatedAsyncioTestCase):
    async def _record(self, response, **kwargs):
        client = _FakeAsyncClient(response)
        with (
            patch.object(api, "load_config", return_value=_cfg()),
            patch.object(api.httpx, "AsyncClient", return_value=client),
        ):
            result = await api.record_early_rakeback(
                club_slug="round-table",
                gg_player_id="8272-5942",
                rake=Decimal("1000"),
                pl=Decimal("-400"),
                expected_amount=Decimal("540"),
                idempotency_key="tg:-100:12345",
                **kwargs,
            )
        return result, client

    async def test_created(self) -> None:
        payload = {
            "recorded": True,
            "duplicate": False,
            "code": "ok",
            "amountRecorded": 540,
            "entry": {"_id": "entry1", "totalGiven": 540},
            "record": {"_id": "rec1", "calculatedAmount": 540},
            "quote": _QUOTE_OK,
        }
        result, client = await self._record(_FakeResponse(201, payload))

        self.assertTrue(result.ok)
        self.assertFalse(result.duplicate)
        self.assertEqual(result.code, "ok")
        self.assertEqual(result.amount_recorded, Decimal("540"))
        self.assertEqual(result.total_given, Decimal("540"))
        self.assertEqual(result.record_id, "rec1")
        self.assertEqual(result.entry_id, "entry1")

        method, url, kwargs = client.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(
            url, "https://aon.test/api/round-table/early-rakeback/bot/record"
        )
        self.assertEqual(kwargs["json"]["expected_amount"], "540.00")
        self.assertEqual(kwargs["json"]["idempotency_key"], "tg:-100:12345")
        self.assertNotIn("allow_below_minimum", kwargs["json"])

    async def test_duplicate_replay_counts_as_recorded(self) -> None:
        payload = {
            "recorded": False,
            "duplicate": True,
            "code": "already_recorded",
            "amountRecorded": 540,
            "entry": {"_id": "entry1", "totalGiven": 540},
            "record": {"_id": "rec1"},
        }
        result, _client = await self._record(_FakeResponse(200, payload))

        self.assertTrue(result.ok)
        self.assertTrue(result.duplicate)
        self.assertEqual(result.code, api.CODE_ALREADY_RECORDED)

    async def test_amount_changed_returns_fresh_quote(self) -> None:
        fresh = {**_QUOTE_OK, "remaining": 610}
        payload = {
            "error": "Amount changed",
            "code": "amount_changed",
            "expectedAmount": 540,
            "quote": fresh,
        }
        result, _client = await self._record(_FakeResponse(409, payload))

        self.assertFalse(result.ok)
        self.assertEqual(result.code, api.CODE_AMOUNT_CHANGED)
        self.assertEqual(result.quote.remaining, Decimal("610"))

    async def test_record_in_progress(self) -> None:
        result, _client = await self._record(
            _FakeResponse(409, {"error": "in progress", "code": "record_in_progress"})
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, api.CODE_RECORD_IN_PROGRESS)

    async def test_below_minimum_threshold(self) -> None:
        payload = {
            "error": "Below minimum",
            "code": "below_minimum_threshold",
            "quote": {**_QUOTE_OK, "remaining": 12, "belowMinimum": True},
        }
        result, _client = await self._record(_FakeResponse(422, payload))

        self.assertFalse(result.ok)
        self.assertEqual(result.code, api.CODE_BELOW_MINIMUM)
        self.assertEqual(result.quote.minimum_threshold, Decimal("50"))

    async def test_nothing_payable_422_codes(self) -> None:
        for code in (
            api.REASON_NOT_LISTED_STANDARD_DISABLED,
            api.REASON_NON_POSITIVE_AMOUNT,
            api.REASON_NOTHING_REMAINING,
        ):
            result, _client = await self._record(
                _FakeResponse(422, {"error": code, "code": code})
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, code)

    async def test_duplicate_nickname_conflict(self) -> None:
        result, _client = await self._record(
            _FakeResponse(
                409, {"error": "nickname taken", "code": "duplicate_nickname"}
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "duplicate_nickname")

    async def test_allow_below_minimum_is_opt_in(self) -> None:
        _result, client = await self._record(
            _FakeResponse(201, {"code": "ok", "amountRecorded": 12}),
            allow_below_minimum=True,
        )
        self.assertTrue(client.calls[0][2]["json"]["allow_below_minimum"])

    async def test_transport_failure_never_raises(self) -> None:
        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("boom")

            async def __aexit__(self, *_exc):
                return False

        with (
            patch.object(api, "load_config", return_value=_cfg()),
            patch.object(api.httpx, "AsyncClient", return_value=_Boom()),
        ):
            result = await api.record_early_rakeback(
                club_slug="round-table",
                gg_player_id="8272-5942",
                rake=Decimal("100"),
                pl=Decimal("0"),
                expected_amount=Decimal("50"),
                idempotency_key="k",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "request_failed")


class DeleteTests(unittest.IsolatedAsyncioTestCase):
    async def _delete(self, response, **kwargs):
        client = _FakeAsyncClient(response)
        with (
            patch.object(api, "load_config", return_value=_cfg()),
            patch.object(api.httpx, "AsyncClient", return_value=client),
        ):
            result = await api.delete_early_rakeback(
                club_slug="round-table",
                idempotency_key="tg:-100:12345",
                **kwargs,
            )
        return result, client

    async def test_deleted(self) -> None:
        payload = {
            "deleted": True,
            "code": "ok",
            "amountRemoved": 540,
            "entryDeleted": False,
            "record": {"_id": "rec1", "calculatedAmount": 540, "source": "bot"},
        }
        result, client = await self._delete(_FakeResponse(200, payload))

        self.assertTrue(result.ok)
        self.assertEqual(result.code, "ok")
        self.assertEqual(result.amount_removed, Decimal("540"))
        self.assertFalse(result.entry_deleted)
        self.assertEqual(result.record_id, "rec1")

        method, url, kwargs = client.calls[0]
        self.assertEqual(method, "DELETE")
        self.assertEqual(
            url, "https://aon.test/api/round-table/early-rakeback/bot/record"
        )
        self.assertEqual(kwargs["params"]["idempotency_key"], "tg:-100:12345")
        self.assertEqual(kwargs["headers"], {"X-Internal-Api-Key": "secret"})

    async def test_already_deleted_is_ok(self) -> None:
        result, _client = await self._delete(
            _FakeResponse(
                200, {"deleted": False, "code": "already_deleted", "amountRemoved": 0}
            )
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.code, api.CODE_ALREADY_DELETED)

    async def test_sends_record_id_alongside_the_key(self) -> None:
        _result, client = await self._delete(
            _FakeResponse(200, {"deleted": True, "code": "ok"}),
            record_id="rec1",
        )
        self.assertEqual(client.calls[0][2]["params"]["record_id"], "rec1")

    async def test_not_bot_record(self) -> None:
        result, _client = await self._delete(
            _FakeResponse(403, {"error": "not a bot record", "code": "not_bot_record"})
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, api.CODE_NOT_BOT_RECORD)

    async def test_missing_reference_never_hits_the_network(self) -> None:
        with (
            patch.object(api, "load_config", return_value=_cfg()),
            patch.object(api.httpx, "AsyncClient") as client_cls,
        ):
            result = await api.delete_early_rakeback(club_slug="round-table")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, api.CODE_MISSING_RECORD_REFERENCE)
        client_cls.assert_not_called()

    async def test_transport_failure_never_raises(self) -> None:
        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("boom")

            async def __aexit__(self, *_exc):
                return False

        with (
            patch.object(api, "load_config", return_value=_cfg()),
            patch.object(api.httpx, "AsyncClient", return_value=_Boom()),
        ):
            result = await api.delete_early_rakeback(
                club_slug="round-table", idempotency_key="k"
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "request_failed")


class AgencyTests(unittest.IsolatedAsyncioTestCase):
    async def _lookup(self, response):
        client = _FakeAsyncClient(response)
        with (
            patch.object(api, "load_config", return_value=_cfg()),
            patch.object(api.httpx, "AsyncClient", return_value=client),
        ):
            result = await api.lookup_early_rakeback_agency(
                club_slug="ClubGTO",
                gg_player_id="3814-6937",
            )
        return result, client

    async def test_excluded_super_agent_downline_is_parsed(self) -> None:
        payload = {
            "ggId": "38146937",
            "displayId": "3814-6937",
            "nickname": "SokoBachi",
            "found": True,
            "reason": "ok",
            "weekStart": "2026-09-14",
            "weekEnd": "2026-09-20",
            "agent": None,
            "superAgent": {
                "ggId": "98166298",
                "displayId": "9816-6298",
                "nickname": "MrFreeze888",
            },
            "standardPlayerRateEnabled": True,
            "excluded": True,
            "excludeReasons": ["excluded_by_super_agent"],
        }
        result, client = await self._lookup(_FakeResponse(200, payload))

        self.assertTrue(result.ok)
        agency = result.agency
        self.assertTrue(agency.found)
        self.assertEqual(agency.reason, api.AGENCY_REASON_OK)
        self.assertTrue(agency.excluded)
        self.assertEqual(agency.exclude_reasons, ("excluded_by_super_agent",))
        self.assertIsNone(agency.agent)
        self.assertEqual(agency.super_agent.gg_id, "98166298")
        self.assertTrue(agency.standard_player_rate_enabled)

        method, url, kwargs = client.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "https://aon.test/api/clubgto/early-rakeback/bot/agency")
        self.assertEqual(kwargs["headers"], {"X-Internal-Api-Key": "secret"})
        self.assertEqual(kwargs["params"]["gg_player_id"], "3814-6937")

    async def test_no_week_history(self) -> None:
        payload = {
            "ggId": "38146937",
            "displayId": "3814-6937",
            "nickname": None,
            "found": False,
            "reason": "no_week_history",
            "weekStart": None,
            "weekEnd": None,
            "agent": None,
            "superAgent": None,
            "standardPlayerRateEnabled": True,
            "excluded": False,
            "excludeReasons": [],
        }
        result, _client = await self._lookup(_FakeResponse(200, payload))
        self.assertTrue(result.ok)
        self.assertFalse(result.agency.found)
        self.assertEqual(result.agency.reason, api.AGENCY_REASON_NO_WEEK_HISTORY)

    async def test_documented_error_codes(self) -> None:
        cases = [
            (400, {"error": "gg_player_id missing", "code": "invalid_player_id"}),
            (404, {"error": "Unknown club", "code": "club_not_found"}),
            (401, {"error": "Invalid internal API key"}),
        ]
        for status, payload in cases:
            result, _client = await self._lookup(_FakeResponse(status, payload))
            self.assertFalse(result.ok, payload)
            self.assertTrue(result.error_code)

    async def test_transport_failure_never_raises(self) -> None:
        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("boom")

            async def __aexit__(self, *_exc):
                return False

        with (
            patch.object(api, "load_config", return_value=_cfg()),
            patch.object(api.httpx, "AsyncClient", return_value=_Boom()),
        ):
            result = await api.lookup_early_rakeback_agency(
                club_slug="clubgto",
                gg_player_id="3814-6937",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "request_failed")

    async def test_not_configured(self) -> None:
        with patch.object(api, "load_config", return_value=None):
            result = await api.lookup_early_rakeback_agency(
                club_slug="clubgto",
                gg_player_id="3814-6937",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_configured")


class ConfigTests(unittest.TestCase):
    def test_configured_flag(self) -> None:
        with patch.object(api, "load_config", return_value=_cfg()):
            self.assertTrue(api.early_rakeback_api_configured())
        with patch.object(api, "load_config", return_value=None):
            self.assertFalse(api.early_rakeback_api_configured())

    def test_trailing_slash_stripped(self) -> None:
        env = {
            "AON_BETA_BASE_URL": "https://aon.test/api/",
            "AON_BETA_INTERNAL_API_KEY": "secret",
        }
        with patch.dict(api.os.environ, env, clear=False):
            cfg = api.load_config()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.base_url, "https://aon.test/api")

    def test_missing_key_is_unconfigured(self) -> None:
        env = {
            "AON_BETA_BASE_URL": "https://aon.test/api",
            "AON_BETA_INTERNAL_API_KEY": "",
        }
        with patch.dict(api.os.environ, env, clear=False):
            self.assertIsNone(api.load_config())


if __name__ == "__main__":
    unittest.main()
