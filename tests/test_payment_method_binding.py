"""Unit tests for payment method binding helpers."""

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from bot.services.payment_method_binding import (
    ATTEMPT_STATUS_PENDING,
    BIND_KIND_MEMO_EMOJI,
    BIND_KIND_SPECIAL_AMOUNT,
    allocate_setup_memo_code,
    allocate_setup_amount_cents,
    bind_mode_for_method,
    effective_min_cents,
    extract_venmo_handle_from_text,
    extract_venmo_url,
    extract_zelle_details,
    find_existing_venmo_link_for_setup,
    format_first_time_memo_setup_message,
    format_first_time_venmo_setup_message,
    get_last_bound_deposit_at,
    match_pending_memo_setup_in_session,
    unbind_chat_from_method,
    venmo_special_amount_binding_enabled,
    _memo_contains_code,
)


class TestBindModeForMethod(unittest.TestCase):
    def test_creator_club_special_amount(self):
        with patch("bot.runtime_config.is_test_bot_worker", return_value=True):
            self.assertEqual(
                bind_mode_for_method("venmo", club_name="Creator Club"),
                BIND_KIND_SPECIAL_AMOUNT,
            )
            self.assertEqual(
                bind_mode_for_method("zelle", club_name="Creator Club"),
                BIND_KIND_SPECIAL_AMOUNT,
            )

    def test_round_table_memo_emoji(self):
        with patch("bot.runtime_config.is_test_bot_worker", return_value=True):
            self.assertEqual(
                bind_mode_for_method("venmo", club_name="Round Table"),
                BIND_KIND_MEMO_EMOJI,
            )
            self.assertEqual(
                bind_mode_for_method("zelle", club_name="Round Table"),
                BIND_KIND_MEMO_EMOJI,
            )

    def test_unconfigured_club_disabled(self):
        with patch("bot.runtime_config.is_test_bot_worker", return_value=True):
            self.assertIsNone(bind_mode_for_method("venmo", club_name="ClubGTO"))
            self.assertIsNone(bind_mode_for_method("venmo"))

    def test_production_disabled(self):
        with patch("bot.runtime_config.is_test_bot_worker", return_value=False):
            self.assertIsNone(
                bind_mode_for_method("venmo", club_name="Creator Club")
            )


class TestVenmoSpecialAmountGating(unittest.TestCase):
    def test_off_on_production_worker(self):
        with patch("bot.runtime_config.is_test_bot_worker", return_value=False):
            self.assertFalse(venmo_special_amount_binding_enabled())

    def test_on_test_bot_worker(self):
        with patch("bot.runtime_config.is_test_bot_worker", return_value=True):
            self.assertTrue(venmo_special_amount_binding_enabled())


class TestUnbind(unittest.TestCase):
    def test_unbind_delegates(self):
        with patch(
            "bot.services.payment_method_binding.get_db"
        ) as mock_get_db:
            session = MagicMock()
            mock_get_db.return_value.__enter__.return_value = session
            row = MagicMock()
            row.telegram_chat_id = -1001
            row.payment_method_slug = "venmo"
            session.query.return_value.filter_by.return_value.one_or_none.return_value = (
                row
            )
            self.assertTrue(unbind_chat_from_method(-1001, "venmo"))
            session.delete.assert_called_once_with(row)


class TestEffectiveMinCents(unittest.TestCase):
    def test_max_of_method_and_tier(self):
        self.assertEqual(
            effective_min_cents(method_min=Decimal("100"), tier_min=Decimal("50")),
            10000,
        )
        self.assertEqual(
            effective_min_cents(method_min=Decimal("50"), tier_min=Decimal("100")),
            10000,
        )

    def test_method_only(self):
        self.assertEqual(
            effective_min_cents(method_min=Decimal("100"), tier_min=None),
            10000,
        )


class TestVenmoExtract(unittest.TestCase):
    def test_url_and_handle(self):
        text = "Venmo: https://venmo.com/u/godfather4444\n• emoji"
        self.assertEqual(
            extract_venmo_url(text),
            "https://venmo.com/u/godfather4444",
        )
        self.assertEqual(
            extract_venmo_handle_from_text(text),
            "@godfather4444",
        )


