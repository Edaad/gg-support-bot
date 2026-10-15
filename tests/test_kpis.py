"""Club KPIs: period resolution, counting rules and the /api/kpis route."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import get_current_admin
from api.kpis import (
    Period,
    compute_kpis,
    is_kpi_excluded_title,
    period_label,
    resolve_period,
    rpa_outcome,
)
from api.routes.kpis import router
from db.connection import get_db_dependency
from db.models import (
    CashAppPayment,
    Club,
    ClubggRpaEvent,
    ClubPaymentMethod,
    CryptoPayment,
    Group,
    GroupChatTicket,
    ManualDepositRequest,
    PayPalPayment,
    StripeCheckoutSession,
    StripeCustomer,
    SupportGroupChat,
    VenmoPayment,
    ZellePayment,
)
from tests.support.sqlite_db import make_sqlite_session_factory

TABLES = [
    "clubs",
    "groups",
    "support_group_chats",
    "venmo_payments",
    "zelle_payments",
    "cashapp_payments",
    "paypal_payments",
    "crypto_payments",
    "stripe_customers",
    "stripe_checkout_sessions",
    "club_payment_methods",
    "manual_deposit_requests",
    "group_chat_tickets",
    "clubgg_rpa_events",
]

RT, CC, GTO = 2, 3, 4
NOW = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)  # Wednesday
TODAY = date(2026, 10, 14)


def _utc(mo, d, h=12, mi=0):
    return datetime(2026, mo, d, h, mi, tzinfo=timezone.utc)


class ResolvePeriodTests(unittest.TestCase):
    def test_week_defaults_to_last_complete_week(self):
        plan = resolve_period("week", today=TODAY)
        self.assertEqual(
            plan.current, Period(date(2026, 10, 5), date(2026, 10, 11), "week")
        )
        self.assertEqual(plan.previous.start, date(2026, 9, 28))
        self.assertEqual(plan.previous.end, date(2026, 10, 4))
        self.assertFalse(plan.previous_truncated)
        self.assertEqual(len(plan.trend), 8)
        self.assertEqual(plan.trend[-1], plan.current)
        self.assertEqual(plan.trend[0].start, date(2026, 8, 17))

    def test_any_day_in_week_anchors_to_monday(self):
        plan = resolve_period("week", anchor=date(2026, 10, 9), today=TODAY)
        self.assertEqual(plan.current.start, date(2026, 10, 5))

    def test_running_week_compares_same_elapsed_days(self):
        plan = resolve_period("week", anchor=TODAY, today=TODAY)
        self.assertEqual(plan.current.start, date(2026, 10, 12))
        self.assertEqual(plan.previous.start, date(2026, 10, 5))
        self.assertEqual(plan.previous.end, date(2026, 10, 7))
        self.assertTrue(plan.previous_truncated)

    def test_day_defaults_to_yesterday(self):
        plan = resolve_period("day", today=TODAY)
        self.assertEqual(plan.current.start, date(2026, 10, 13))
        self.assertEqual(plan.previous.start, date(2026, 10, 12))
        self.assertEqual(len(plan.trend), 14)
        self.assertEqual(plan.trend[-1].start, date(2026, 10, 13))

    def test_month(self):
        plan = resolve_period("month", today=TODAY)
        self.assertEqual(plan.current.start, date(2026, 9, 1))
        self.assertEqual(plan.current.end, date(2026, 9, 30))
        self.assertEqual(plan.previous.end, date(2026, 8, 31))
        running = resolve_period("month", anchor=TODAY, today=TODAY)
        self.assertEqual(running.previous.start, date(2026, 9, 1))
        self.assertEqual(running.previous.end, date(2026, 9, 14))
        jan = resolve_period("month", anchor=date(2026, 1, 20), today=TODAY)
        self.assertEqual(jan.previous.start, date(2025, 12, 1))

    def test_custom_previous_is_same_length(self):
        plan = resolve_period(
            "custom", start=date(2026, 10, 1), end=date(2026, 10, 10), today=TODAY
        )
        self.assertEqual(plan.previous.start, date(2026, 9, 21))
        self.assertEqual(plan.previous.end, date(2026, 9, 30))
        self.assertEqual(len(plan.trend), 10)

    def test_custom_long_range_uses_contiguous_week_buckets(self):
        plan = resolve_period(
            "custom", start=date(2026, 8, 5), end=date(2026, 10, 3), today=TODAY
        )
        self.assertEqual(plan.trend[0].start, date(2026, 8, 5))
        self.assertEqual(plan.trend[-1].end, date(2026, 10, 3))
        for a, b in zip(plan.trend, plan.trend[1:]):
            self.assertEqual(a.end + timedelta(days=1), b.start)
            self.assertEqual(b.start.weekday(), 0)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            resolve_period("custom", start=date(2026, 10, 1), today=TODAY)
        with self.assertRaises(ValueError):
            resolve_period(
                "custom", start=date(2026, 10, 2), end=date(2026, 10, 1), today=TODAY
            )
        with self.assertRaises(ValueError):
            resolve_period(
                "custom", start=date(2025, 1, 1), end=date(2026, 10, 1), today=TODAY
            )
        with self.assertRaises(ValueError):
            resolve_period("year", today=TODAY)

    def test_dst_week_bounds(self):
        # DST ends Sunday Nov 1 2026: Monday starts at 04:00 UTC, next at 05:00 UTC.
        p = Period(date(2026, 10, 26), date(2026, 11, 1), "week")
        self.assertEqual(p.start_utc, datetime(2026, 10, 26, 4, tzinfo=timezone.utc))
        self.assertEqual(p.end_utc, datetime(2026, 11, 2, 5, tzinfo=timezone.utc))

    def test_labels(self):
        self.assertEqual(
            period_label(Period(date(2026, 10, 5), date(2026, 10, 11), "week")),
            "Oct 5 – 11",
        )
        self.assertEqual(
            period_label(Period(date(2026, 9, 28), date(2026, 10, 4), "week")),
            "Sep 28 – Oct 4",
        )
        self.assertEqual(
            period_label(Period(date(2026, 10, 9), date(2026, 10, 9), "day")),
            "Fri, Oct 9",
        )
        self.assertEqual(
            period_label(Period(date(2026, 9, 1), date(2026, 9, 30), "month")),
            "September 2026",
        )


class HelperTests(unittest.TestCase):
    def test_excluded_titles(self):
        handles = ("roundtablesupport2",)
        self.assertTrue(is_kpi_excluded_title("RT / 1 / x / TEST", handles))
        self.assertTrue(is_kpi_excluded_title("RT / 1111-1111 / x", handles))
        self.assertTrue(is_kpi_excluded_title("RT / 9 / @RoundTableSupport2", handles))
        self.assertTrue(is_kpi_excluded_title("rt/", handles))
        self.assertFalse(is_kpi_excluded_title("GTO / 8190-5287 / carol", handles))
        self.assertFalse(is_kpi_excluded_title(None, handles))

    def test_rpa_outcome(self):
        self.assertEqual(rpa_outcome("completed", NOW, NOW), "completed")
        self.assertEqual(rpa_outcome("stale", NOW, NOW), "stale")
        self.assertEqual(
            rpa_outcome("started", NOW - timedelta(minutes=16), NOW), "stuck"
        )
        self.assertEqual(
            rpa_outcome("started", NOW - timedelta(minutes=5), NOW), "in_progress"
        )


class _SeededDb(unittest.TestCase):
    def setUp(self):
        self.factory = make_sqlite_session_factory(TABLES)
        with self.factory() as s:
            self._seed(s)
            s.commit()

    def _seed(self, s):
        for cid, name in ((RT, "Round Table"), (CC, "Creator Club"), (GTO, "ClubGTO")):
            s.add(Club(id=cid, name=name, telegram_user_id=cid))
        s.flush()

        def group(chat_id, club_id, name, added):
            s.add(Group(chat_id=chat_id, club_id=club_id, name=name, added_at=added))

        # added_at is naive UTC.
        group(-100, RT, "RT / 1111-2222 / alice", datetime(2026, 10, 6, 15))
        # 03:30 UTC Oct 5 = Oct 4 23:30 ET -> previous week.
        group(-101, CC, "CC / 3333-4444 / bob", datetime(2026, 10, 5, 3, 30))
        group(-102, GTO, "GTO / 5555-6666 / carol", datetime(2026, 10, 8, 15))
        group(-103, GTO, "GTO / 7777-8889 / dave", datetime(2026, 10, 9, 15))
        group(-104, RT, "RT / 9999-0000 / x / TEST", datetime(2026, 10, 6, 15))
        group(
            -105, RT, "RT / 1234-5678 / @RoundTableSupport2", datetime(2026, 10, 6, 15)
        )
        group(-106, GTO, "GTO / 2468-1357 / erin", datetime(2026, 9, 1, 15))
        group(-107, CC, "CC / 1357-2468 / frank", datetime(2026, 8, 1, 15))
        group(-108, RT, "RT / 5678-1234 / internal", datetime(2026, 8, 1, 15))
        s.add(
            SupportGroupChat(
                club_key="round_table",
                club_display_name="Round Table",
                telegram_chat_id=-108,
                telegram_chat_title="RT / 5678-1234 / internal",
                is_internal=True,
            )
        )

        def venmo(chat, cents, at, *, test=False, club=None):
            s.add(
                VenmoPayment(
                    method_owner="round-table",
                    payer_name="p",
                    venmo_handle="h",
                    amount_cents=cents,
                    telegram_chat_id=chat,
                    club_id=club,
                    is_test=test,
                    created_at=at,
                )
            )

        venmo(-100, 10000, _utc(10, 7, 14))
        venmo(None, 5000, _utc(10, 7, 14))  # unbound
        venmo(-100, 7000, _utc(10, 7, 15), test=True)
        venmo(-108, 8000, _utc(10, 7, 15))  # internal chat
        venmo(-104, 9000, _utc(10, 7, 15))  # test group
        venmo(-101, 2000, _utc(10, 1, 15))  # previous week, CC first deposit
        # Row says CC but the group is GTO: the group's club wins.
        s.add(
            ZellePayment(
                method_owner="vaughn",
                payer_name="p",
                zelle_recipient="r",
                amount_cents=20000,
                telegram_chat_id=-106,
                club_id=CC,
                created_at=_utc(10, 7, 16),
            )
        )
        s.add(
            ZellePayment(
                method_owner="vaughn",
                payer_name="p",
                zelle_recipient="r",
                amount_cents=6000,
                telegram_chat_id=-107,
                created_at=_utc(9, 10, 16),
            )
        )
        s.add(
            CashAppPayment(
                method_owner="round-table",
                payer_name="p",
                cashapp_handle="h",
                amount_cents=3000,
                telegram_chat_id=-107,
                created_at=_utc(10, 8, 16),
            )
        )
        # 03:30 UTC Oct 12 = Oct 11 23:30 ET (in); 04:30 UTC = Oct 12 00:30 ET (out).
        for cents, at in ((1000, _utc(10, 12, 3, 30)), (1100, _utc(10, 12, 4, 30))):
            s.add(
                PayPalPayment(
                    method_owner="round-table",
                    payer_name="p",
                    paypal_email="e",
                    amount_cents=cents,
                    telegram_chat_id=-100,
                    created_at=at,
                )
            )

        def crypto(chat, cents, created, paid):
            s.add(
                CryptoPayment(
                    method_owner="round-table",
                    amount_cents=cents,
                    token_symbol="USDC",
                    chain="eth",
                    from_address="a",
                    to_address="b",
                    transaction_hash=f"0x{cents}",
                    alert_scope="rt_at_cc",
                    telegram_chat_id=chat,
                    created_at=created,
                    paid_at=paid,
                )
            )

        # Ingested Oct 12 but paid Oct 10 -> counts on Oct 10.
        crypto(-107, 50000, _utc(10, 12, 5), "2026-10-10T12:00:00Z")
        # -102's only deposit lands after the week: still converted "by now".
        crypto(-102, 4000, _utc(10, 13, 15), "2026-10-13T15:00:00Z")

        s.add(
            StripeCustomer(telegram_chat_id=-100, club_id=RT, stripe_customer_id="cus")
        )
        s.flush()
        s.add(
            StripeCheckoutSession(
                stripe_checkout_session_id="cs_1",
                stripe_customer_id="cus",
                telegram_chat_id=-100,
                club_id=RT,
                amount_cents=2500,
                status="complete",
                created_at=_utc(10, 4, 23),
                completed_at=_utc(10, 5, 14),
            )
        )
        s.add(
            StripeCheckoutSession(
                stripe_checkout_session_id="cs_2",
                stripe_customer_id="cus",
                telegram_chat_id=-100,
                club_id=RT,
                amount_cents=9900,
                status="open",
                created_at=_utc(10, 6, 14),
            )
        )

        union = ClubPaymentMethod(
            id=1,
            club_id=GTO,
            direction="deposit",
            name="Zelle",
            slug="zelle",
            pool_pay_type="union_method",
        )
        other = ClubPaymentMethod(
            id=2,
            club_id=GTO,
            direction="deposit",
            name="Zelle",
            slug="zelle2",
            pool_pay_type="large_cashout",
        )
        s.add_all([union, other])
        s.flush()

        def mdr(method_id, amount, checked, chat=-106):
            s.add(
                ManualDepositRequest(
                    club_id=GTO,
                    method_id=method_id,
                    method_name="Zelle",
                    method_slug="zelle",
                    variant_name="v",
                    amount=Decimal(amount),
                    telegram_chat_id=chat,
                    trade_record_checked=checked,
                    created_at=_utc(10, 9, 15),
                )
            )

        mdr(1, "40.00", True)
        mdr(1, "41.00", False)
        mdr(2, "42.00", True)

        def ticket(tid, day, chat, club, category, frt=None):
            events = {"customer_first_message": f"{day.isoformat()}T10:00:00Z"}
            if frt is not None:
                admin = datetime.fromisoformat(f"{day.isoformat()}T10:00:00+00:00")
                events["admin_first_response"] = (
                    admin + timedelta(seconds=frt)
                ).isoformat()
            s.add(
                GroupChatTicket(
                    id=tid,
                    activity_date=day,
                    chat_id=chat,
                    club_id=club,
                    ticket_index=tid,
                    start_msg_id=1,
                    end_msg_id=2,
                    message_ids=[1, 2],
                    category=category,
                    events=events,
                    prompt_version="v",
                    model="m",
                    brief_summary=f"t{tid}",
                )
            )

        ticket(1, date(2026, 10, 6), -100, RT, "auto_deposit")
        ticket(2, date(2026, 10, 7), -100, RT, "manual_deposit", 60)
        ticket(3, date(2026, 10, 8), -102, GTO, "cashout", 400)
        ticket(4, date(2026, 10, 9), -106, GTO, "other", 120)
        ticket(5, date(2026, 10, 10), -107, CC, "early_rakeback", 30)
        ticket(6, date(2026, 10, 7), -104, RT, "other", 9999)  # test group
        ticket(7, date(2026, 10, 2), -101, CC, "cashout", 200)

        def rpa(i, source, status, at, club=None, chat=None):
            s.add(
                ClubggRpaEvent(
                    request_id=f"r{i}",
                    operation="deposit",
                    source=source,
                    status=status,
                    started_at=at,
                    club_id=club,
                    telegram_chat_id=chat,
                )
            )

        rpa(1, "payment_auto_deposit", "completed", _utc(10, 6), chat=-100)
        rpa(2, "payment_auto_deposit", "completed", _utc(10, 7), chat=-100)
        rpa(3, "payment_auto_deposit", "completed", _utc(10, 8), club=RT)
        rpa(4, "payment_auto_deposit", "failed", _utc(10, 8), chat=-102)
        rpa(5, "payment_auto_deposit", "started", _utc(10, 9), chat=-102)  # stuck
        rpa(6, "payment_auto_deposit", "stale", _utc(10, 9), chat=-102)
        rpa(7, "early_rakeback", "completed", _utc(10, 9), chat=-107)
        rpa(8, "staff_add", "completed", _utc(10, 9), chat=-100)
        rpa(9, "payment_auto_deposit", "failed", _utc(10, 9), chat=-104)  # test


class ComputeKpisTests(_SeededDb):
    def _kpis(self, **kw):
        kw.setdefault("granularity", "week")
        kw.setdefault("anchor", date(2026, 10, 5))
        with self.factory() as s:
            return compute_kpis(s, now=NOW, **kw)

    def test_deposits_counted_once_under_group_club(self):
        out = self._kpis()
        dep = out["current"]["deposits"]
        self.assertEqual(dep["count"], 7)
        self.assertEqual(dep["amount_usd"], 905.0)
        rails = {r["rail"]: (r["count"], r["amount_usd"]) for r in dep["by_rail"]}
        self.assertEqual(
            rails,
            {
                "venmo": (1, 100.0),
                "zelle": (1, 200.0),
                "cashapp": (1, 30.0),
                "paypal": (1, 10.0),
                "crypto": (1, 500.0),
                "stripe": (1, 25.0),
                "pool_pay": (1, 40.0),
            },
        )
        by_club = {c["club_name"]: c for c in out["by_club"]}
        self.assertEqual(by_club["Round Table"]["deposits_count"], 3)
        self.assertEqual(by_club["Round Table"]["deposits_usd"], 135.0)
        self.assertEqual(by_club["Creator Club"]["deposits_usd"], 530.0)
        self.assertEqual(by_club["ClubGTO"]["deposits_usd"], 240.0)
        self.assertEqual(sum(c["deposits_count"] for c in out["by_club"]), 7)
        self.assertEqual(out["prior"]["deposits"]["count"], 1)

    def test_club_filter(self):
        out = self._kpis(club_id=GTO)
        self.assertEqual(out["club_name"], "ClubGTO")
        self.assertEqual(out["current"]["deposits"]["count"], 2)
        self.assertEqual(out["by_club"], [])
        self.assertEqual(out["current"]["tickets"]["total"], 2)

    def test_new_players(self):
        new = self._kpis()["current"]["new_players"]
        self.assertEqual(new["joined"], 3)  # -100, -102, -103
        self.assertEqual(new["converted"], 2)  # -100 now, -102 after the week
        self.assertAlmostEqual(new["conversion_rate"], 0.6667)
        self.assertEqual(new["first_deposits"], 2)  # -100 (stripe), -106 (zelle)
        prior = self._kpis()["prior"]["new_players"]
        self.assertEqual((prior["joined"], prior["converted"]), (1, 1))
        self.assertEqual(prior["first_deposits"], 1)

    def test_response_time_human_tickets_only(self):
        out = self._kpis()
        rt = out["current"]["response_time"]
        self.assertEqual(rt["tickets"], 5)
        self.assertEqual(rt["human_answered"], 4)
        self.assertEqual(rt["no_human_reply"], 1)
        self.assertEqual(rt["median_seconds"], 90.0)
        # FRTs 30, 60, 120, 400: exactly 2 minutes counts as under 2 minutes.
        self.assertEqual(rt["under_2m"], 3)
        self.assertEqual(rt["pct_under_2m"], 0.75)
        dist = {b["key"]: b["count"] for b in rt["distribution"]}
        self.assertEqual(
            dist,
            {"lt_1m": 2, "1_2m": 1, "2_5m": 0, "5_15m": 1, "15_60m": 0, "gt_60m": 0},
        )
        cats = {
            c["category"]: c["count"] for c in out["current"]["tickets"]["by_category"]
        }
        self.assertEqual(cats["other"], 1)
        self.assertEqual(cats["bonus"], 0)
        self.assertEqual(len(cats), 9)
        self.assertEqual(
            out["freshness"]["tickets_complete_through"], date(2026, 10, 10)
        )
        self.assertTrue(out["period"]["tickets_pending"])
        self.assertFalse(out["previous"]["tickets_pending"])

    def test_automation(self):
        auto = self._kpis()["current"]["automation"]
        overall = auto["overall"]
        self.assertEqual(
            (
                overall["completed"],
                overall["failed"],
                overall["stuck"],
                overall["stale"],
            ),
            (4, 1, 1, 1),
        )
        self.assertEqual(overall["attempted"], 6)
        self.assertAlmostEqual(overall["rate"], 0.6667)
        by_source = {r["source"]: r for r in auto["by_source"]}
        self.assertEqual(by_source["payment_auto_deposit"]["rate"], 0.6)
        self.assertEqual(by_source["early_rakeback"]["rate"], 1.0)
        self.assertIsNone(by_source["transfer"]["rate"])
        self.assertNotIn("staff_add", by_source)

    def test_trend_matches_period(self):
        out = self._kpis()
        self.assertEqual(len(out["trend"]), 8)
        last = out["trend"][-1]
        self.assertEqual(last["start"], date(2026, 10, 5))
        self.assertEqual(last["deposits_count"], 7)
        self.assertEqual(out["trend"][-2]["deposits_count"], 1)

    def test_day_view(self):
        out = self._kpis(granularity="day", anchor=date(2026, 10, 7))
        self.assertEqual(out["current"]["deposits"]["count"], 2)  # venmo + zelle
        self.assertEqual(len(out["trend"]), 14)


class KpiRouteTests(_SeededDb):
    def _client(self, role):
        app = FastAPI()
        app.include_router(router)

        def override_db():
            session = self.factory()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db_dependency] = override_db
        app.dependency_overrides[get_current_admin] = lambda: role
        return TestClient(app)

    def test_admin(self):
        r = self._client("admin").get(
            "/api/kpis", params={"granularity": "week", "date": "2026-10-05"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["period"]["label"], "Oct 5 – 11")
        self.assertEqual(len(body["by_club"]), 3)

    def test_gto_is_scoped(self):
        client = self._client("gto")
        r = client.get("/api/kpis", params={"date": "2026-10-05"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["club_id"], GTO)
        self.assertEqual(r.json()["by_club"], [])
        r = client.get("/api/kpis", params={"date": "2026-10-05", "club_id": RT})
        self.assertEqual(r.status_code, 403)

    def test_account_manager_denied(self):
        r = self._client("account_manager").get("/api/kpis")
        self.assertEqual(r.status_code, 403)

    def test_bad_input(self):
        client = self._client("admin")
        self.assertEqual(
            client.get("/api/kpis", params={"date": "10/05"}).status_code, 400
        )
        r = client.get(
            "/api/kpis", params={"granularity": "custom", "from": "2026-10-01"}
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(
            client.get("/api/kpis", params={"granularity": "x"}).status_code, 400
        )


if __name__ == "__main__":
    unittest.main()
