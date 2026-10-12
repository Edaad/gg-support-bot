"""PII masking / secret dropping and JSON serialisation used by every tool result."""

from __future__ import annotations

import dataclasses
import enum
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pydantic import BaseModel

from db.models import Club
from mcp_server.redact import REDACTED, mask, redact
from mcp_server.serialize import BINARY_PLACEHOLDER, cents_to_usd, to_jsonable


class RedactTests(unittest.TestCase):
    def test_secrets_dropped_at_depth_regardless_of_include_pii(self) -> None:
        payload = {
            "id": 1,
            "token": "abc",
            "nested": {"password": "pw", "items": [{"api_key": "k", "ok": 1}]},
        }
        for include_pii in (False, True):
            out = redact(payload, include_pii=include_pii)
            self.assertNotIn("token", out)
            self.assertNotIn("password", out["nested"])
            self.assertEqual(out["nested"]["items"], [{"ok": 1}])
            self.assertEqual(out["id"], 1)

    def test_secret_keys_match_case_insensitively(self) -> None:
        out = redact({"Authorization": "Bearer x", "Content": "raw"}, include_pii=True)
        self.assertEqual(out, {})

    def test_name_mask(self) -> None:
        self.assertEqual(mask("payer_name", "Alice Smith"), "A***")
        out = redact({"payer_name": "  Bob  "}, include_pii=False)
        self.assertEqual(out["payer_name"], "B***")

    def test_email_mask(self) -> None:
        self.assertEqual(mask("email", "alice@example.com"), "al***@example.com")
        self.assertEqual(mask("paypal_email", "not-an-email"), "n***")

    def test_handle_mask(self) -> None:
        self.assertEqual(mask("venmo_handle", "@alice"), "@al***")
        self.assertEqual(mask("cashapp_tag", "$bobby"), "@$b***")
        self.assertEqual(mask("username", "   "), "   ")

    def test_phone_mask(self) -> None:
        self.assertEqual(mask("phone", "+1 (555) 123-4567"), "***4567")
        self.assertEqual(mask("phone_number", "no digits"), REDACTED)

    def test_crypto_mask(self) -> None:
        long_address = "0x1234567890abcdef1234567890abcdef12345678"
        self.assertEqual(mask("from_address", long_address), "0x1234…5678")
        self.assertEqual(mask("tx_hash", "short"), REDACTED)

    def test_free_text_is_fully_redacted(self) -> None:
        self.assertEqual(mask("memo", "paying for chips"), REDACTED)
        self.assertEqual(mask("invite_link", "https://t.me/+abc"), REDACTED)
        out = redact({"payout_details": "zelle 555-1234"}, include_pii=False)
        self.assertEqual(out["payout_details"], REDACTED)

    def test_non_string_pii_is_redacted_and_none_kept(self) -> None:
        self.assertEqual(mask("payer_name", 123), REDACTED)
        self.assertIsNone(mask("payer_name", None))
        out = redact({"memo": None, "handle": ["a", "b"]}, include_pii=False)
        self.assertIsNone(out["memo"])
        self.assertEqual(out["handle"], REDACTED)

    def test_include_pii_passes_values_through_but_drops_secrets(self) -> None:
        payload = {
            "payer_name": "Alice",
            "email": "alice@example.com",
            "memo": "hello",
            "telethon_auth_string": "1BQ...",
        }
        out = redact(payload, include_pii=True)
        self.assertEqual(out["payer_name"], "Alice")
        self.assertEqual(out["email"], "alice@example.com")
        self.assertEqual(out["memo"], "hello")
        self.assertNotIn("telethon_auth_string", out)

    def test_navigation_keys_untouched(self) -> None:
        payload = {
            "chat_id": -1001234567890,
            "telegram_chat_id": -1001234567890,
            "group_title": "GTO / 8190-5287 / ThePirate343",
            "gg_player_id": "8190-5287",
        }
        self.assertEqual(redact(payload, include_pii=False), payload)

    def test_lists_and_tuples_recurse(self) -> None:
        payload = [
            {"payer_name": "Alice", "amount_cents": 100},
            ({"token": "x", "memo": "m"}, "plain"),
        ]
        out = redact(payload, include_pii=False)
        self.assertEqual(out[0], {"payer_name": "A***", "amount_cents": 100})
        self.assertIsInstance(out[1], list)
        self.assertEqual(out[1], [{"memo": REDACTED}, "plain"])

    def test_scalars_pass_through(self) -> None:
        self.assertEqual(redact("x", include_pii=False), "x")
        self.assertEqual(redact(3, include_pii=False), 3)
        self.assertIsNone(redact(None, include_pii=False))