class TestAllocateSetupMemoCode(unittest.TestCase):
    def test_first_pending_gets_first_code(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.scalar.return_value = 0
        code = allocate_setup_memo_code(session, variant_id=1)
        self.assertEqual(code, "GG-FLOP")

    def test_second_pending_gets_second_code(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.scalar.return_value = 1
        code = allocate_setup_memo_code(session, variant_id=1)
        self.assertEqual(code, "GG-TURN")

    def test_exhausted_pool_raises(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.scalar.return_value = 10
        with self.assertRaises(ValueError):
            allocate_setup_memo_code(session, variant_id=1)


class TestExistingVenmoLink(unittest.TestCase):
    def test_finds_payer_binding_first(self):
        from db.models import GroupPaymentMethodBinding, VenmoPayerBinding

        payer_row = VenmoPayerBinding(
            payer_name_normalized="moshe toussoun",
            venmo_handle="@godfather4444",
            telegram_chat_id=-1001,
            club_id=2,
        )

        session = MagicMock()

        def _query(model):
            q = MagicMock()
            if model is VenmoPayerBinding:
                q.filter_by.return_value.one_or_none.return_value = payer_row
            elif model is GroupPaymentMethodBinding:
                q.filter_by.return_value.one_or_none.return_value = None
            return q

        session.query.side_effect = _query

        found = find_existing_venmo_link_for_setup(
            session,
            payer_name="Moshe Toussoun",
            setup_chat_id=-1002,
        )
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.linked_chat_id, -1001)
        self.assertEqual(found.via, "payer_binding")

    def test_falls_back_to_group_binding(self):
        from db.models import GroupPaymentMethodBinding, VenmoPayerBinding

        group_row = GroupPaymentMethodBinding(
            telegram_chat_id=-1002,
            club_id=2,
            payment_method_slug="venmo",
            bound_via="memo_emoji",
        )

        session = MagicMock()

        def _query(model):
            q = MagicMock()
            if model is VenmoPayerBinding:
                q.filter_by.return_value.one_or_none.return_value = None
            elif model is GroupPaymentMethodBinding:
                q.filter_by.return_value.one_or_none.return_value = group_row
            return q

        session.query.side_effect = _query

        found = find_existing_venmo_link_for_setup(
            session,
            payer_name="New Player",
            setup_chat_id=-1002,
        )
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.linked_chat_id, -1002)
        self.assertEqual(found.via, "group_binding")


class TestLastBoundDeposit(unittest.TestCase):
    def test_returns_latest_matching_payment(self):
        from datetime import datetime, timezone

        from db.models import VenmoPayment

        older = VenmoPayment(
            id=1,
            payer_name="Moshe Toussoun",
            amount_cents=10000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            telegram_chat_id=-1001,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        newer = VenmoPayment(
            id=2,
            payer_name="Moshe Toussoun",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            telegram_chat_id=-1001,
            created_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
        )

        session = MagicMock()
        session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            newer,
            older,
        ]

        found = get_last_bound_deposit_at(
            session,
            payer_name="Moshe Toussoun",
            telegram_chat_id=-1001,
            exclude_payment_id=99,
        )
        self.assertEqual(found, newer.created_at)


class TestMemoContainsCode(unittest.TestCase):
    def test_finds_code_in_memo(self):
        self.assertTrue(_memo_contains_code("Payment GG-FLOP thanks", "GG-FLOP"))
        self.assertTrue(_memo_contains_code("payment gg-flop", "GG-FLOP"))
        self.assertFalse(_memo_contains_code("Payment thanks", "GG-FLOP"))


class TestZelleExtract(unittest.TestCase):
    def test_email_and_name(self):
        text = (
            "Zelle Email: coachingg444@gmail.com\n"
            "Zelle Name: CONCORD CONSULTING AGENCY, INC\n"
        )
        email, name = extract_zelle_details(text)
        self.assertEqual(email, "coachingg444@gmail.com")
        self.assertIn("CONCORD", name or "")


class TestMemoSetupMessage(unittest.TestCase):
    def test_venmo_memo_html(self):
        text = format_first_time_memo_setup_message(
            payment_method_slug="venmo",
            variant_response_text="Venmo: https://venmo.com/u/testuser",
        )
        self.assertIn("FIRST-TIME VENMO SETUP", text)
        self.assertIn("Copy and paste the code above", text)
        self.assertIn("venmo.com/u/testuser", text)

    def test_zelle_memo_html(self):
        text = format_first_time_memo_setup_message(
            payment_method_slug="zelle",
            variant_response_text=(
                "Zelle Email: a@b.com\nZelle Name: ACME INC\n"
            ),
        )
        self.assertIn("FIRST-TIME ZELLE SETUP", text)
        self.assertIn("a@b.com", text)


class TestAllocateSetupAmount(unittest.TestCase):
    def test_first_pending_gets_cent_below_chosen_deposit(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.scalar.return_value = 0
        cents = allocate_setup_amount_cents(
            session, variant_id=1, deposit_amount_cents=9000
        )
        self.assertEqual(cents, 8999)

    def test_second_pending_decrements(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.scalar.return_value = 1
        cents = allocate_setup_amount_cents(
            session, variant_id=1, deposit_amount_cents=9000
        )
        self.assertEqual(cents, 8998)

    def test_deposit_too_small_raises(self):
        session = MagicMock()
        with self.assertRaises(ValueError):
            allocate_setup_amount_cents(
                session, variant_id=1, deposit_amount_cents=1
            )


class TestSetupMessage(unittest.TestCase):
    def test_includes_amounts_html(self):
        text = format_first_time_venmo_setup_message(
            setup_amount_cents=8999,
            chosen_amount_cents=9000,
            variant_response_text="Venmo: https://venmo.com/u/club-round",
        )
        self.assertIn("$89.99", text)
        self.assertIn("$90.00", text)
        self.assertIn("<code>$89.99</code>", text)
        self.assertIn("Pay this exact amount only", text)
        self.assertIn("FIRST-TIME VENMO SETUP", text)
        self.assertIn("venmo.com/u/club-round", text)

    def test_plain_fallback_emphasizes_exact_amount(self):
        text = format_first_time_venmo_setup_message(
            setup_amount_cents=8999,
            chosen_amount_cents=9000,
            variant_response_text="Venmo: https://venmo.com/u/club-round",
            use_html=False,
        )
        self.assertIn("PAY THIS EXACT AMOUNT ONLY", text)
        self.assertIn("$89.99", text)
        self.assertNotIn("<b>", text)


class TestMatchPendingInSession(unittest.TestCase):
    def test_match_finds_attempt_by_handle(self):
        from datetime import datetime, timedelta, timezone

        from db.models import PaymentMethodBindAttempt

        attempt = PaymentMethodBindAttempt(
            id=7,
            telegram_chat_id=-1001,
            club_id=2,
            payment_method_slug="venmo",
            method_id=10,
            variant_id=3,
            bind_kind=BIND_KIND_SPECIAL_AMOUNT,
            amount_cents=9999,
            status=ATTEMPT_STATUS_PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        variant = MagicMock()
        variant.response_text = "Venmo: https://venmo.com/u/club-round"
        variant.response_caption = None

        session = MagicMock()
        session.query.return_value.filter_by.return_value.filter.return_value.order_by.return_value.all.return_value = [
            attempt
        ]
        session.query.return_value.get.return_value = variant

        from bot.services.payment_method_binding import (
            match_pending_venmo_setup_in_session,
        )

        found = match_pending_venmo_setup_in_session(
            session, amount_cents=9999, venmo_handle="@club-round"
        )
        self.assertIs(found, attempt)


class TestMatchMemoInSession(unittest.TestCase):
    def test_match_finds_attempt_by_code_and_handle(self):
        from datetime import datetime, timedelta, timezone

        from db.models import PaymentMethodBindAttempt

        attempt = PaymentMethodBindAttempt(
            id=8,
            telegram_chat_id=-1002,
            club_id=2,
            payment_method_slug="venmo",
            method_id=10,
            variant_id=3,
            bind_kind=BIND_KIND_MEMO_EMOJI,
            amount_cents=None,
            setup_emoji="GG-FLOP",
            status=ATTEMPT_STATUS_PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        variant = MagicMock()
        variant.response_text = "Venmo: https://venmo.com/u/club-round"
        variant.response_caption = None

        session = MagicMock()
        session.query.return_value.filter_by.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
            attempt
        ]
        session.query.return_value.get.return_value = variant

        found = match_pending_memo_setup_in_session(
            session,
            payment_method_slug="venmo",
            venmo_handle="@club-round",
            memo="Thanks GG-FLOP",
        )
        self.assertIs(found, attempt)

    def test_wrong_code_no_match(self):
        from datetime import datetime, timedelta, timezone

        from db.models import PaymentMethodBindAttempt

        attempt = PaymentMethodBindAttempt(
            id=9,
            telegram_chat_id=-1002,
            club_id=2,
            payment_method_slug="venmo",
            method_id=10,
            variant_id=3,
            bind_kind=BIND_KIND_MEMO_EMOJI,
            setup_emoji="GG-TURN",
            status=ATTEMPT_STATUS_PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        variant = MagicMock()
        variant.response_text = "Venmo: https://venmo.com/u/club-round"

        session = MagicMock()
        session.query.return_value.filter_by.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
            attempt
        ]
        session.query.return_value.get.return_value = variant

        found = match_pending_memo_setup_in_session(
            session,
            payment_method_slug="venmo",
            venmo_handle="@club-round",
            memo="Thanks GG-FLOP",
        )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
