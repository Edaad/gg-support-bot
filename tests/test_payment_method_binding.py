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
    format_first_time_memo_instructions_message,
    format_first_time_amount_instructions_message,
    format_first_time_payment_destination_message,
    format_first_time_venmo_setup_message,
    get_pending_bind_attempt,
    get_last_bound_deposit_at,
    match_pending_memo_setup_in_session,
    unbind_chat_from_all_methods,
    unbind_chat_from_method,
    _memo_contains_code,
)


def _mock_method_row(*, enabled: bool, mode: str | None):
    row = MagicMock()
    row.first_time_linking_enabled = enabled
    row.first_time_bind_mode = mode
    return row


class TestBindModeForMethod(unittest.TestCase):
    def test_returns_mode_when_enabled_on_method(self):
        with patch("bot.services.payment_method_binding.get_db") as mock_get_db:
            session = MagicMock()
            mock_get_db.return_value.__enter__.return_value = session
            session.query.return_value.filter_by.return_value.one_or_none.return_value = (
                _mock_method_row(enabled=True, mode=BIND_KIND_MEMO_EMOJI)
            )
            self.assertEqual(
                bind_mode_for_method("venmo", club_id=2),
                BIND_KIND_MEMO_EMOJI,
            )

    def test_disabled_method_returns_none(self):
        with patch("bot.services.payment_method_binding.get_db") as mock_get_db:
            session = MagicMock()
            mock_get_db.return_value.__enter__.return_value = session
            session.query.return_value.filter_by.return_value.one_or_none.return_value = (
                _mock_method_row(enabled=False, mode=BIND_KIND_SPECIAL_AMOUNT)
            )
            self.assertIsNone(bind_mode_for_method("venmo", club_id=2))

    def test_unsupported_slug_returns_none(self):
        self.assertIsNone(bind_mode_for_method("crypto", club_id=2))

    def test_missing_club_id_returns_none(self):
        self.assertIsNone(bind_mode_for_method("venmo"))

    def test_zelle_memo_mode_disabled(self):
        with patch("bot.services.payment_method_binding.get_db") as mock_get_db:
            session = MagicMock()
            mock_get_db.return_value.__enter__.return_value = session
            session.query.return_value.filter_by.return_value.one_or_none.return_value = (
                _mock_method_row(enabled=True, mode=BIND_KIND_MEMO_EMOJI)
            )
            self.assertIsNone(bind_mode_for_method("zelle", club_id=2))


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

    def test_unbind_all_clears_bindings_and_pending(self):
        venmo_row = MagicMock()
        zelle_row = MagicMock()
        with (
            patch("bot.services.payment_method_binding.get_db") as mock_get_db,
            patch(
                "bot.services.payment_method_binding.cancel_all_pending_attempts_for_chat",
                return_value=1,
            ) as cancel_all_mock,
        ):
            session = MagicMock()
            mock_get_db.return_value.__enter__.return_value = session
            session.query.return_value.filter_by.return_value.all.return_value = [
                venmo_row,
                zelle_row,
            ]

            removed, cancelled = unbind_chat_from_all_methods(-1001)

        self.assertEqual(removed, 2)
        self.assertEqual(cancelled, 1)
        self.assertEqual(session.delete.call_count, 2)
        cancel_all_mock.assert_called_once()


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
        self.assertEqual(code, "FLOP")

    def test_second_pending_gets_second_code(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.scalar.return_value = 1
        code = allocate_setup_memo_code(session, variant_id=1)
        self.assertEqual(code, "TURN")

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
        self.assertTrue(_memo_contains_code("Payment FLOP thanks", "FLOP"))
        self.assertTrue(_memo_contains_code("payment flop", "FLOP"))
        self.assertFalse(_memo_contains_code("Payment thanks", "FLOP"))


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
    def test_venmo_memo_instructions_embeds_code(self):
        text = format_first_time_memo_instructions_message(
            payment_method_slug="venmo",
            setup_code="FLOP",
        )
        self.assertIn("One-time Venmo setup", text)
        self.assertIn("Tap the code below", text)
        self.assertIn("<code>FLOP</code>", text)
        self.assertIn("future deposits", text)
        self.assertNotIn("venmo.com", text)

    def test_zelle_memo_instructions_embeds_code(self):
        text = format_first_time_memo_instructions_message(
            payment_method_slug="zelle",
            setup_code="TURN",
        )
        self.assertIn("One-time Zelle setup", text)
        self.assertIn("<code>TURN</code>", text)
        self.assertNotIn("@", text)

    def test_venmo_payment_destination(self):
        text = format_first_time_payment_destination_message(
            payment_method_slug="venmo",
            variant_response_text="Venmo: https://venmo.com/u/testuser",
        )
        self.assertIn("venmo.com/u/testuser", text)
        self.assertNotIn("FIRST-TIME", text)

    def test_zelle_payment_destination(self):
        text = format_first_time_payment_destination_message(
            payment_method_slug="zelle",
            variant_response_text=(
                "Zelle Email: a@b.com\nZelle Name: ACME INC\n"
            ),
        )
        self.assertIn("a@b.com", text)
        self.assertIn("ACME", text)

    def test_venmo_memo_html_legacy_combined(self):
        text = format_first_time_memo_setup_message(
            payment_method_slug="venmo",
            variant_response_text="Venmo: https://venmo.com/u/testuser",
        )
        self.assertIn("FIRST-TIME VENMO SETUP", text)
        self.assertIn("Copy and paste the code above", text)
        self.assertIn("venmo.com/u/testuser", text)

    def test_zelle_memo_html_legacy_combined(self):
        text = format_first_time_memo_setup_message(
            payment_method_slug="zelle",
            variant_response_text=(
                "Zelle Email: a@b.com\nZelle Name: ACME INC\n"
            ),
        )
        self.assertIn("FIRST-TIME ZELLE SETUP", text)
        self.assertIn("a@b.com", text)


class TestAmountInstructionsMessage(unittest.TestCase):
    def test_instructions_no_setup_amount_or_link(self):
        text = format_first_time_amount_instructions_message(
            payment_method_slug="venmo",
            chosen_amount_cents=9000,
        )
        self.assertIn("FIRST-TIME VENMO SETUP", text)
        self.assertIn("$90.00", text)
        self.assertNotIn("$89.99", text)
        self.assertNotIn("venmo.com", text)


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
            setup_emoji="FLOP",
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
            memo="Thanks FLOP",
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
            setup_emoji="TURN",
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
            memo="Thanks FLOP",
        )
        self.assertIsNone(found)


