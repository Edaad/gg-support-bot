"""Round Table Venmo and Cash App balance cards."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.account_balances import (
    list_account_balances,
    list_blocking_sends,
    offset_cents_for_balance,
    update_account_balance_settings,
)
from api.auth import create_token
from api.routes.account_balances import router
from db.models import (
    AccountBalanceSettings,
    Base,
    CashAppPayment,
    Club,
    ClubPaymentMethod,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    CryptoPayment,
    OutboundSend,
    PayPalPayment,
    VenmoPayment,
)

NOW = datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc)
WINDOW = timedelta(hours=168)


class AccountBalanceServiceTestCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        tables = [
            Club.__table__,
            ClubPaymentMethod.__table__,
            ClubPaymentTier.__table__,
            ClubPaymentTierVariant.__table__,
            VenmoPayment.__table__,
            CashAppPayment.__table__,
            OutboundSend.__table__,
            PayPalPayment.__table__,
            CryptoPayment.__table__,
            Base.metadata.tables["account_balance_settings"],
        ]
        Base.metadata.create_all(engine, tables=tables)
        self.session = sessionmaker(bind=engine)()
        self.rt = Club(name="Round Table", telegram_user_id=1)
        self.other = Club(name="ClubGTO", telegram_user_id=2)
        self.session.add_all([self.rt, self.other])
        self.session.flush()
        self.methods: dict[tuple[int, str, str], ClubPaymentMethod] = {}
        self._add_variant(self.rt, "venmo", "deposit", "@Godfather4444")
        self._add_variant(self.rt, "venmo", "cashout", "godfather4444")
        self._add_variant(self.rt, "venmo", "deposit", "@janseashells")
        self._add_variant(self.rt, "cashapp", "deposit", "creatorclub")
        self._add_variant(self.rt, "cashapp", "deposit", "$ClubRound")
        self._add_variant(self.other, "venmo", "deposit", "@notours")
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def _add_variant(self, club: Club, slug: str, direction: str, tag: str) -> None:
        key = (club.id, slug, direction)
        method = self.methods.get(key)
        if method is None:
            method = ClubPaymentMethod(
                club_id=club.id,
                direction=direction,
                name=slug,
                slug=slug,
            )
            self.session.add(method)
            self.session.flush()
            tier = ClubPaymentTier(method_id=method.id, label="Default")
            self.session.add(tier)
            self.session.flush()
            method.tier_id = tier.id  # type: ignore[attr-defined]
            self.methods[key] = method
        tier_id = method.tier_id  # type: ignore[attr-defined]
        legacy = {"venmo_tag": tag} if slug == "venmo" else {"cashapp_tag": tag}
        self.session.add(
            ClubPaymentTierVariant(
                method_id=method.id,
                tier_id=tier_id,
                label=tag,
                **legacy,
            )
        )
        self.session.flush()

    def _venmo(self, handle: str, amount_cents: int, **kwargs) -> None:
        self.session.add(
            VenmoPayment(
                method_owner=kwargs.get("method_owner", "round-table"),
                payer_name="Payer",
                amount_cents=amount_cents,
                venmo_handle=handle,
                paid_at=kwargs.get("paid_at"),
                created_at=kwargs.get("created_at", NOW),
                is_test=kwargs.get("is_test", False),
            )
        )

    def _cashapp(self, handle: str, amount_cents: int, **kwargs) -> None:
        self.session.add(
            CashAppPayment(
                method_owner=kwargs.get("method_owner", "round-table"),
                payer_name="Payer",
                amount_cents=amount_cents,
                cashapp_handle=handle,
                paid_at=kwargs.get("paid_at"),
                created_at=kwargs.get("created_at", NOW),
                is_test=kwargs.get("is_test", False),
            )
        )

    def _send(self, method: str, tag: str, amount_cents: int, **kwargs) -> None:
        self.session.add(
            OutboundSend(
                method=method,
                tag=tag,
                method_owner=kwargs.get("method_owner", "round-table"),
                recipient=kwargs.get("recipient", "Player"),
                amount_cents=amount_cents,
                source_external_id=kwargs["source_external_id"],
                paid_at=kwargs.get("paid_at"),
                created_at=kwargs.get("created_at", NOW),
            )
        )

    def test_one_card_per_tag_and_exclusions(self):
        cards = list_account_balances(self.session, now=NOW)
        venmo_tags = [card.tag for card in cards["venmo"]]
        cashapp_tags = [card.tag for card in cards["cashapp"]]
        self.assertEqual(venmo_tags, ["@godfather4444"])
        self.assertEqual(cashapp_tags, ["$clubround"])
        self.assertEqual(cards["venmo"][0].offset_cents, 0)
        self.assertEqual(cards["venmo"][0].sending_ceiling_cents, 1_000_000)
        self.assertEqual(cards["venmo"][0].inflow_max_cents, 500_000)
        self.assertEqual(cards["cashapp"][0].sending_ceiling_cents, 500_000)

    def test_round_table_rows_week_window_and_paid_at_fallback(self):
        self._venmo(
            "@godfather4444",
            300_000,
            paid_at="2026-10-05T04:30:00Z",
        )
        self._venmo(
            "godfather4444",
            100_000,
            paid_at="yesterday",
            created_at=datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
        )
        self._venmo("@godfather4444", 50_000, method_owner="vaughn")
        self._venmo(
            "@godfather4444", 25_000, is_test=True, paid_at="2026-10-06T12:00:00Z"
        )
        self._send(
            "venmo",
            "@godfather4444",
            40_000,
            source_external_id="old",
            paid_at="2026-09-01T12:00:00Z",
        )
        self.session.commit()

        card = list_account_balances(self.session, now=NOW)["venmo"][0]
        self.assertEqual(card.received_week_cents, 300_000)
        self.assertEqual(card.balance_cents, 300_000 + 100_000 - 40_000)
        self.assertFalse(card.inflow_maxed)

    def test_rolling_window_boundary_and_negative_remainder(self):
        expired_at = NOW - WINDOW
        blocking_at = expired_at + timedelta(seconds=1)
        self._send(
            "venmo",
            "@godfather4444",
            200_000,
            source_external_id="expired",
            paid_at=expired_at.isoformat(),
            recipient="Old",
        )
        self._send(
            "venmo",
            "@godfather4444",
            1_200_000,
            source_external_id="blocking",
            paid_at=blocking_at.isoformat(),
            recipient="New",
        )
        self._send(
            "venmo",
            "@godfather4444",
            10_000,
            source_external_id="vaughn",
            method_owner="vaughn",
            paid_at=blocking_at.isoformat(),
        )
        self.session.commit()

        card = list_account_balances(self.session, now=NOW)["venmo"][0]
        self.assertEqual(card.sending_remaining_cents, 1_000_000 - 1_200_000)
        self.assertTrue(card.send_power_maxed)
        self.assertEqual(card.balance_cents, -(200_000 + 1_200_000))

        sends = list_blocking_sends(self.session, "venmo", "Godfather4444", now=NOW)
        self.assertEqual([row.recipient for row in sends], ["New"])
        self.assertEqual(sends[0].expires_at, blocking_at + WINDOW)

    def test_settings_update_changes_only_that_card(self):
        card = update_account_balance_settings(
            self.session,
            "cashapp",
            "ClubRound",
            offset_cents=120_000,
            sending_ceiling_cents=800_000,
            inflow_max_cents=200_000,
            now=NOW,
        )
        self.session.commit()
        self.assertEqual(card.offset_cents, 120_000)
        self.assertEqual(card.balance_cents, 120_000)
        self.assertEqual(card.sending_ceiling_cents, 800_000)
        self.assertEqual(card.inflow_max_cents, 200_000)

        venmo = list_account_balances(self.session, now=NOW)["venmo"][0]
        self.assertEqual(venmo.sending_ceiling_cents, 1_000_000)
        self.assertEqual(venmo.offset_cents, 0)

    def test_inflow_maxed_uses_card_threshold(self):
        self._cashapp("$clubround", 200_000, paid_at="2026-10-06T15:00:00Z")
        self.session.commit()
        update_account_balance_settings(
            self.session,
            "cashapp",
            "$clubround",
            offset_cents=0,
            sending_ceiling_cents=500_000,
            inflow_max_cents=150_000,
            now=NOW,
        )
        self.session.commit()
        card = list_account_balances(self.session, now=NOW)["cashapp"][0]
        self.assertEqual(card.received_week_cents, 200_000)
        self.assertTrue(card.inflow_maxed)

    def test_cards_sort_by_lower_of_balance_and_remaining(self):
        self._add_variant(self.rt, "venmo", "deposit", "@rich")
        self._add_variant(self.rt, "venmo", "deposit", "@spender")
        self._venmo("@rich", 5_000_000)
        self._send("venmo", "@rich", 1_200_000, source_external_id="rich-send")
        self._venmo("@spender", 900_000)
        self._send("venmo", "@spender", 200_000, source_external_id="spender-send")
        self.session.commit()

        tags = [
            card.tag for card in list_account_balances(self.session, now=NOW)["venmo"]
        ]
        self.assertEqual(tags, ["@spender", "@godfather4444", "@rich"])

    def test_reported_balance_sets_offset_from_ledger(self):
        self._venmo("@godfather4444", 300_000)
        self._send("venmo", "@godfather4444", 40_000, source_external_id="out")
        self.session.commit()
        offset = offset_cents_for_balance(
            self.session, "venmo", "@godfather4444", 100_000
        )
        self.assertEqual(offset, 100_000 - (300_000 - 40_000))
        card = update_account_balance_settings(
            self.session,
            "venmo",
            "@godfather4444",
            offset_cents=offset,
            sending_ceiling_cents=1_000_000,
            inflow_max_cents=500_000,
            now=NOW,
        )
        self.assertEqual(card.balance_cents, 100_000)
        self.assertEqual(card.offset_cents, -160_000)

    def _paypal(self, email: str, amount_cents: int, **kwargs) -> None:
        self.session.add(
            PayPalPayment(
                method_owner=kwargs.get("method_owner", "round-table"),
                payer_name="Payer",
                amount_cents=amount_cents,
                paypal_email=email,
                paid_at=kwargs.get("paid_at"),
                created_at=kwargs.get("created_at", NOW),
                is_test=kwargs.get("is_test", False),
            )
        )

    def _crypto(
        self,
        address: str,
        chain: str,
        amount_cents: int,
        *,
        created_at: datetime = NOW,
        **kwargs,
    ) -> None:
        self.session.add(
            CryptoPayment(
                method_owner=kwargs.get("method_owner", "round-table"),
                amount_cents=amount_cents,
                token_symbol="USDC",
                chain=chain,
                from_address="0xsender",
                to_address=address,
                transaction_hash=kwargs["transaction_hash"],
                alert_scope="rt",
                paid_at=kwargs.get("paid_at"),
                created_at=created_at,
                is_test=kwargs.get("is_test", False),
            )
        )

    def test_paypal_card_is_only_the_aiden_email(self):
        self._paypal("Aiden@ConcordConsultingInc.com", 200_000)
        self._paypal("aiden@concordconsulting.com", 40_000)
        self._paypal("payments@clubgto.com", 900_000)
        self._paypal("aiden@concordconsultinginc.com", 50_000, is_test=True)
        self._paypal("aiden@concordconsultinginc.com", 10_000, method_owner="vaughn")
        self.session.commit()

        cards = list_account_balances(self.session, now=NOW)
        self.assertEqual(len(cards["paypal"]), 1)
        card = cards["paypal"][0]
        self.assertEqual(card.tag, "aiden@concordconsultinginc.com")
        self.assertEqual(card.label, "aiden@concordconsultinginc.com")
        self.assertIsNone(card.detail)
        self.assertIsNone(card.custom_label)
        self.assertEqual(card.balance_cents, 240_000)
        self.assertEqual(card.sending_ceiling_cents, 500_000)
        self.assertEqual(card.inflow_max_cents, 500_000)
        self.assertEqual(card.sending_remaining_cents, 500_000)

    def test_paypal_card_keeps_settings_stored_under_the_old_email(self):
        self._paypal("aiden@concordconsultinginc.com", 200_000)
        self.session.add(
            AccountBalanceSettings(
                method="paypal",
                tag="aiden@concordconsulting.com",
                offset_cents=-50_000,
                sending_ceiling_cents=500_000,
                inflow_max_cents=500_000,
            )
        )
        self.session.commit()

        card = list_account_balances(self.session, now=NOW)["paypal"][0]
        self.assertEqual(card.tag, "aiden@concordconsultinginc.com")
        self.assertEqual(card.offset_cents, -50_000)
        self.assertEqual(card.balance_cents, 150_000)

    def test_crypto_collapses_chains_and_numbers_solana_by_first_deposit(self):
        evm = "0xc50b01cAa68EcB657807b198E1C3360fAC3CBA96"
        first = "EHYyR77wLRdTqP4vSdjbFAaeVLA9udjBtTBVDNx94bPe"
        second = "BZYb4e4x2pgmz2U9YoyCzWXzNgqoGZ4E9PZksHEMktqH"
        self._crypto(
            evm,
            "base",
            100,
            transaction_hash="base",
            created_at=NOW - timedelta(days=3),
        )
        self._crypto(
            evm.lower(),
            "ethereum",
            200,
            transaction_hash="eth",
            created_at=NOW - timedelta(days=2),
        )
        self._crypto(
            evm,
            "bsc",
            300,
            transaction_hash="bsc",
            created_at=NOW - timedelta(days=1),
        )
        self._crypto(
            second,
            "solana",
            400,
            transaction_hash="sol-late",
            created_at=NOW - timedelta(days=1),
        )
        self._crypto(
            first,
            "solana",
            500,
            transaction_hash="sol-early",
            created_at=NOW - timedelta(days=4),
        )
        self._crypto(first, "solana", 1, transaction_hash="test", is_test=True)
        self._send(
            "crypto",
            evm,
            50,
            source_external_id="evm-send",
        )
        self.session.commit()

        cards = {
            card.tag: card
            for card in list_account_balances(self.session, now=NOW)["crypto"]
        }
        self.assertEqual(set(cards), {evm.lower(), first, second})
        shared = cards[evm.lower()]
        self.assertEqual(shared.label, "Ethereum, BSC, Base")
        self.assertEqual(shared.detail, evm)
        self.assertEqual(shared.balance_cents, 100 + 200 + 300 - 50)
        self.assertEqual(shared.sending_ceiling_cents, 500_000)
        self.assertEqual(cards[first].label, "Solana #1")
        self.assertEqual(cards[second].label, "Solana #2")
        self.assertEqual(cards[first].detail, first)

    def test_variant_name_and_custom_label(self):
        address = "TTswLWyB8WRb9ZPHiZ1zXsmAjeh3ux4Som"
        self._add_variant(self.rt, "crypto", "deposit", address)
        variant = (
            self.session.query(ClubPaymentTierVariant).filter_by(label=address).one()
        )
        variant.tag = address
        variant.label = "Tron treasury"
        self._crypto(address, "tron", 80_000, transaction_hash="tron")
        self.session.commit()

        card = list_account_balances(self.session, now=NOW)["crypto"][0]
        self.assertEqual(card.label, "Tron treasury")
        self.assertIsNone(card.custom_label)

        renamed = update_account_balance_settings(
            self.session,
            "crypto",
            address,
            offset_cents=0,
            sending_ceiling_cents=500_000,
            inflow_max_cents=500_000,
            label="Hot wallet",
            set_label=True,
            now=NOW,
        )
        self.assertEqual(renamed.label, "Hot wallet")
        self.assertEqual(renamed.custom_label, "Hot wallet")
        self.assertEqual(renamed.detail, address)

        cleared = update_account_balance_settings(
            self.session,
            "crypto",
            address,
            offset_cents=0,
            sending_ceiling_cents=500_000,
            inflow_max_cents=500_000,
            label="  ",
            set_label=True,
            now=NOW,
        )
        self.assertEqual(cleared.label, "Tron treasury")
        self.assertIsNone(cleared.custom_label)


class AccountBalanceApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False
        )
        self.env_patch.start()
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            engine,
            tables=[
                Club.__table__,
                ClubPaymentMethod.__table__,
                ClubPaymentTier.__table__,
                ClubPaymentTierVariant.__table__,
                VenmoPayment.__table__,
                CashAppPayment.__table__,
                OutboundSend.__table__,
                PayPalPayment.__table__,
                CryptoPayment.__table__,
                Base.metadata.tables["account_balance_settings"],
            ],
        )
        self.session = sessionmaker(bind=engine)()
        club = Club(name="Round Table", telegram_user_id=1)
        self.session.add(club)
        self.session.flush()
        method = ClubPaymentMethod(
            club_id=club.id, direction="deposit", name="Venmo", slug="venmo"
        )
        self.session.add(method)
        self.session.flush()
        tier = ClubPaymentTier(method_id=method.id, label="Default")
        self.session.add(tier)
        self.session.flush()
        self.session.add(
            ClubPaymentTierVariant(
                method_id=method.id,
                tier_id=tier.id,
                label="Main",
                tag="@godfather4444",
            )
        )
        self.session.commit()

        @contextmanager
        def fake_db():
            try:
                yield self.session
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise

        self.db_patch = patch("api.routes.account_balances.get_db", fake_db)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.env_patch.stop()
        self.session.close()

    def test_patch_requires_admin_and_saves(self):
        self.session.add(
            VenmoPayment(
                method_owner="round-table",
                payer_name="Payer",
                amount_cents=10_000,
                venmo_handle="@godfather4444",
                created_at=datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc),
            )
        )
        self.session.commit()

        denied = self.client.patch(
            "/api/account-balances/venmo/@godfather4444",
            json={
                "balance_cents": 100,
                "sending_ceiling_cents": 50_000,
                "inflow_max_cents": 50_000,
            },
            headers={"Authorization": f"Bearer {create_token('account_manager')}"},
        )
        self.assertEqual(denied.status_code, 403)

        saved = self.client.patch(
            "/api/account-balances/venmo/@godfather4444",
            json={
                "balance_cents": 2500,
                "sending_ceiling_cents": 50_000,
                "inflow_max_cents": 40_000,
            },
            headers={"Authorization": f"Bearer {create_token()}"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        body = saved.json()
        self.assertEqual(body["balance_cents"], 2500)
        self.assertEqual(body["offset_cents"], 2500 - 10_000)
        self.assertEqual(body["sending_ceiling_cents"], 50_000)

        listed = self.client.get(
            "/api/account-balances",
            headers={"Authorization": f"Bearer {create_token()}"},
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["venmo"][0]["balance_cents"], 2500)
        self.assertEqual(listed.json()["venmo"][0]["offset_cents"], 2500 - 10_000)
