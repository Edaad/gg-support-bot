#!/usr/bin/env python3
"""Backfill expenses from a CSV export.

Expected columns: date, amount, description, club (expense_type optional).

Usage (dry run — default, no DB writes):
    python scripts/backfill_expenses_from_csv.py --csv ~/Downloads/Expenses.csv

Apply to database (requires DATABASE_URL):
    DATABASE_URL=... python scripts/backfill_expenses_from_csv.py --csv ~/Downloads/Expenses.csv --apply

Smoke test:
    DATABASE_URL=... python scripts/backfill_expenses_from_csv.py --csv ~/Downloads/Expenses.csv --apply --limit 5
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from db.connection import get_db, init_engine
from db.models import Club, Expense

DEFAULT_EXPENSE_TYPE = "Other"


def _strip_cell(s: str | None) -> str:
    if s is None:
        return ""
    return s.replace("\x00", "").strip()


def parse_expense_date(raw: str) -> date:
    """Parse ISO or DD/MM/YYYY[, time] into a calendar date."""
    raw = _strip_cell(raw)
    if not raw:
        raise ValueError("date is empty")
    if "T" in raw:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.date()
    date_part = raw.split(",")[0].strip()
    return datetime.strptime(date_part, "%d/%m/%Y").date()


def parse_amount(raw: str) -> Decimal:
    raw = _strip_cell(raw).replace("$", "").replace(",", "")
    if not raw:
        raise ValueError("amount is empty")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount {raw!r}") from exc
    if amount == 0:
        raise ValueError("amount must not be zero")
    return amount


def resolve_expense_type(raw: str | None) -> str:
    et = _strip_cell(raw)
    return et if et else DEFAULT_EXPENSE_TYPE


@dataclass(frozen=True)
class ParsedExpenseRow:
    line_no: int
    expense_date: date
    amount: Decimal
    description: str | None
    club_name: str
    expense_type: str


@dataclass
class RowOutcome:
    line_no: int
    status: str  # ok | skip_duplicate | error
    message: str
    parsed: ParsedExpenseRow | None = None


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        required = {"date", "amount", "description", "club"}
        missing = required - {h.strip() for h in reader.fieldnames if h}
        if missing:
            raise ValueError(f"CSV missing columns: {sorted(missing)}")
        return list(reader)


def parse_csv_row(line_no: int, row: dict[str, str]) -> ParsedExpenseRow:
    club_name = _strip_cell(row.get("club"))
    if not club_name:
        raise ValueError("club is empty")
    description = _strip_cell(row.get("description")) or None
    return ParsedExpenseRow(
        line_no=line_no,
        expense_date=parse_expense_date(row.get("date", "")),
        amount=parse_amount(row.get("amount", "")),
        description=description,
        club_name=club_name,
        expense_type=resolve_expense_type(row.get("expense_type")),
    )


def fetch_club_name_to_id(session: Session) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for club in session.query(Club.id, Club.name).all():
        mapping[club.name.strip().lower()] = club.id
    return mapping


def expense_exists(
    session: Session,
    *,
    club_id: int,
    expense_date: date,
    amount: Decimal,
    description: str | None,
) -> bool:
    query = session.query(Expense.id).filter(
        Expense.club_id == club_id,
        Expense.expense_date == expense_date,
        Expense.amount == amount,
    )
    if description is None:
        query = query.filter(Expense.description.is_(None))
    else:
        query = query.filter(Expense.description == description)
    return query.first() is not None


def process_rows(
    session: Session | None,
    parsed_rows: Iterable[ParsedExpenseRow],
    *,
    apply: bool,
    club_map: dict[str, int] | None,
) -> list[RowOutcome]:
    outcomes: list[RowOutcome] = []
    for row in parsed_rows:
        if club_map is None:
            outcomes.append(
                RowOutcome(row.line_no, "ok", "parsed (no club lookup)", row)
            )
            continue
        club_id = club_map.get(row.club_name.lower())
        if club_id is None:
            outcomes.append(
                RowOutcome(
                    row.line_no,
                    "error",
                    f"unknown club {row.club_name!r}",
                    row,
                )
            )
            continue
        if session is not None and expense_exists(
            session,
            club_id=club_id,
            expense_date=row.expense_date,
            amount=row.amount,
            description=row.description,
        ):
            outcomes.append(
                RowOutcome(row.line_no, "skip_duplicate", "already exists", row)
            )
            continue
        if apply and session is not None:
            session.add(
                Expense(
                    amount=row.amount,
                    expense_type=row.expense_type,
                    description=row.description,
                    club_id=club_id,
                    expense_date=row.expense_date,
                    pending=False,
                )
            )
        outcomes.append(RowOutcome(row.line_no, "ok", "insert" if apply else "would insert", row))
    return outcomes


def print_summary(outcomes: list[RowOutcome], *, apply: bool) -> None:
    counts: dict[str, int] = defaultdict(int)
    totals_by_club: dict[str, Decimal] = defaultdict(Decimal)
    errors: list[RowOutcome] = []

    for o in outcomes:
        counts[o.status] += 1
        if o.status == "error":
            errors.append(o)
        if o.parsed and o.status in ("ok", "skip_duplicate"):
            totals_by_club[o.parsed.club_name] += o.parsed.amount

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"\n{mode} summary:")
    for status in ("ok", "skip_duplicate", "error"):
        if counts[status]:
            print(f"  {status}: {counts[status]}")
    if totals_by_club:
        print("\nAmount totals by club (parsed rows, incl. skipped dupes):")
        for club in sorted(totals_by_club):
            print(f"  {club}: {totals_by_club[club]}")
    if errors:
        print("\nErrors:")
        for o in errors[:20]:
            print(f"  line {o.line_no}: {o.message}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True, help="Path to Expenses.csv")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to the database (otherwise dry run only)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max data rows to process")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any row fails to parse or has unknown club",
    )
    args = parser.parse_args()

    if not args.csv.is_file():
        print(f"File not found: {args.csv}", file=sys.stderr)
        return 1

    raw_rows = load_csv_rows(args.csv)
    if args.limit is not None:
        raw_rows = raw_rows[: args.limit]

    parsed: list[ParsedExpenseRow] = []
    parse_errors: list[RowOutcome] = []
    for i, row in enumerate(raw_rows, start=2):
        try:
            parsed.append(parse_csv_row(i, row))
        except ValueError as exc:
            parse_errors.append(RowOutcome(i, "error", str(exc)))

    if parse_errors:
        print("Parse errors:")
        for o in parse_errors:
            print(f"  line {o.line_no}: {o.message}")
        if args.strict:
            return 1

    outcomes = list(parse_errors)

    if not parsed:
        print_summary(outcomes, apply=args.apply)
        error_count = sum(1 for o in outcomes if o.status == "error")
        return 1 if args.strict and error_count else 0

    try:
        init_engine()
    except RuntimeError:
        outcomes.extend(process_rows(None, parsed, apply=False, club_map=None))
        for o in outcomes:
            if o.parsed and o.status == "ok":
                p = o.parsed
                desc = (p.description or "")[:50]
                print(
                    f"line {p.line_no}: {o.message} | {p.expense_date} | {p.amount} | "
                    f"{p.club_name} | {p.expense_type!r} | {desc!r}"
                )
        print_summary(outcomes, apply=args.apply)
        error_count = sum(1 for o in outcomes if o.status == "error")
        return 1 if args.strict and error_count else 0

    with get_db() as session:
        club_map = fetch_club_name_to_id(session)
        outcomes.extend(
            process_rows(session, parsed, apply=args.apply, club_map=club_map)
        )

    for o in outcomes:
        if o.parsed and o.status == "ok":
            p = o.parsed
            desc = (p.description or "")[:50]
            print(
                f"line {p.line_no}: {o.message} | {p.expense_date} | {p.amount} | "
                f"{p.club_name} | {p.expense_type!r} | {desc!r}"
            )

    print_summary(outcomes, apply=args.apply)

    error_count = sum(1 for o in outcomes if o.status == "error")
    if args.strict and error_count:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
