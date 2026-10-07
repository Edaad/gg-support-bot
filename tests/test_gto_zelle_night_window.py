"""ClubGTO Zelle tag off from midnight until 2:00am Eastern."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from api.auth import ROLE_ADMIN, ROLE_GTO
from api.routes import v2_payment as v2
from api.schemas_v2 import ClubPaymentTierVariantUpdate
from bot.services.club_payment_v2 import variant_is_active
from bot.services.gto_zelle_night import (
    NIGHT_TAG,
    guard_night_window_activation,
    in_gto_zelle_night_window,
    sync_gto_zelle_night_window,
)
from bot.services.variant_pause import (
    apply_pause,
    card_state,
    resume_gto_zelle_variant,
)

_NY = ZoneInfo("America/New_York")


def _et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    local = datetime(year, month, day, hour, minute, tzinfo=_NY)
    return local.astimezone(timezone.utc)


AT_1230 = _et(2026, 10, 7, 0, 30)
AT_200 = _et(2026, 10, 7, 2, 0)


def _variant(**overrides):
    data = dict(
        id=9,
        method_id=4,
        tier_id=10,
        label="Starship",
        is_active=True,
        paused_until=None,
        night_release_on=None,
        weight=10,
        sort_order=0,
        response_type="text",
        response_text=None,
        response_file_id=None,
        response_caption=None,
        use_group_checkout_link=None,
        group_checkout_provider=None,
        hyperlink_text=None,
        checkout_min_amount=None,
        checkout_max_amount=None,
        tag=NIGHT_TAG,
        link=None,
        response_mode=None,
        venmo_tag=None,
        venmo_link=None,
        venmo_response_mode=None,
        cashapp_tag=None,
        cashapp_link=None,
        cashapp_response_mode=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


class _Query:
    def __init__(self, session: "_Session") -> None:
        self.session = session

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.session.club

    def all(self):
        return list(self.session.variants)


class _Session:
    def __init__(self, variants: list) -> None:
        self.club = SimpleNamespace(id=3)
        self.variants = variants
        self.flushed = 0

    def query(self, _model):
        return _Query(self)

    def flush(self) -> None:
        self.flushed += 1


class NightWindowTests(unittest.TestCase):
    def test_bounds_include_midnight_and_exclude_two(self):
        self.assertTrue(in_gto_zelle_night_window(_et(2026, 10, 7, 0, 0)))
        self.assertTrue(in_gto_zelle_night_window(AT_1230))
        self.assertTrue(in_gto_zelle_night_window(_et(2026, 10, 7, 1, 59)))
        self.assertFalse(in_gto_zelle_night_window(AT_200))
        self.assertFalse(in_gto_zelle_night_window(_et(2026, 10, 7, 15, 0)))

    def test_both_one_thirties_on_fall_back_are_inside(self):
        day = datetime(2026, 11, 1, 1, 30, tzinfo=_NY)
        first = day.replace(fold=0).astimezone(timezone.utc)
        second = day.replace(fold=1).astimezone(timezone.utc)
        self.assertTrue(in_gto_zelle_night_window(first))
        self.assertTrue(in_gto_zelle_night_window(second))
        self.assertFalse(in_gto_zelle_night_window(_et(2026, 11, 1, 2, 0)))

    def test_active_tag_is_turned_off_until_two(self):
        row = _variant()
        other = _variant(id=10, tag="other@example.com")
        session = _Session([row, other])
        sync_gto_zelle_night_window(session, AT_1230)
        self.assertFalse(row.is_active)
        self.assertEqual(row.paused_until, AT_200)
        self.assertTrue(other.is_active)
        self.assertEqual(session.flushed, 1)

    def test_second_sync_does_not_write_again(self):
        row = _variant(is_active=False, paused_until=AT_200)
        session = _Session([row])
        sync_gto_zelle_night_window(session, AT_1230)
        self.assertEqual(session.flushed, 0)

    def test_already_off_stays_off(self):
        row = _variant(is_active=False, paused_until=None)
        session = _Session([row])
        sync_gto_zelle_night_window(session, AT_1230)
        self.assertFalse(row.is_active)
        self.assertIsNone(row.paused_until)
        self.assertEqual(session.flushed, 0)
        sync_gto_zelle_night_window(session, AT_200)
        self.assertFalse(row.is_active)

    def test_forty_eight_hour_pause_is_not_overwritten(self):
        until = AT_1230 + timedelta(hours=48)
        row = _variant(is_active=False, paused_until=until)
        session = _Session([row])
        sync_gto_zelle_night_window(session, AT_1230)
        self.assertEqual(row.paused_until, until)
        sync_gto_zelle_night_window(session, AT_200)
        self.assertEqual(row.paused_until, until)
        self.assertFalse(row.is_active)

    def test_head_admin_release_stays_on_through_the_window(self):
        row = _variant(night_release_on=AT_1230.astimezone(_NY).date())
        session = _Session([row])
        sync_gto_zelle_night_window(session, AT_1230)
        self.assertTrue(row.is_active)
        self.assertIsNone(row.paused_until)
        self.assertEqual(session.flushed, 0)

    def test_release_flag_clears_at_two(self):
        row = _variant(night_release_on=AT_1230.astimezone(_NY).date())
        session = _Session([row])
        sync_gto_zelle_night_window(session, AT_200)
        self.assertIsNone(row.night_release_on)
        self.assertTrue(row.is_active)

    def test_rotation_skips_the_tag_during_the_window(self):
        row = _variant()
        with patch("bot.services.gto_zelle_night._now", return_value=AT_1230):
            self.assertFalse(variant_is_active(row))
            self.assertEqual(card_state(row, AT_1230), "disabled")
            with self.assertRaises(ValueError):
                apply_pause(row, AT_1230)
        self.assertTrue(variant_is_active(_variant(tag="other@example.com")))

    def test_released_tag_is_offered_again(self):
        row = _variant(night_release_on=AT_1230.astimezone(_NY).date())
        with patch("bot.services.gto_zelle_night._now", return_value=AT_1230):
            self.assertTrue(variant_is_active(row))
            self.assertEqual(card_state(row, AT_1230), "active")

    def test_account_manager_cannot_resume(self):
        row = _variant(is_active=False, paused_until=AT_200)
        tier = SimpleNamespace(id=10)
        with patch(
            "bot.services.variant_pause.gto_zelle_rows",
            return_value=[(row, tier)],
        ):
            with self.assertRaises(ValueError):
                resume_gto_zelle_variant(MagicMock(), 9, AT_1230)
        self.assertFalse(row.is_active)


class HeadAdminActivationTests(unittest.TestCase):
    def test_non_admin_cannot_turn_it_on(self):
        row = _variant(is_active=False, paused_until=AT_200)
        data = {"is_active": True, "paused_until": None}
        with self.assertRaises(ValueError):
            guard_night_window_activation(row, data, ROLE_GTO, AT_1230)
        self.assertFalse(row.is_active)

    def test_head_admin_can_turn_it_on(self):
        row = _variant(is_active=False, paused_until=AT_200)
        data = {"is_active": True, "paused_until": None}
        guard_night_window_activation(row, data, ROLE_ADMIN, AT_1230)
        self.assertEqual(row.night_release_on, AT_1230.astimezone(_NY).date())

    def test_saving_while_off_keeps_the_two_am_return(self):
        row = _variant(is_active=False, paused_until=AT_200)
        data = {"is_active": False, "paused_until": None}
        guard_night_window_activation(row, data, ROLE_GTO, AT_1230)
        self.assertNotIn("paused_until", data)
        self.assertIsNone(row.night_release_on)

    def test_turning_off_after_a_release_does_not_come_back(self):
        row = _variant(night_release_on=AT_1230.astimezone(_NY).date())
        data = {"is_active": False, "paused_until": None}
        guard_night_window_activation(row, data, ROLE_ADMIN, AT_1230)
        self.assertIsNone(row.night_release_on)
        self.assertIsNone(data["paused_until"])

    def test_variant_editor_rejects_gto_during_the_window(self):
        row = _variant(is_active=False, paused_until=AT_200)
        tier = SimpleNamespace(id=10, label="Default", method_id=4, tiers=[])
        method = SimpleNamespace(id=4, slug="zelle", club_id=7, tiers=[])
        token = v2._v2_role.set(ROLE_GTO)
        try:
            with (
                patch.object(v2, "_get_variant", return_value=row),
                patch.object(v2, "_get_method", return_value=method),
                patch.object(v2, "_get_tier", return_value=tier),
                patch("bot.services.variant_pause.release_expired_variant_pauses"),
                patch("bot.services.gto_zelle_night._now", return_value=AT_1230),
                self.assertRaises(HTTPException) as caught,
            ):
                v2.update_variant(
                    row.id,
                    ClubPaymentTierVariantUpdate(is_active=True),
                    MagicMock(),
                )
        finally:
            v2._v2_role.reset(token)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertFalse(row.is_active)
        self.assertEqual(row.paused_until, AT_200)

    def test_variant_editor_head_admin_turns_it_on(self):
        row = _variant(is_active=False, paused_until=AT_200)
        tier = SimpleNamespace(id=10, label="Default", method_id=4)
        method = SimpleNamespace(id=4, slug="zelle", club_id=7, tiers=[])
        token = v2._v2_role.set(ROLE_ADMIN)
        try:
            with (
                patch.object(v2, "_get_variant", return_value=row),
                patch.object(v2, "_get_method", return_value=method),
                patch.object(v2, "_get_tier", return_value=tier),
                patch("bot.services.variant_pause.release_expired_variant_pauses"),
                patch("bot.services.gto_zelle_night._now", return_value=AT_1230),
            ):
                v2.update_variant(
                    row.id,
                    ClubPaymentTierVariantUpdate(is_active=True),
                    MagicMock(),
                )
        finally:
            v2._v2_role.reset(token)
        self.assertTrue(row.is_active)
        self.assertIsNone(row.paused_until)
        self.assertEqual(row.night_release_on, AT_1230.astimezone(_NY).date())


if __name__ == "__main__":
    unittest.main()
