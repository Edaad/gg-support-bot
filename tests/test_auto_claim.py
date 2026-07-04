"""Tests for auto claim-back gating in run_auto_claim (no network)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import patch

from bot.services import clubgg_deposit_api as api


class TestRunAutoClaimGating(unittest.IsolatedAsyncioTestCase):
    async def test_not_configured_when_no_config(self):
        with patch.object(api, "load_config", return_value=None):
            outcome = await api.run_auto_claim(
                club_id=4,
                chat_id=-100,
                job_id=1,
                amount=Decimal("100"),
                group_title="RT / 2427-3267 / Samin",
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "not_configured")

    async def test_disabled_when_toggle_off(self):
        fake_cfg = object()
        with patch.object(api, "load_config", return_value=fake_cfg), patch.object(
            api, "get_auto_claim_enabled", return_value=False
        ):
            outcome = await api.run_auto_claim(
                club_id=4,
                chat_id=-100,
                job_id=1,
                amount=Decimal("100"),
                group_title="RT / 2427-3267 / Samin",
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "disabled")

    async def test_no_player_id_when_title_unparseable(self):
        fake_cfg = object()
        with patch.object(api, "load_config", return_value=fake_cfg), patch.object(
            api, "get_auto_claim_enabled", return_value=True
        ):
            outcome = await api.run_auto_claim(
                club_id=4,
                chat_id=-100,
                job_id=1,
                amount=Decimal("100"),
                group_title="just some chatty group name",
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "no_player_id")

    def test_deposit_api_configured_reflects_load_config(self):
        with patch.object(api, "load_config", return_value=None):
            self.assertFalse(api.deposit_api_configured())
        with patch.object(api, "load_config", return_value=object()):
            self.assertTrue(api.deposit_api_configured())


if __name__ == "__main__":
    unittest.main()
