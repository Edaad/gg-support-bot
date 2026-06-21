#!/usr/bin/env python3
"""Backfill staff_cashout_records from completed cashier_cashout_jobs.

Usage:
    DATABASE_URL=... python scripts/backfill_staff_cashout_records.py
    DATABASE_URL=... python scripts/backfill_staff_cashout_records.py --apply
    DATABASE_URL=... python scripts/backfill_staff_cashout_records.py --apply --limit 10

Default is dry-run (no writes).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.services.staff_cashout_records import (  # noqa: E402
    create_staff_cashout_record_from_job,
    get_staff_cashout_record_by_job_id,
)
from cashier.services.jobs import _job_to_dict  # noqa: E402
from db.connection import get_db  # noqa: E402
from db.models import CashierCashoutJob  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write records (default: dry-run only)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max jobs to process")
    args = parser.parse_args()

    with get_db() as session:
        query = (
            session.query(CashierCashoutJob)
            .filter(CashierCashoutJob.status == "completed")
            .order_by(CashierCashoutJob.completed_at.desc().nullslast())
        )
        if args.limit:
            query = query.limit(args.limit)
        jobs = query.all()

    created = 0
    skipped = 0
    for job in jobs:
        job_dict = _job_to_dict(job)
        if get_staff_cashout_record_by_job_id(int(job.id)):
            skipped += 1
            print(f"skip job_id={job.id} (record exists)")
            continue
        if not args.apply:
            print(f"would create record for job_id={job.id} title={job.group_title!r}")
            created += 1
            continue
        record_id = create_staff_cashout_record_from_job(job_dict)
        print(f"created record_id={record_id} job_id={job.id}")
        created += 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n{mode}: {created} to create/would create, {skipped} skipped (existing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
