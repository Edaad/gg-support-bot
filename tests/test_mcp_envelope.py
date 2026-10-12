"""Response envelopes: page(), single(), dry_run(), write_result()."""

from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal

from mcp_server.envelope import dry_run, page, single, write_result
from mcp_server.redact import REDACTED


def _rows(n: int) -> list[dict]:
    return [{"id": i, "payer_name": f"Payer {i}"} for i in range(n)]


class PageTests(unittest.TestCase):
    def test_truncated_when_total_exceeds_window(self) -> None:
        out = page(_rows(5), total=10, limit=5, offset=0)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["total"], 10)
        self.assertEqual(out["returned"], 5)
        self.assertEqual(out["limit"], 5)
        self.assertEqual(out["offset"], 0)

    def test_not_truncated_when_offset_plus_rows_reaches_total(self) -> None:
        out = page(_rows(5), total=10, limit=5, offset=5)
        self.assertFalse(out["truncated"])
        out = page(_rows(3), total=3, limit=50, offset=0)
        self.assertFalse(out["truncated"])

    def test_total_none_uses_limit_heuristic(self) -> None:
        full = page(_rows(5), total=None, limit=5, offset=0)
        self.assertTrue(full["truncated"])
        self.assertIsNone(full["total"])
        partial = page(_rows(4), total=None, limit=5, offset=0)
        self.assertFalse(partial["truncated"])

    def test_empty_page(self) -> None:
        out = page([], total=0, limit=50, offset=0)
        self.assertEqual(out["items"], [])
        self.assertEqual(out["returned"], 0)
        self.assertFalse(out["truncated"])

    def test_pii_masked_flag_and_masking(self) -> None:
        masked = page(_rows(1), total=1, limit=1, offset=0)
        self.assertTrue(masked["pii_masked"])
        self.assertEqual(masked["items"][0]["payer_name"], "P***")
        clear = page(_rows(1), total=1, limit=1, offset=0, include_pii=True)
        self.assertFalse(clear["pii_masked"])
        self.assertEqual(clear["items"][0]["payer_name"], "Payer 0")

    def test_notes_and_as_of(self) -> None:
        out = page([], total=0, limit=1, offset=0, notes=("amounts are dollars",))
        self.assertEqual(out["notes"], ["amounts are dollars"])
        self.assertIsInstance(out["as_of"], str)
        self.assertTrue(out["as_of"].endswith("Z"))
        self.assertEqual(page([], total=0, limit=1, offset=0)["notes"], [])

    def test_items_are_serialised(self) -> None:
        rows = [{"amount": Decimal("1.005"), "when": datetime(2026, 1, 1), "t": (1, 2)}]
        out = page(rows, total=1, limit=1, offset=0)
        self.assertEqual(
            out["items"][0],
            {"amount": 1.0, "when": "2026-01-01T00:00:00Z", "t": [1, 2]},
        )

    def test_accepts_any_iterable(self) -> None:
        out = page((r for r in _rows(2)), total=2, limit=10, offset=0)
        self.assertEqual(out["returned"], 2)


class SingleTests(unittest.TestCase):
    def test_single_masks_and_wraps(self) -> None:
        out = single({"id": 1, "memo": "hi", "token": "t"})
        self.assertEqual(out["item"], {"id": 1, "memo": REDACTED})
        self.assertTrue(out["pii_masked"])
        self.assertEqual(out["notes"], [])
        self.assertIn("as_of", out)

    def test_single_include_pii_and_notes(self) -> None:
        out = single({"memo": "hi"}, include_pii=True, notes=["n"])
        self.assertEqual(out["item"]["memo"], "hi")
        self.assertFalse(out["pii_masked"])
        self.assertEqual(out["notes"], ["n"])

    def test_single_none_item(self) -> None:
        self.assertIsNone(single(None)["item"])


class WriteEnvelopeTests(unittest.TestCase):
    def test_dry_run_is_refused_and_not_applied(self) -> None:
        out = dry_run(
            actor="dev",
            current={"note": None, "payer_name": "Alice"},
            proposed={"note": "x"},
            message="dry run",
        )
        self.assertTrue(out["refused"])
        self.assertFalse(out["applied"])
        self.assertFalse(out["ok"])
        self.assertIn("confirm=true", out["reason"])
        self.assertEqual(out["actor"], "dev")
        self.assertEqual(out["proposed"], {"note": "x"})
        self.assertEqual(out["current"]["payer_name"], "A***")
        self.assertEqual(out["message"], "dry run")
        self.assertEqual(out["notes"], [])
        self.assertIn("as_of", out)

    def test_dry_run_notes_and_none_current(self) -> None:
        out = dry_run(actor="a", current=None, proposed={}, message="m", notes=["n"])
        self.assertIsNone(out["current"])
        self.assertEqual(out["notes"], ["n"])

    def test_write_result_is_applied(self) -> None:
        out = write_result(
            actor="dev",
            before={"note": None},
            after={"note": "x", "amount": Decimal("5.00"), "token": "secret"},
            message="updated",
            notes=["amounts are dollars"],
        )
        self.assertTrue(out["applied"])
        self.assertTrue(out["ok"])
        self.assertFalse(out["refused"])
        self.assertEqual(out["actor"], "dev")
        self.assertEqual(out["before"], {"note": None})
        self.assertEqual(out["after"], {"note": "x", "amount": 5.0})
        self.assertEqual(out["message"], "updated")
        self.assertEqual(out["notes"], ["amounts are dollars"])
        self.assertIn("as_of", out)


if __name__ == "__main__":
    unittest.main()
