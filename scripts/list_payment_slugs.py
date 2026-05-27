"""List payment method + sub-option slugs from Postgres.

This script shows the *exact* `slug` values the bot reads from the dashboard DB:
- payment method slug: `payment_methods.slug`
- sub-option slug: `payment_sub_options.slug`

Usage:
  python3.11 scripts/list_payment_slugs.py
  python3.11 scripts/list_payment_slugs.py --direction deposit
  python3.11 scripts/list_payment_slugs.py --club-id 2 --direction deposit
  python3.11 scripts/list_payment_slugs.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if sys.version_info < (3, 10):
    raise SystemExit(
        f"Python 3.10+ required (this interpreter is {sys.version.split()[0]})."
    )

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except Exception:
    pass


@dataclass(frozen=True)
class SubOptionRow:
    id: int
    name: str
    slug: str
    is_active: bool


@dataclass(frozen=True)
class MethodRow:
    id: int
    name: str
    slug: str
    direction: str
    is_active: bool
    has_sub_options: bool
    sub_options: tuple[SubOptionRow, ...] = ()


@dataclass(frozen=True)
class ClubRow:
    id: int
    name: str
    methods: tuple[MethodRow, ...]


def _fetch(*, club_id: int | None, direction: str | None) -> list[ClubRow]:
    from db.connection import init_engine, get_db
    from db.models import Club, PaymentMethod, PaymentSubOption

    init_engine()

    with get_db() as session:
        clubs_q = session.query(Club).order_by(Club.name)
        if club_id is not None:
            clubs_q = clubs_q.filter(Club.id == int(club_id))
        clubs = clubs_q.all()

        out: list[ClubRow] = []
        for club in clubs:
            methods_q = (
                session.query(PaymentMethod)
                .filter(PaymentMethod.club_id == club.id)
                .order_by(PaymentMethod.direction, PaymentMethod.sort_order, PaymentMethod.id)
            )
            if direction:
                methods_q = methods_q.filter(PaymentMethod.direction == direction)
            methods = methods_q.all()

            method_rows: list[MethodRow] = []
            for m in methods:
                subs: list[SubOptionRow] = []
                if bool(m.has_sub_options):
                    sub_rows = (
                        session.query(PaymentSubOption)
                        .filter(PaymentSubOption.method_id == m.id)
                        .order_by(PaymentSubOption.sort_order, PaymentSubOption.id)
                        .all()
                    )
                    subs = [
                        SubOptionRow(
                            id=int(s.id),
                            name=str(s.name or ""),
                            slug=str(s.slug or ""),
                            is_active=bool(s.is_active),
                        )
                        for s in sub_rows
                    ]

                method_rows.append(
                    MethodRow(
                        id=int(m.id),
                        name=str(m.name or ""),
                        slug=str(m.slug or ""),
                        direction=str(m.direction or ""),
                        is_active=bool(m.is_active),
                        has_sub_options=bool(m.has_sub_options),
                        sub_options=tuple(subs),
                    )
                )

            out.append(
                ClubRow(
                    id=int(club.id),
                    name=str(club.name or ""),
                    methods=tuple(method_rows),
                )
            )

    return out


def _print_human(rows: list[ClubRow]) -> None:
    if not rows:
        print("No clubs found.")
        return
    for club in rows:
        print(f"== {club.name} (club_id={club.id}) ==")
        if not club.methods:
            print("  (no payment methods)")
            print()
            continue
        for m in club.methods:
            flags = []
            if m.is_active:
                flags.append("active")
            else:
                flags.append("inactive")
            if m.has_sub_options:
                flags.append("has_sub_options")
            flag_txt = ",".join(flags)
            print(f"  [{m.direction}] method_id={m.id} slug={m.slug!r} name={m.name!r} ({flag_txt})")
            for s in m.sub_options:
                sflag = "active" if s.is_active else "inactive"
                print(f"    - sub_id={s.id} slug={s.slug!r} name={s.name!r} ({sflag})")
        print()


def main() -> None:
    p = argparse.ArgumentParser(description="List payment method + sub-option slugs.")
    p.add_argument("--club-id", type=int, default=None, help="Filter to a single clubs.id")
    p.add_argument(
        "--direction",
        choices=("deposit", "cashout"),
        default=None,
        help="Filter to deposit or cashout methods",
    )
    p.add_argument("--json", action="store_true", help="Print JSON instead of text")
    args = p.parse_args()

    rows = _fetch(club_id=args.club_id, direction=args.direction)
    if args.json:
        payload: list[dict[str, Any]] = [asdict(r) for r in rows]
        print(json.dumps(payload, indent=2))
    else:
        _print_human(rows)


if __name__ == "__main__":
    main()

