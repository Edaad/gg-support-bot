"""Tests for automated cashout: handle validation, claim union override, and
the /cashout mode-precedence (simple > auto > canned)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import cashout_handle_validation as cv
from bot.services import clubgg_deposit_api as api
from bot.handlers import cashout as co


class HandleValidationTests(unittest.TestCase):
    def test_venmo_accepts_handle_and_link(self):
        self.assertEqual(cv.validate_cashout_handle("venmo", "@John_Doe"), "@john_doe")
        self.assertEqual(
            cv.validate_cashout_handle("venmo", "pay https://venmo.com/u/jane"),
            "@jane",
        )

    def test_venmo_rejects_bare_word(self):
        self.assertIsNone(cv.validate_cashout_handle("venmo", "my venmo is john"))

    def test_cashapp_accepts_tag_and_link(self):
        self.assertEqual(cv.validate_cashout_handle("cashapp", "$Johnny"), "$johnny")
        self.assertEqual(
            cv.validate_cashout_handle("cashapp", "https://cash.app/$jane"), "$jane"
        )

    def test_cashapp_rejects_bare_word(self):
        self.assertIsNone(cv.validate_cashout_handle("cashapp", "cashapp john"))

    def test_zelle_accepts_phone_and_email(self):
        self.assertEqual(
            cv.validate_cashout_handle("zelle", "555-123-4567"), "5551234567"
        )
        self.assertEqual(
            cv.validate_cashout_handle("zelle", "Me@Example.com"), "me@example.com"
        )

    def test_zelle_rejects_prose(self):
        self.assertIsNone(cv.validate_cashout_handle("zelle", "please call me"))

    def test_crypto_accepts_address_tokens(self):
        self.assertEqual(
            cv.validate_cashout_handle(
                "crypto", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
            ),
            "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
        )
        # ETH addresses are case-sensitive (checksum) — preserved.
        self.assertEqual(
            cv.validate_cashout_handle(
                "crypto", "0x52908400098527886E0F7030069857D2E4169EE7"
            ),
            "0x52908400098527886E0F7030069857D2E4169EE7",
        )

    def test_crypto_rejects_sentence(self):
        self.assertIsNone(cv.validate_cashout_handle("crypto", "send it here please"))

    def test_paypal_accepts_email_and_link(self):
        self.assertEqual(
            cv.validate_cashout_handle("paypal", "me@example.com"), "me@example.com"
        )
        self.assertEqual(
            cv.validate_cashout_handle("paypal", "https://paypal.me/john"),
            "paypal.me/john",
        )

    def test_unsupported_slug(self):
        self.assertFalse(cv.supported_cashout_slug("stripe"))
        self.assertIsNone(cv.validate_cashout_handle("stripe", "anything"))
        self.assertTrue(cv.supported_cashout_slug("venmo"))


class RunAutoClaimUnionOverrideTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, union):
        cfg = SimpleNamespace(timeout_sec=5)
        with patch.object(api, "load_config", return_value=cfg), patch.object(
            api, "get_auto_claim_enabled", return_value=True
        ), patch.object(
            api, "get_club_by_id", return_value=SimpleNamespace(name="Round Table")
        ), patch.object(
            api, "_health_ok", new=AsyncMock(return_value=(False, "down"))
        ):
            return await api.run_auto_claim(
                club_id=2,
                chat_id=-100,
                job_id=0,
                amount=Decimal("100"),
                group_title="RT AT / 2427-3267 / Samin",
                union_shorthand=union,
                request_id="auto-cashout-test",
            )

    async def test_at_override_maps_to_aces_table(self):
        outcome = await self._run("AT")
        self.assertEqual(outcome.status, "no_machine")
        self.assertEqual(outcome.clubgg_club, "Aces Table")

    async def test_rt_override_maps_to_round_table(self):
        outcome = await self._run("RT")
        self.assertEqual(outcome.status, "no_machine")
        self.assertEqual(outcome.clubgg_club, "Round Table")


def _make_update(user_id=555):
    message = SimpleNamespace(reply_text=AsyncMock())
    chat = SimpleNamespace(id=-1001, type="supergroup", title="RT / 1-2 / P")
    user = SimpleNamespace(id=user_id)
    return SimpleNamespace(
        effective_message=message,
        effective_chat=chat,
        effective_user=user,
        message=message,
    )


class CashoutModePrecedenceTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self):
        return SimpleNamespace(chat_data={}, user_data={}, job_queue=None)

    async def _entry(self, *, simple, auto, max_amt=None, soft=None):
        update = _make_update()
        context = self._ctx()
        patches = [
            patch.object(
                co, "block_if_group_money_flow_active", new=AsyncMock(return_value=False)
            ),
            patch.object(co, "is_update_too_old", return_value=False),
            patch.object(co, "get_club_for_chat", return_value=7),
            patch.object(co, "update_group_name", return_value=None),
            patch.object(co, "is_club_staff", return_value=False),
            patch.object(co, "check_cashout_eligibility", return_value=(True, None)),
            patch.object(co, "mark_active_flow", return_value=None),
            patch.object(co, "get_club_allows_multi_cashout", return_value=False),
            patch.object(co, "_cashout_amount_prompt_kwargs", return_value={}),
            patch.object(co, "record_activity", return_value=None),
            patch.object(co, "_cleanup_after_flow", return_value=None),
            patch.object(co, "_send_simple_response", new=AsyncMock()),
            patch.object(co, "get_auto_cashout_enabled", return_value=auto),
            patch.object(co, "get_cashout_max_amount", return_value=max_amt),
            patch.object(co, "get_cashout_soft_limit", return_value=soft),
            patch.object(
                co,
                "get_club_simple_mode",
                return_value=({"response_type": "text"} if simple else None),
            ),
            patch.object(co.popup_keyboard_svc, "prepare_flow_entry_keyboard"),
            patch.object(
                co.popup_keyboard_svc, "pop_strip_reply_markup", return_value=None
            ),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        state = await co.cashout_entry(update, context)
        return state, context

    async def test_auto_on_returns_auto_amount(self):
        state, context = await self._entry(simple=False, auto=True)
        self.assertEqual(state, co.CASHOUT_AUTO_AMOUNT)
        self.assertTrue(context.chat_data.get("cashout_auto"))

    async def test_auto_off_returns_regular_amount(self):
        state, context = await self._entry(simple=False, auto=False)
        self.assertEqual(state, co.CASHOUT_AMOUNT)
        self.assertNotIn("cashout_auto", context.chat_data)

    async def test_simple_mode_wins_over_auto(self):
        # simple on + auto on, no max/soft -> simple immediate response + END
        from telegram.ext import ConversationHandler

        state, context = await self._entry(simple=True, auto=True)
        self.assertEqual(state, ConversationHandler.END)
        self.assertNotIn("cashout_auto", context.chat_data)


if __name__ == "__main__":
    unittest.main()