class _Kind(enum.Enum):
    A = "alpha"


class _Model(BaseModel):
    amount: Decimal
    when: datetime


@dataclasses.dataclass
class _Row:
    id: int
    total: Decimal
    tags: tuple


class SerializeTests(unittest.TestCase):
    def test_decimal_to_float_two_places(self) -> None:
        self.assertEqual(to_jsonable(Decimal("12.346")), 12.35)
        self.assertEqual(to_jsonable(Decimal("12.344")), 12.34)
        self.assertEqual(to_jsonable(Decimal("10")), 10.0)
        self.assertIsInstance(to_jsonable(Decimal("1.5")), float)

    def test_naive_datetime_gets_z_suffix(self) -> None:
        self.assertEqual(
            to_jsonable(datetime(2026, 7, 1, 12, 30, 15)), "2026-07-01T12:30:15Z"
        )

    def test_aware_datetime_keeps_offset(self) -> None:
        utc = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(to_jsonable(utc), "2026-07-01T12:00:00+00:00")
        minus_four = datetime(2026, 7, 1, 8, 0, tzinfo=timezone(timedelta(hours=-4)))
        self.assertEqual(to_jsonable(minus_four), "2026-07-01T08:00:00-04:00")

    def test_date_uuid_enum_bytes(self) -> None:
        self.assertEqual(to_jsonable(date(2026, 1, 15)), "2026-01-15")
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        self.assertEqual(to_jsonable(uid), str(uid))
        self.assertEqual(to_jsonable(_Kind.A), "alpha")
        self.assertEqual(to_jsonable(b"\x00\x01"), BINARY_PLACEHOLDER)
        self.assertEqual(to_jsonable(bytearray(b"x")), BINARY_PLACEHOLDER)
        self.assertEqual(to_jsonable(memoryview(b"x")), BINARY_PLACEHOLDER)

    def test_pydantic_model(self) -> None:
        out = to_jsonable(_Model(amount=Decimal("5.005"), when=datetime(2026, 1, 1)))
        self.assertEqual(out, {"amount": 5.0, "when": "2026-01-01T00:00:00Z"})

    def test_dataclass(self) -> None:
        out = to_jsonable(_Row(id=1, total=Decimal("2.50"), tags=("a", Decimal("1"))))
        self.assertEqual(out, {"id": 1, "total": 2.5, "tags": ["a", 1.0]})
        # The dataclass *type* itself is not treated as an instance.
        self.assertEqual(to_jsonable(_Row), str(_Row))

    def test_orm_row_columns_only(self) -> None:
        club = Club(id=7, name="ClubGTO", telegram_user_id=42, welcome_text="hi")
        out = to_jsonable(club)
        self.assertIsInstance(out, dict)
        self.assertEqual(out["id"], 7)
        self.assertEqual(out["name"], "ClubGTO")
        self.assertEqual(out["telegram_user_id"], 42)
        self.assertEqual(out["welcome_text"], "hi")
        # Relationships are never followed.
        for relationship_key in ("groups", "linked_accounts", "club_payment_methods"):
            self.assertNotIn(relationship_key, out)

    def test_mappings_sets_and_fallback(self) -> None:
        out = to_jsonable({1: {"x", "x"}, "k": frozenset([Decimal("1")])})
        self.assertEqual(out, {"1": ["x"], "k": [1.0]})
        self.assertEqual(to_jsonable(object).__class__, str)

    def test_cents_to_usd(self) -> None:
        self.assertIsNone(cents_to_usd(None))
        self.assertEqual(cents_to_usd(12345), 123.45)
        self.assertEqual(cents_to_usd(0), 0.0)
        self.assertEqual(cents_to_usd(-250), -2.5)


if __name__ == "__main__":
    unittest.main()