class TestZelleRecipientHelpers(unittest.TestCase):
    def test_extract_zelle_details_email(self):
        text = "Zelle Email: pay@example.com\nZelle Name: ACME LLC"
        email, name = extract_zelle_details(text)
        self.assertEqual(email, "pay@example.com")
        self.assertEqual(name, "ACME LLC")

    def test_extract_zelle_recipient_phone(self):
        from bot.services.payment_method_binding import (
            extract_zelle_recipient_from_text,
            normalize_zelle_recipient,
        )

        text = "Zelle: 310-567-0961"
        self.assertEqual(extract_zelle_recipient_from_text(text), "3105670961")
        self.assertEqual(normalize_zelle_recipient("310-567-0961"), "3105670961")
        self.assertEqual(normalize_zelle_recipient("Pay@Example.com"), "pay@example.com")

    def test_zelle_memo_setup_requires_recipient_match(self):
        from datetime import datetime, timedelta, timezone

        from bot.services.payment_method_binding import match_pending_memo_setup_in_session
        from db.models import PaymentMethodBindAttempt

        attempt = PaymentMethodBindAttempt(
            id=11,
            telegram_chat_id=-1003,
            club_id=2,
            payment_method_slug="zelle",
            method_id=10,
            variant_id=4,
            bind_kind=BIND_KIND_MEMO_EMOJI,
            setup_emoji="RIVER",
            status=ATTEMPT_STATUS_PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        variant = MagicMock()
        variant.response_text = "Zelle Email: pay@example.com\nZelle Name: ACME"
        variant.response_caption = None

        session = MagicMock()
        session.query.return_value.filter_by.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
            attempt
        ]
        session.query.return_value.get.return_value = variant

        found = match_pending_memo_setup_in_session(
            session,
            payment_method_slug="zelle",
            zelle_recipient="pay@example.com",
            memo="RIVER setup",
        )
        self.assertIsNone(found)

        wrong_recipient = match_pending_memo_setup_in_session(
            session,
            payment_method_slug="zelle",
            zelle_recipient="other@example.com",
            memo="RIVER setup",
        )
        self.assertIsNone(wrong_recipient)


if __name__ == "__main__":
    unittest.main()
