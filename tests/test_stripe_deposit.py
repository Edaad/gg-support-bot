"""Unit tests for Stripe deposit customer + checkout session attachment."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import IntegrityError

from bot.services import stripe_deposit as sd
from db.models import StripeCheckoutSession, StripeCustomer


CHAT_ID = -1001234567890
CLUB_ID = 2
GROUP_TITLE = "RT / 6485-8168 / Angus Mcgoon"


class FakeQuery:
    def __init__(self, store: "FakeSessionStore", model):
        self._store = store
        self._model = model
        self._telegram_chat_id = None
        self._stripe_customer_id = None

    def filter(self, *args, **kwargs):
        for expr in args:
            left = getattr(expr, "left", None)
            right = getattr(expr, "right", None)
            if left is not None and hasattr(left, "key"):
                if left.key == "telegram_chat_id" and right is not None:
                    self._telegram_chat_id = int(right.value if hasattr(right, "value") else right)
                if left.key == "stripe_customer_id" and right is not None:
                    self._stripe_customer_id = str(
                        right.value if hasattr(right, "value") else right
                    )
        return self

    def one_or_none(self):
        if self._model is StripeCustomer:
            if self._telegram_chat_id is not None:
                return self._store.customers.get(self._telegram_chat_id)
            if self._stripe_customer_id is not None:
                for row in self._store.customers.values():
                    if row.stripe_customer_id == self._stripe_customer_id:
                        return row
        return None


class FakeSessionStore:
    def __init__(self):
        self.customers: dict[int, StripeCustomer] = {}
        self.checkout_sessions: list[StripeCheckoutSession] = []

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        if isinstance(obj, StripeCustomer):
            if obj.telegram_chat_id in self.customers:
                raise IntegrityError("", {}, Exception("unique telegram_chat_id"))
            self.customers[obj.telegram_chat_id] = obj
        elif isinstance(obj, StripeCheckoutSession):
            self.checkout_sessions.append(obj)

    def flush(self):
        pass


def _make_store_with_customer(customer_id: str = "cus_existing") -> FakeSessionStore:
    store = FakeSessionStore()
    store.customers[CHAT_ID] = StripeCustomer(
        telegram_chat_id=CHAT_ID,
        club_id=CLUB_ID,
        stripe_customer_id=customer_id,
        gg_player_id="6485-8168",
        player_display_name="Angus Mcgoon",
    )
    return store


def _stripe_mocks():
    product = SimpleNamespace(id="prod_test")
    price = SimpleNamespace(id="price_test")
    customer = SimpleNamespace(id="cus_new")
    checkout = SimpleNamespace(
        id="cs_test",
        url="https://checkout.stripe.com/test",
    )
    return product, price, customer, checkout


class StripeDepositTestCase(unittest.TestCase):
    def test_resolve_checkout_amount_cents_from_dashboard(self):
        min_c, max_c, preset = sd.resolve_checkout_amount_cents(min_usd=25, max_usd=75)
        self.assertEqual(min_c, 2500)
        self.assertEqual(max_c, 7500)
        self.assertEqual(preset, 5000)

    def test_resolve_checkout_amount_cents_defaults(self):
        min_c, max_c, preset = sd.resolve_checkout_amount_cents()
        self.assertEqual(min_c, sd.STRIPE_CHECKOUT_MIN_CENTS)
        self.assertEqual(max_c, sd.STRIPE_CHECKOUT_MAX_CENTS)
        self.assertEqual(preset, sd.STRIPE_CHECKOUT_PRESET_CENTS)

    def test_resolve_checkout_amount_cents_from_deposit_amount(self):
        min_c, max_c, preset = sd.resolve_checkout_amount_cents(
            min_usd=20, max_usd=100, preset_usd=100
        )
        self.assertEqual(min_c, 2000)
        self.assertEqual(max_c, 10000)
        self.assertEqual(preset, 10000)

    def test_resolve_checkout_amount_cents_preset_clamped_to_max(self):
        _, _, preset = sd.resolve_checkout_amount_cents(
            min_usd=20, max_usd=100, preset_usd=150
        )
        self.assertEqual(preset, 10000)

    def test_resolve_checkout_amount_cents_preset_clamped_to_min(self):
        _, _, preset = sd.resolve_checkout_amount_cents(
            min_usd=20, max_usd=100, preset_usd=10
        )
        self.assertEqual(preset, 2000)

    def setUp(self):
        self.env_patch = patch.dict(
            os.environ,
            {"STRIPE_SECRET_KEY": "sk_test_fake"},
            clear=False,
        )
        self.env_patch.start()
        sd.stripe.api_key = "sk_test_fake"

    def tearDown(self):
        self.env_patch.stop()

    @contextmanager
    def _db(self, store: FakeSessionStore):
        @contextmanager
        def fake_get_db():
            yield store

        with patch.object(sd, "get_db", fake_get_db):
            yield

    @contextmanager
    def _club_mocks(self):
        with (
            patch.object(sd, "update_group_name"),
            patch.object(
                sd,
                "get_group_title_for_chat",
                return_value=(GROUP_TITLE, CLUB_ID),
            ),
        ):
            yield

    def test_existing_customer_reuse(self):
        """A: Reuse cus_existing; do not call stripe.Customer.create."""
        store = _make_store_with_customer("cus_existing")
        product, price, customer, checkout = _stripe_mocks()

        with (
            self._db(store),
            self._club_mocks(),
            patch.object(sd.stripe.Customer, "create", return_value=customer) as cust_create,
            patch.object(sd.stripe.Product, "create", return_value=product),
            patch.object(sd.stripe.Price, "create", return_value=price) as price_create,
            patch.object(
                sd.stripe.checkout.Session,
                "create",
                return_value=checkout,
            ) as session_create,
        ):
            result = sd.create_stripe_checkout_session(
                telegram_chat_id=CHAT_ID,
                club_id=CLUB_ID,
                group_title=GROUP_TITLE,
            )

        cust_create.assert_not_called()
        session_create.assert_called_once()
        self.assertEqual(session_create.call_args.kwargs["customer"], "cus_existing")
        self.assertEqual(result.customer_id, "cus_existing")
        price_kwargs = price_create.call_args.kwargs
        self.assertEqual(
            price_kwargs["custom_unit_amount"]["minimum"], sd.STRIPE_CHECKOUT_MIN_CENTS
        )
        self.assertEqual(
            price_kwargs["custom_unit_amount"]["maximum"], sd.STRIPE_CHECKOUT_MAX_CENTS
        )
        self.assertEqual(
            price_kwargs["custom_unit_amount"]["preset"], sd.STRIPE_CHECKOUT_PRESET_CENTS
        )

    def test_checkout_session_uses_deposit_preset(self):
        store = _make_store_with_customer("cus_existing")
        product, price, customer, checkout = _stripe_mocks()

        with (
            self._db(store),
            self._club_mocks(),
            patch.object(sd.stripe.Customer, "create", return_value=customer),
            patch.object(sd.stripe.Product, "create", return_value=product),
            patch.object(sd.stripe.Price, "create", return_value=price) as price_create,
            patch.object(
                sd.stripe.checkout.Session,
                "create",
                return_value=checkout,
            ),
        ):
            sd.create_stripe_checkout_session(
                telegram_chat_id=CHAT_ID,
                club_id=CLUB_ID,
                group_title=GROUP_TITLE,
                checkout_min_usd=20,
                checkout_max_usd=100,
                checkout_preset_usd=100,
            )

        price_kwargs = price_create.call_args.kwargs
        self.assertEqual(price_kwargs["custom_unit_amount"]["minimum"], 2000)
        self.assertEqual(price_kwargs["custom_unit_amount"]["maximum"], 10000)
        self.assertEqual(price_kwargs["custom_unit_amount"]["preset"], 10000)

    def test_new_customer_creation(self):
        """B: Create one Stripe Customer and attach checkout to it."""
        store = FakeSessionStore()
        product, price, customer, checkout = _stripe_mocks()

        with (
            self._db(store),
            self._club_mocks(),
            patch.object(sd.stripe.Customer, "create", return_value=customer) as cust_create,
            patch.object(sd.stripe.Product, "create", return_value=product),
            patch.object(sd.stripe.Price, "create", return_value=price),
            patch.object(
                sd.stripe.checkout.Session,
                "create",
                return_value=checkout,
            ) as session_create,
        ):
            result = sd.create_stripe_checkout_session(
                telegram_chat_id=CHAT_ID,
                club_id=CLUB_ID,
                group_title=GROUP_TITLE,
            )

        cust_create.assert_called_once()
        self.assertEqual(len(store.customers), 1)
        self.assertEqual(store.customers[CHAT_ID].stripe_customer_id, "cus_new")
        session_create.assert_called_once()
        self.assertEqual(session_create.call_args.kwargs["customer"], "cus_new")
        self.assertEqual(result.customer_id, "cus_new")

    def test_no_duplicate_customer_on_repeat_deposits(self):
        """C: Two checkouts share one cus_... mapping."""
        store = FakeSessionStore()
        product, price, customer, checkout = _stripe_mocks()
        checkout2 = SimpleNamespace(
            id="cs_test_2",
            url="https://checkout.stripe.com/test2",
        )

        with (
            self._db(store),
            self._club_mocks(),
            patch.object(sd.stripe.Customer, "create", return_value=customer) as cust_create,
            patch.object(sd.stripe.Product, "create", return_value=product),
            patch.object(sd.stripe.Price, "create", return_value=price),
            patch.object(
                sd.stripe.checkout.Session,
                "create",
                side_effect=[checkout, checkout2],
            ) as session_create,
        ):
            first = sd.create_stripe_checkout_session(
                telegram_chat_id=CHAT_ID,
                club_id=CLUB_ID,
                group_title=GROUP_TITLE,
            )
            second = sd.create_stripe_checkout_session(
                telegram_chat_id=CHAT_ID,
                club_id=CLUB_ID,
                group_title=GROUP_TITLE,
            )

        cust_create.assert_called_once()
        self.assertEqual(len(store.customers), 1)
        self.assertEqual(first.customer_id, "cus_new")
        self.assertEqual(second.customer_id, "cus_new")
        self.assertEqual(session_create.call_count, 2)
        for call in session_create.call_args_list:
            self.assertEqual(call.kwargs["customer"], "cus_new")
        self.assertEqual(len(store.checkout_sessions), 0)

    def test_checkout_session_metadata_and_client_reference(self):
        """D: Session carries client_reference_id and metadata."""
        store = _make_store_with_customer("cus_existing")
        product, price, customer, checkout = _stripe_mocks()

        with (
            self._db(store),
            self._club_mocks(),
            patch.object(sd.stripe.Customer, "create", return_value=customer),
            patch.object(sd.stripe.Product, "create", return_value=product),
            patch.object(sd.stripe.Price, "create", return_value=price),
            patch.object(
                sd.stripe.checkout.Session,
                "create",
                return_value=checkout,
            ) as session_create,
        ):
            sd.create_stripe_checkout_session(
                telegram_chat_id=CHAT_ID,
                club_id=CLUB_ID,
                group_title=GROUP_TITLE,
            )

        kwargs = session_create.call_args.kwargs
        self.assertEqual(kwargs["client_reference_id"], str(CHAT_ID))
        self.assertEqual(kwargs["mode"], "payment")
        self.assertIn("telegram_chat_id", kwargs["metadata"])
        self.assertEqual(kwargs["metadata"]["telegram_chat_id"], str(CHAT_ID))
        self.assertEqual(kwargs["metadata"]["club_id"], str(CLUB_ID))
        self.assertEqual(kwargs["metadata"]["gg_player_id"], "6485-8168")

    def test_checkout_metadata_includes_payment_method_id(self):
        store = _make_store_with_customer("cus_existing")
        product, price, customer, checkout = _stripe_mocks()

        with (
            self._db(store),
            self._club_mocks(),
            patch.object(sd.stripe.Customer, "create", return_value=customer),
            patch.object(sd.stripe.Product, "create", return_value=product),
            patch.object(sd.stripe.Price, "create", return_value=price),
            patch.object(sd.stripe.checkout.Session, "create", return_value=checkout) as session_create,
        ):
            sd.create_stripe_checkout_session(
                telegram_chat_id=CHAT_ID,
                club_id=CLUB_ID,
                payment_method_id=42,
                group_title=GROUP_TITLE,
            )

        meta = session_create.call_args.kwargs["metadata"]
        self.assertEqual(meta["payment_method_id"], "42")
        self.assertEqual(len(store.checkout_sessions), 0)

    def test_integrity_error_reuses_existing_customer(self):
        """Race: duplicate insert returns existing cus_existing."""
        store = FakeSessionStore()
        existing = StripeCustomer(
            telegram_chat_id=CHAT_ID,
            club_id=CLUB_ID,
            stripe_customer_id="cus_existing",
        )
        product, price, customer, checkout = _stripe_mocks()
        lookup_reads = [None, existing]

        original_query = store.query

        def query_with_race(model):
            q = original_query(model)
            if model is not StripeCustomer:
                return q

            def one_or_none():
                if lookup_reads:
                    return lookup_reads.pop(0)
                return q.one_or_none()

            q.one_or_none = one_or_none  # type: ignore[method-assign]
            return q

        store.query = query_with_race  # type: ignore[method-assign]

        def add_raise_on_customer(obj):
            if isinstance(obj, StripeCustomer):
                raise IntegrityError("", {}, Exception("race"))
            store.checkout_sessions.append(obj)

        store.add = add_raise_on_customer  # type: ignore[method-assign]

        with (
            self._db(store),
            patch.object(sd.stripe.Customer, "create", return_value=customer) as cust_create,
        ):
            result = sd.get_or_create_stripe_customer(
                telegram_chat_id=CHAT_ID,
                club_id=CLUB_ID,
                group_title=GROUP_TITLE,
            )

        cust_create.assert_called_once()
        self.assertEqual(result, "cus_existing")


if __name__ == "__main__":
    unittest.main()
