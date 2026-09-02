"""Tests for union deposit instruction builder."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from bot.services.union_deposit_instruction import build_union_deposit_instruction


def _method(**kwargs):
    defaults = {
        "union_type": "cashapp",
        "method_tag": "$cashapp",
        "deposit_limit": Decimal("5000"),
        "min_amount": Decimal("500"),
        "max_amount": Decimal("2000"),
        "payment_account_name": "CONCORD CONSULTING AGENCY, INC",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class BuildUnionDepositInstructionTests(unittest.TestCase):
    def test_full_template(self):
        text = build_union_deposit_instruction(_method(), used_sum=Decimal("3800"))
        self.assertIn("Min: $500", text)
        self.assertIn("Max: $1200", text)
        self.assertIn("Cash App Tag: $cashapp", text)
        self.assertIn(
            "Cash App Name: CONCORD CONSULTING AGENCY, INC",
            text,
        )
        self.assertIn("random emoji", text)
        self.assertIn("Credits will be added", text)

    def test_omits_min_when_unset(self):
        text = build_union_deposit_instruction(
            _method(min_amount=None),
            used_sum=Decimal("0"),
        )
        self.assertNotIn("Min:", text)
        self.assertIn("Max: $2000", text)

    def test_omits_name_when_unset(self):
        text = build_union_deposit_instruction(
            _method(payment_account_name=None),
            used_sum=Decimal("0"),
        )
        self.assertNotIn(" Name:", text)

    def test_max_uses_remaining_capacity(self):
        text = build_union_deposit_instruction(
            _method(max_amount=None),
            used_sum=Decimal("4500"),
        )
        self.assertIn("Max: $500", text)

    def test_ack_step_excludes_current_request_from_used_sum(self):
        method = _method(
            deposit_limit=Decimal("500"),
            min_amount=Decimal("500"),
            max_amount=None,
            union_type="zelle",
            method_tag="zelle email",
        )
        text = build_union_deposit_instruction(method, used_sum=Decimal("0"))
        self.assertIn("Min: $500", text)
        self.assertIn("Max: $500", text)

    def test_returns_none_without_method_tag(self):
        self.assertIsNone(
            build_union_deposit_instruction(
                _method(method_tag=""),
                used_sum=Decimal("0"),
            )
        )

    def test_works_with_dict(self):
        text = build_union_deposit_instruction(
            {
                "union_type": "zelle",
                "method_tag": "pay@example.com",
                "deposit_limit": "1000",
                "min_amount": None,
                "max_amount": None,
                "payment_account_name": None,
            },
            used_sum=Decimal("100"),
        )
        self.assertIn("Zelle Tag: pay@example.com", text)
        self.assertIn("Max: $900", text)

    def test_html_tag_tap_to_copy(self):
        text = build_union_deposit_instruction(
            _method(method_tag="$cashapp"),
            used_sum=Decimal("0"),
            html_mode=True,
        )
        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn("Tap the tag below to copy it.", text)
        self.assertIn("<code>$cashapp</code>", text)


if __name__ == "__main__":
    unittest.main()
