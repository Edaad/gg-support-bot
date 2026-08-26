"""Tests for union / manual trade-request deposit methods."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from api.payment_v2_helpers import apply_manual_trade_request_constraints
from bot.services.deposit_method_access import (
    _method_belongs_to_club,
    filter_deposit_methods_for_chat,
    method_visible_for_chat,
)
from bot.services.manual_deposit_requests import (
    ManualDepositCapacityError,
    capacity_allows,
    create_request_atomic,
)


class ApplyManualTradeConstraintsTests(unittest.TestCase):
    def test_requires_limit_and_message(self):
        method = SimpleNamespace(
            tracks_manual_requests=True,
            direction="deposit",
            deposit_limit=None,
            manual_request_message="hi",
            manual_request_variant_name="v1",
            tiers=[],
            is_public=True,
        )
        with self.assertRaises(ValueError):
            apply_manual_trade_request_constraints(method)

        method.deposit_limit = Decimal("1000")
        method.manual_request_message = "  "
        with self.assertRaises(ValueError):
            apply_manual_trade_request_constraints(method)

        method.manual_request_message = "Send here"
        apply_manual_trade_request_constraints(method)
        self.assertIsNone(method.manual_request_variant_name)

    def test_forces_off_linking_and_sets_public(self):
        method = SimpleNamespace(
            tracks_manual_requests=True,
            direction="deposit",
            deposit_limit=Decimal("500"),
            manual_request_message=" Pay me ",
            manual_request_variant_name=" Union ",
            has_sub_options=True,
            first_time_linking_enabled=True,
            first_time_bind_mode="special_amount",
            tiers=[],
            is_public=False,
        )
        apply_manual_trade_request_constraints(method)
        self.assertFalse(method.has_sub_options)
        self.assertFalse(method.first_time_linking_enabled)
        self.assertIsNone(method.first_time_bind_mode)
        self.assertTrue(method.is_public)
        self.assertEqual(method.manual_request_message, "Pay me")
        self.assertIsNone(method.manual_request_variant_name)

    def test_rejects_stripe_on_tiers(self):
        tier = SimpleNamespace(use_group_checkout_link=True, variants=[])
        method = SimpleNamespace(
            tracks_manual_requests=True,
            direction="deposit",
            deposit_limit=Decimal("100"),
            manual_request_message="x",
            manual_request_variant_name="y",
            has_sub_options=False,
            first_time_linking_enabled=False,
            first_time_bind_mode=None,
            tiers=[tier],
            is_public=True,
        )
        with self.assertRaises(ValueError):
            apply_manual_trade_request_constraints(method)

    def test_noop_when_flag_off(self):
        method = SimpleNamespace(
            tracks_manual_requests=False,
            has_sub_options=True,
            first_time_linking_enabled=True,
        )
        apply_manual_trade_request_constraints(method)
        self.assertTrue(method.has_sub_options)


class CapacityAllowsTests(unittest.TestCase):
    def test_amount_aware_remaining_capacity(self):
        session = MagicMock()
        with patch(
            "bot.services.manual_deposit_requests.sum_for_method",
            return_value=Decimal("900"),
        ):
            self.assertTrue(
                capacity_allows(
                    session,
                    method_id=1,
                    amount=Decimal("50"),
                    deposit_limit=Decimal("1000"),
                )
            )
            self.assertFalse(
                capacity_allows(
                    session,
                    method_id=1,
                    amount=Decimal("200"),
                    deposit_limit=Decimal("1000"),
                )
            )


class MethodBelongsToClubTests(unittest.TestCase):
    def test_union_uses_junction_only(self):
        method = SimpleNamespace(
            club_id=1,
            tracks_manual_requests=True,
            method_clubs=[SimpleNamespace(club_id=2), SimpleNamespace(club_id=3)],
        )
        self.assertFalse(_method_belongs_to_club(method, 1))
        self.assertTrue(_method_belongs_to_club(method, 2))

    def test_normal_uses_club_id(self):
        method = SimpleNamespace(
            club_id=5,
            tracks_manual_requests=False,
            method_clubs=[],
        )
        self.assertTrue(_method_belongs_to_club(method, 5))
        self.assertFalse(_method_belongs_to_club(method, 6))


class WhitelistOnlyTests(unittest.TestCase):
    def test_empty_whitelist_hides_private_method(self):
        self.assertFalse(
            method_visible_for_chat(is_public=False, access_type=None)
        )
        self.assertTrue(
            method_visible_for_chat(is_public=False, access_type="whitelist")
        )

    def test_filter_deposit_methods_requires_whitelist(self):
        methods = [
            {"id": 10, "is_public": False, "name": "Union"},
            {"id": 11, "is_public": True, "name": "Venmo"},
        ]
        with patch(
            "bot.services.deposit_method_access.get_db"
        ) as get_db, patch(
            "bot.services.deposit_method_access._access_map_for_chat",
            return_value={10: "whitelist"},
        ):
            session = MagicMock()
            cm = MagicMock()
            cm.__enter__.return_value = session
            cm.__exit__.return_value = False
            get_db.return_value = cm
            shown = filter_deposit_methods_for_chat(-100, methods)
        self.assertEqual([m["id"] for m in shown], [10, 11])

        with patch(
            "bot.services.deposit_method_access.get_db"
        ) as get_db, patch(
            "bot.services.deposit_method_access._access_map_for_chat",
            return_value={},
        ):
            session = MagicMock()
            cm = MagicMock()
            cm.__enter__.return_value = session
            cm.__exit__.return_value = False
            get_db.return_value = cm
            shown = filter_deposit_methods_for_chat(-100, methods)
        self.assertEqual([m["id"] for m in shown], [11])


def _mock_session_for_get_methods(*, normal, union):
    """Build a session whose query chain returns normal then union lists."""
    session = MagicMock()

    normal_q = MagicMock()
    normal_q.filter_by.return_value = normal_q
    normal_q.filter.return_value = normal_q
    normal_q.order_by.return_value = normal_q
    normal_q.all.return_value = normal

    union_q = MagicMock()
    union_q.join.return_value = union_q
    union_q.filter.return_value = union_q
    union_q.options.return_value = union_q
    union_q.order_by.return_value = union_q
    union_q.all.return_value = union

    # First query(...) is normal methods; second is union.
    session.query.side_effect = [normal_q, union_q]
    return session


class GetMethodsForAmountManualTests(unittest.TestCase):
    def test_hides_when_amount_exceeds_remaining(self):
        from bot.services import club_payment_v2

        method = SimpleNamespace(
            id=7,
            name="Zelle",
            slug="zelle-union",
            min_amount=None,
            max_amount=None,
            has_sub_options=False,
            is_public=False,
            tracks_manual_requests=True,
            manual_request_message="msg",
            manual_request_variant_name="v",
            deposit_limit=Decimal("1000"),
            accumulated_amount=Decimal("0"),
        )
        session = _mock_session_for_get_methods(normal=[], union=[method])
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch.object(club_payment_v2, "get_db", return_value=cm), patch(
            "bot.services.manual_deposit_requests.sum_for_method",
            return_value=Decimal("900"),
        ):
            shown = club_payment_v2.get_methods_for_amount(
                1, "deposit", Decimal("200")
            )
            self.assertEqual(shown, [])

        session2 = _mock_session_for_get_methods(normal=[], union=[method])
        cm2 = MagicMock()
        cm2.__enter__.return_value = session2
        cm2.__exit__.return_value = False
        with patch.object(club_payment_v2, "get_db", return_value=cm2), patch(
            "bot.services.manual_deposit_requests.sum_for_method",
            return_value=Decimal("900"),
        ):
            shown_ok = club_payment_v2.get_methods_for_amount(
                1, "deposit", Decimal("50")
            )
            self.assertEqual(len(shown_ok), 1)
            self.assertTrue(shown_ok[0]["tracks_manual_requests"])

    def test_appends_union_after_normal(self):
        from bot.services import club_payment_v2

        normal = SimpleNamespace(
            id=1,
            name="Venmo",
            slug="venmo",
            min_amount=None,
            max_amount=None,
            has_sub_options=False,
            is_public=True,
            tracks_manual_requests=False,
            manual_request_message=None,
            manual_request_variant_name=None,
            deposit_limit=None,
            accumulated_amount=Decimal("0"),
        )
        union = SimpleNamespace(
            id=9,
            name="Zelle Union",
            slug="zelle-union",
            min_amount=None,
            max_amount=None,
            has_sub_options=False,
            is_public=False,
            tracks_manual_requests=True,
            manual_request_message="msg",
            manual_request_variant_name="v",
            deposit_limit=Decimal("5000"),
            accumulated_amount=Decimal("0"),
        )
        session = _mock_session_for_get_methods(normal=[normal], union=[union])
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        with patch.object(club_payment_v2, "get_db", return_value=cm), patch(
            "bot.services.manual_deposit_requests.sum_for_method",
            return_value=Decimal("0"),
        ):
            shown = club_payment_v2.get_methods_for_amount(
                1, "deposit", Decimal("100")
            )
        self.assertEqual([m["slug"] for m in shown], ["venmo", "zelle-union"])

    def test_hides_when_amount_outside_min_max(self):
        from bot.services import club_payment_v2

        method = SimpleNamespace(
            id=8,
            name="Zelle",
            slug="zelle-union",
            min_amount=Decimal("50"),
            max_amount=Decimal("200"),
            has_sub_options=False,
            is_public=False,
            tracks_manual_requests=True,
            manual_request_message="msg",
            manual_request_variant_name="v",
            deposit_limit=Decimal("10000"),
            accumulated_amount=Decimal("0"),
        )

        def run(amount):
            session = _mock_session_for_get_methods(normal=[], union=[method])
            cm = MagicMock()
            cm.__enter__.return_value = session
            cm.__exit__.return_value = False
            with patch.object(club_payment_v2, "get_db", return_value=cm), patch(
                "bot.services.manual_deposit_requests.sum_for_method",
                return_value=Decimal("0"),
            ):
                return club_payment_v2.get_methods_for_amount(1, "deposit", amount)

        self.assertEqual(run(Decimal("25")), [])
        self.assertEqual(run(Decimal("250")), [])
        self.assertEqual(len(run(Decimal("100"))), 1)


class CreateRequestAtomicTests(unittest.TestCase):
    def test_rejects_over_capacity(self):
        method = SimpleNamespace(
            id=3,
            name="Zelle",
            slug="zelle-union",
            tracks_manual_requests=True,
            is_active=True,
            deposit_limit=Decimal("1000"),
            manual_request_variant_name="Union",
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = (
            method
        )
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch(
            "bot.services.manual_deposit_requests.get_db", return_value=cm
        ), patch(
            "bot.services.manual_deposit_requests.capacity_allows",
            return_value=False,
        ):
            with self.assertRaises(ManualDepositCapacityError):
                create_request_atomic(
                    club_id=1,
                    method_id=3,
                    amount=Decimal("200"),
                    telegram_chat_id=-100,
                    group_title="GTO / 2222-2222 / jz",
                )


class ManualTradeLedgerFetchTests(unittest.TestCase):
    def test_checked_rows_included_unchecked_excluded(self):
        from api import audit_ledger

        checked = SimpleNamespace(
            id=1,
            club_id=10,
            telegram_chat_id=-100,
            group_title="GTO / 2222-2222 / jz",
            method_slug="zelle-union",
            method_name="Zelle",
            variant_name="Union",
            amount=Decimal("100"),
            created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
            trade_record_checked=True,
        )
        session = MagicMock()
        q = MagicMock()
        session.query.return_value = q
        q.filter.return_value = q
        q.order_by.return_value.all.return_value = [checked]

        with patch.object(
            audit_ledger, "apply_analytics_payment_exclusion", side_effect=lambda s, qq, c: qq
        ), patch.object(
            audit_ledger, "payment_in_audit_day_for_club", return_value=True
        ), patch.object(
            audit_ledger,
            "resolve_group_title",
            return_value=("GTO / 2222-2222 / jz", "2222-2222"),
        ):
            events = audit_ledger._fetch_manual_trade_request_events(
                session,
                club_slug="clubgto",
                audit_date=date(2026, 8, 25),
                from_dt=datetime(2026, 8, 25, tzinfo=timezone.utc),
                to_dt=datetime(2026, 8, 26, tzinfo=timezone.utc),
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source, "deposit_zelle-union")
        self.assertEqual(events[0].source_label, "Zelle")
        self.assertEqual(events[0].gg_player_id, "2222-2222")
        self.assertEqual(events[0].amount_usd, Decimal("100"))


class UnionMethodsApiHelpersTests(unittest.TestCase):
    def test_sync_method_clubs_sets_anchor(self):
        from api.routes.union_methods import _sync_method_clubs

        db = MagicMock()
        method = SimpleNamespace(id=5, club_id=1, method_clubs=[])
        clubs = [
            SimpleNamespace(id=1, name="RT"),
            SimpleNamespace(id=3, name="CC"),
        ]
        _sync_method_clubs(db, method, clubs)
        self.assertEqual(method.club_id, 1)
        self.assertEqual(db.add.call_count, 2)


class ManualDepositRequestListQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from db.models import Base, Club, ClubPaymentMethod, ManualDepositRequest

        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[
                Club.__table__,
                ClubPaymentMethod.__table__,
                ManualDepositRequest.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine)
        session = self.Session()
        session.add(Club(id=1, name="Round Table", telegram_user_id=1001, is_active=True))
        session.add(Club(id=2, name="ClubGTO", telegram_user_id=1002, is_active=True))
        session.add(
            ClubPaymentMethod(
                id=10,
                club_id=1,
                direction="deposit",
                name="Zelle",
                slug="zelle-union",
                deposit_limit=Decimal("1000"),
                tracks_manual_requests=True,
                is_active=True,
                is_public=False,
                manual_request_message="pay",
                manual_request_variant_name="v1",
            )
        )
        session.add(
            ManualDepositRequest(
                id=1,
                club_id=1,
                method_id=10,
                method_name="Zelle",
                method_slug="zelle-union",
                variant_name="v1",
                group_title="RT / 1111-1111 / Alice",
                amount=Decimal("500.00"),
                telegram_chat_id=-1001,
                trade_record_checked=False,
            )
        )
        session.add(
            ManualDepositRequest(
                id=2,
                club_id=2,
                method_id=10,
                method_name="Zelle",
                method_slug="zelle-union",
                variant_name="v1",
                group_title="GTO / 2222-2222 / Bob",
                amount=Decimal("75.50"),
                telegram_chat_id=-1002,
                trade_record_checked=True,
            )
        )
        session.commit()
        session.close()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_q_matches_group_title(self):
        from api.routes.manual_deposit_requests import _list_query

        session = self.Session()
        try:
            rows = _list_query(session, q="Alice").all()
            self.assertEqual([r.id for r in rows], [1])
        finally:
            session.close()

    def test_q_matches_club_name(self):
        from api.routes.manual_deposit_requests import _list_query

        session = self.Session()
        try:
            rows = _list_query(session, q="clubgto").all()
            self.assertEqual([r.id for r in rows], [2])
        finally:
            session.close()

    def test_q_matches_numeric_amount(self):
        from api.routes.manual_deposit_requests import _list_query

        session = self.Session()
        try:
            rows = _list_query(session, q="500").all()
            self.assertEqual([r.id for r in rows], [1])
            rows_exact = _list_query(session, q="75.50").all()
            self.assertEqual([r.id for r in rows_exact], [2])
        finally:
            session.close()

    def test_empty_q_returns_all(self):
        from api.routes.manual_deposit_requests import _list_query

        session = self.Session()
        try:
            rows = _list_query(session, q="  ").all()
            self.assertEqual(sorted(r.id for r in rows), [1, 2])
        finally:
            session.close()

    def test_q_matches_tag(self):
        from api.routes.manual_deposit_requests import _list_query

        session = self.Session()
        try:
            rows = _list_query(session, q="zelle-union").all()
            self.assertEqual(sorted(r.id for r in rows), [1, 2])
        finally:
            session.close()

    def test_method_type_filter(self):
        from api.routes.manual_deposit_requests import _list_query

        session = self.Session()
        try:
            rows = _list_query(session, method_type="zelle").all()
            self.assertEqual(sorted(r.id for r in rows), [1, 2])
        finally:
            session.close()

    def test_row_clubs_includes_non_member_club(self):
        from api.routes.union_methods import _row_clubs_for_methods

        session = self.Session()
        try:
            by_method = _row_clubs_for_methods(session, [10])
            ids = [c.id for c in by_method[10]]
            self.assertEqual(ids, [1, 2])
            names = {c.id: c.name for c in by_method[10]}
            self.assertEqual(names[1], "Round Table")
            self.assertEqual(names[2], "ClubGTO")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
