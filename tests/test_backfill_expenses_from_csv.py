"""Tests for expenses CSV backfill parsing and import logic."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base, Club, Expense
from scripts.backfill_expenses_from_csv import (
    DEFAULT_EXPENSE_TYPE,
    expense_exists,
    parse_amount,
    parse_csv_row,
    parse_expense_date,
    process_rows,
    resolve_expense_type,
)


class ExpenseCsvParsingTestCase(unittest.TestCase):
    def test_parse_iso_date(self) -> None:
        self.assertEqual(
            parse_expense_date("2025-06-21T20:00:00.000Z"),
            date(2025, 6, 21),
        )

    def test_parse_dd_mm_yyyy_with_time(self) -> None:
        self.assertEqual(
            parse_expense_date("26/06/2025, 0:52:04"),
            date(2025, 6, 26),
        )

    def test_parse_amount_positive(self) -> None:
        self.assertEqual(parse_amount("100"), Decimal("100"))
        self.assertEqual(parse_amount("$1,234.56"), Decimal("1234.56"))

    def test_parse_amount_negative(self) -> None:
        self.assertEqual(parse_amount("-106"), Decimal("-106"))

    def test_parse_amount_zero_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_amount("0")

    def test_resolve_expense_type_fallback(self) -> None:
        self.assertEqual(resolve_expense_type(""), DEFAULT_EXPENSE_TYPE)
        self.assertEqual(resolve_expense_type("Diamonds"), "Diamonds")

    def test_parse_csv_row(self) -> None:
        row = parse_csv_row(
            2,
            {
                "date": "26/06/2025, 3:38:54",
                "amount": "64",
                "description": "RT Emails",
                "club": "Round Table",
                "expense_type": "",
            },
        )
        self.assertEqual(row.expense_date, date(2025, 6, 26))
        self.assertEqual(row.amount, Decimal("64"))
        self.assertEqual(row.club_name, "Round Table")
        self.assertEqual(row.expense_type, DEFAULT_EXPENSE_TYPE)
        self.assertEqual(row.description, "RT Emails")


class ExpenseCsvImportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine, tables=[Club.__table__, Expense.__table__])
        self.Session = sessionmaker(bind=self.engine)
        session = self.Session()
        session.add(Club(id=1, name="Round Table", telegram_user_id=1001, is_active=True))
        session.add(Club(id=2, name="ClubGTO", telegram_user_id=1002, is_active=True))
        session.commit()
        session.close()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_unknown_club_is_error(self) -> None:
        session = self.Session()
        parsed = parse_csv_row(
            2,
            {
                "date": "01/07/2025, 1:00:00",
                "amount": "10",
                "description": "x",
                "club": "Unknown Club",
            },
        )
        outcomes = process_rows(session, [parsed], apply=False, club_map={"round table": 1})
        self.assertEqual(outcomes[0].status, "error")
        session.close()

    def test_duplicate_skip(self) -> None:
        session = self.Session()
        session.add(
            Expense(
                amount=Decimal("50"),
                expense_type="Ads",
                description="FB",
                club_id=1,
                expense_date=date(2026, 8, 1),
                pending=False,
            )
        )
        session.commit()

        parsed = parse_csv_row(
            2,
            {
                "date": "01/08/2026, 0:00:00",
                "amount": "50",
                "description": "FB",
                "club": "Round Table",
                "expense_type": "Ads",
            },
        )
        outcomes = process_rows(
            session,
            [parsed],
            apply=True,
            club_map={"round table": 1, "clubgto": 2},
        )
        self.assertEqual(outcomes[0].status, "skip_duplicate")
        self.assertEqual(session.query(Expense).count(), 1)
        session.close()

    def test_apply_inserts_row(self) -> None:
        session = self.Session()
        parsed = parse_csv_row(
            2,
            {
                "date": "01/08/2026, 0:00:00",
                "amount": "-20",
                "description": "Refund",
                "club": "Round Table",
                "expense_type": "Credit",
            },
        )
        outcomes = process_rows(
            session,
            [parsed],
            apply=True,
            club_map={"round table": 1},
        )
        session.commit()
        self.assertEqual(outcomes[0].status, "ok")
        row = session.query(Expense).one()
        self.assertEqual(row.amount, Decimal("-20"))
        self.assertFalse(row.pending)
        session.close()

    def test_expense_exists_null_description(self) -> None:
        session = self.Session()
        session.add(
            Expense(
                amount=Decimal("109"),
                expense_type="Diamonds",
                description=None,
                club_id=1,
                expense_date=date(2025, 8, 30),
                pending=False,
            )
        )
        session.commit()
        self.assertTrue(
            expense_exists(
                session,
                club_id=1,
                expense_date=date(2025, 8, 30),
                amount=Decimal("109"),
                description=None,
            )
        )
        self.assertFalse(
            expense_exists(
                session,
                club_id=1,
                expense_date=date(2025, 8, 30),
                amount=Decimal("109"),
                description="",
            )
        )
        session.close()


if __name__ == "__main__":
    unittest.main()
