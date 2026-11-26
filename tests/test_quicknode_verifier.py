"""QuickNode verifier job end to end: fixtures -> decode -> agree -> price -> post."""

from __future__ import annotations

import copy
import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from bot.services.quicknode import store, verifier
from bot.services.quicknode.hints import extract_hints
from bot.services.quicknode.sources import SourceError
from bot.services.venmo_payments import IngestResult
from db.models import CryptoPayment, QuicknodeTransfer, QuicknodeTxHint
from tests.support import quicknode as f
from tests.support.sqlite_db import get_db_for, make_sqlite_session_factory

ALL_CHAINS = "ethereum,bsc,base,tron,solana,bitcoin"
NOT_FOUND = {
    "ethereum": {"tx": None},
    "bsc": {"tx": None},
    "base": {"tx": None},
    "tron": {"tx": {}, "info": {}},
    "solana": {"result": None},
    "bitcoin": {"tx": None, "tip_height": None},
}
FEEDS = {
    "ETH": Decimal("1685.95"),
    "BNB": Decimal("553.31"),
    "TRX": Decimal("0.3036"),
    "SOL": Decimal("70.44"),
    "BTC": Decimal("60172.71"),
    "USDT": Decimal("0.9991"),
    "USDC": Decimal("1.0000"),
}


def _at(ts: int, seconds: int = 120) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc) + timedelta(seconds=seconds)


def _block_time(bundle: dict) -> int:
    return int(bundle["block"]["timestamp"], 16)


class VerifierTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.factory = make_sqlite_session_factory(
            ["clubs", "crypto_payments", "quicknode_tx_hints", "quicknode_transfers"]
        )
        self.bundles: dict[tuple[str, str, str], object] = {}
        self.cb = dict(FEEDS)
        self.cg = dict(FEEDS)
        self.ingest = AsyncMock(side_effect=self._fake_ingest)
        self.alert = AsyncMock()
        self.env = {
            "QUICKNODE_TRACKED_WALLETS": f.wallets_json(),
            "QUICKNODE_POST_CHAINS": ALL_CHAINS,
            "QUICKNODE_TEST_POSTS": "",
        }
        self.patches = [
            patch("bot.services.quicknode.store.get_db", get_db_for(self.factory)),
            patch.dict(os.environ, self.env),
            patch("bot.services.quicknode.sources.fetch_raw", new=self._fetch_raw),
            patch("bot.services.quicknode.pricing.coinbase_price", new=self._cb),
            patch("bot.services.quicknode.pricing.coingecko_price", new=self._cg),
            patch("bot.services.quicknode.verifier.ingest_crypto_payment", self.ingest),
            patch("bot.services.quicknode.verifier.alert", self.alert),
        ]
        for p in self.patches:
            p.start()
        verifier._OUTAGES.clear()
        verifier._CONFIG_ALERTED = False

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    # ---------------------------------------------------------------- fakes

    async def _fetch_raw(self, chain, which, tx_hash):
        value = self.bundles.get((chain, which, tx_hash))
        if value is None:
            value = self.bundles.get((chain, "*", tx_hash))
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value if value is not None else NOT_FOUND[chain])

    async def _fake_ingest(self, **kw):
        """Like ingest_crypto_payment: one crypto_payments row per source_external_id."""
        with self.factory() as s:
            existing = (
                s.query(CryptoPayment)
                .filter_by(source_external_id=kw["source_external_id"])
                .one_or_none()
            )
            if existing is not None:
                return IngestResult(
                    payment_id=existing.id,
                    status="unbound",
                    auto_bound=False,
                    created=False,
                )
            row = CryptoPayment(
                method_owner=kw["method_owner"],
                amount_cents=int(Decimal(kw["amount"]) * 100),
                token_symbol=kw["token_symbol"],
                chain=kw["chain"],
                from_address=kw["from_address"],
                to_address=kw["to_address"],
                transaction_hash=kw["transaction_hash"],
                source_external_id=kw["source_external_id"],
                alert_name=kw["alert_name"],
                alert_scope="clubgto" if " GTO " in kw["alert_name"] else "rt_at_cc",
                is_test=kw["test"],
            )
            s.add(row)
            s.commit()
            return IngestResult(
                payment_id=row.id, status="unbound", auto_bound=False, created=True
            )

    async def _cb(self, client, symbol):
        if self.cb.get(symbol) is None:
            raise RuntimeError("coinbase down")
        return self.cb[symbol]

    async def _cg(self, client, symbol):
        if self.cg.get(symbol) is None:
            raise RuntimeError("coingecko down")
        return self.cg[symbol]

    # -------------------------------------------------------------- helpers

    def serve(self, chain, tx_hash, bundle, which="*"):
        self.bundles[(chain, which, tx_hash)] = bundle

    def hint(self, chain, tx_hash, now):
        store.record_hints(chain, [tx_hash], now=now)

    async def run_once(self, now):
        return await verifier.run_verifier_once(now)

    def hint_row(self, tx_hash):
        with self.factory() as s:
            return s.query(QuicknodeTxHint).filter_by(tx_hash=tx_hash).one()

    def transfers(self):
        with self.factory() as s:
            return s.query(QuicknodeTransfer).order_by(QuicknodeTransfer.id).all()

    def alerts(self):
        return [c.args[0] for c in self.alert.await_args_list]

    def tron_setup(self):
        tx = f.TRON_USDT_TX
        self.serve("tron", tx, f.tron_bundle(tx))
        now = _at(f.TRON_USDT_BLOCK_TIME)
        self.hint("tron", tx, now)
        return tx, now

    # ---------------------------------------------------------------- tests

    async def test_genuine_tron_usdt_posts(self):
        tx, now = self.tron_setup()
        self.assertEqual(await self.run_once(now), {"done": 1})
        self.ingest.assert_awaited_once()
        kwargs = self.ingest.await_args.kwargs
        self.assertEqual(kwargs["amount"], "22.00")
        self.assertEqual(kwargs["chain"], "tron")
        self.assertEqual(kwargs["transaction_hash"], tx)
        self.assertFalse(kwargs["transaction_hash"].startswith("0x"))
        self.assertEqual(kwargs["method_owner"], "vaughn")
        self.assertEqual(kwargs["alert_name"], "QuickNode GTO tron")
        self.assertEqual(kwargs["token_symbol"], "USDT")
        self.assertEqual(kwargs["to_address"], f.TRON_WALLET)
        self.assertTrue(kwargs["from_address"].startswith("T"))
        self.assertEqual(kwargs["source_external_id"], f"{tx}_0")
        self.assertEqual(kwargs["paid_at"], "2026-11-20T03:48:21Z")
        self.assertIs(kwargs["allow_auto_bind"], False)
        self.assertIs(kwargs["test"], False)
        (row,) = self.transfers()
        self.assertEqual((row.state, row.amount_usd), ("posted", "22.00"))
        with self.factory() as s:
            payment = s.query(CryptoPayment).one()
        self.assertEqual(row.crypto_payment_id, payment.id)
        self.assertEqual(payment.amount_cents, 2200)
        self.assertEqual(row.raw_amount, "22000000")
        self.assertEqual(self.hint_row(tx).state, "done")
        self.assertEqual(self.alerts(), [])

    async def test_alert_scope_resolves_from_owner_token(self):
        from bot.services.crypto_payments import (
            ALERT_SCOPE_CLUBGTO,
            ALERT_SCOPE_RT_AT_CC,
            resolve_alert_scope,
        )

        self.assertEqual(resolve_alert_scope("QuickNode GTO tron"), ALERT_SCOPE_CLUBGTO)
        self.assertEqual(
            resolve_alert_scope("QuickNode RT solana"), ALERT_SCOPE_RT_AT_CC
        )

    async def test_tampered_payload_amount_comes_from_chain(self):
        payload = {
            "matchingTransactions": [
                {
                    "hash": "0x" + f.TRON_USDT_TX,
                    "value": "0xffffffffff",
                    "to": "0x" + "00" * 20,
                    "input": "0xa9059cbb" + "f" * 128,
                }
            ],
            "matchingReceipts": [
                {"transactionHash": "0x" + f.TRON_USDT_TX, "amount": "99999"}
            ],
        }
        now = _at(f.TRON_USDT_BLOCK_TIME)
        store.record_hints("tron", extract_hints("tron", payload), now=now)
        self.serve("tron", f.TRON_USDT_TX, f.tron_bundle(f.TRON_USDT_TX))
        await self.run_once(now)
        self.assertEqual(self.ingest.await_args.kwargs["amount"], "22.00")

    async def test_duplicate_hint_and_duplicate_transfer(self):
        tx, now = self.tron_setup()
        self.hint("tron", tx, now)  # second delivery of the same hint
        await self.run_once(now)
        # Force the hint back to pending, as a lease expiry after a crash would.
        with self.factory() as s:
            row = s.query(QuicknodeTxHint).one()
            row.state = "pending"
            s.commit()
        await self.run_once(now + timedelta(minutes=1))
        self.ingest.assert_awaited_once()
        self.assertEqual(len(self.transfers()), 1)
        self.assertEqual(self.hint_row(tx).reason, "duplicate")

    async def test_tron_dust_rejected_without_alert(self):
        tx = f.TRON_DUST_TX
        self.serve("tron", tx, f.tron_bundle(tx))
        await self.run_hint("tron", tx, _at(1795146513))
        (row,) = self.transfers()
        self.assertEqual(
            (row.state, row.reason, row.amount_usd), ("rejected", "dust", "0.00")
        )
        self.ingest.assert_not_awaited()
        self.assertEqual(self.alerts(), [])

    async def run_hint(self, chain, tx, now):
        self.hint(chain, tx, now)
        return await self.run_once(now)

    async def test_native_dust_on_ethereum(self):
        tx = f.evm_hash("ethereum", "native")
        bundle = f.evm_bundle("ethereum", "native")
        self.serve("ethereum", tx, bundle)
        await self.run_hint("ethereum", tx, _at(_block_time(bundle)))
        self.assertEqual(self.transfers()[0].reason, "dust")
        self.ingest.assert_not_awaited()

    async def test_bsc_native_and_eighteen_decimal_token(self):
        native = f.evm_hash("bsc", "native")
        nb = f.evm_bundle("bsc", "native")
        self.serve("bsc", native, nb)
        token = f.evm_hash("bsc", "token")
        tb = f.evm_bundle("bsc", "token")
        tb["receipt"]["logs"][0]["data"] = "0x" + format(1000000000000000001, "064x")
        self.serve("bsc", token, tb)
        now = _at(_block_time(tb))
        self.hint("bsc", native, now)
        self.hint("bsc", token, now)
        await self.run_once(now)
        amounts = {
            c.kwargs["token_symbol"]: c.kwargs["amount"]
            for c in self.ingest.await_args_list
        }
        # 0.2941515659445437 BNB * 553.31 (lower feed) = 162.75..., rounded down.
        self.assertEqual(amounts, {"BNB": "162.75", "USDT": "1.00"})

    async def test_sources_disagree_held_once(self):
        tx, now = self.tron_setup()
        other = f.tron_bundle(tx)
        other["info"]["log"][0]["data"] = "0" * 56 + "05f5e100"
        self.serve("tron", tx, other, which="b")
        await self.run_once(now)
        self.ingest.assert_not_awaited()
        row = self.hint_row(tx)
        self.assertEqual((row.state, row.reason), ("held", "sources_disagree"))
        self.assertEqual(len(self.alerts()), 1)
        await self.run_once(now + timedelta(minutes=5))
        self.assertEqual(len(self.alerts()), 1)

    async def test_block_hash_disagreement_held(self):
        tx = f.evm_hash("base", "token")
        a = f.evm_bundle("base", "token")
        b = copy.deepcopy(a)
        fake = "0x" + "cd" * 32
        b["tx"]["blockHash"] = b["receipt"]["blockHash"] = b["block"]["hash"] = fake
        self.serve("base", tx, a, which="a")
        self.serve("base", tx, b, which="b")
        await self.run_hint("base", tx, _at(_block_time(a)))
        self.assertEqual(self.hint_row(tx).reason, "sources_disagree")

    async def test_source_down_alerts_once_then_recovers(self):
        tx, now = self.tron_setup()
        second = f.TRON_DUST_TX
        self.serve("tron", second, f.tron_bundle(second))
        self.hint("tron", second, now)
        self.serve("tron", tx, SourceError("boom"), which="b")
        self.serve("tron", second, SourceError("boom"), which="b")
        self.assertEqual(await self.run_once(now), {"retry": 2})
        down = [a for a in self.alerts() if "source down" in a]
        self.assertEqual(len(down), 1)
        self.assertIn("tron:public", down[0])
        self.ingest.assert_not_awaited()
        self.assertGreater(
            self.hint_row(tx).next_check_at.replace(tzinfo=timezone.utc), now
        )

        del self.bundles[("tron", "b", tx)]
        del self.bundles[("tron", "b", second)]
        await self.run_once(now + timedelta(minutes=5))
        self.assertTrue(any("recovered" in a for a in self.alerts()))
        self.ingest.assert_awaited_once()

    async def test_not_found_on_both_rejected_quietly_after_two_hours(self):
        tx = "0x" + "ef" * 32
        now = datetime(2026, 11, 25, tzinfo=timezone.utc)
        self.hint("ethereum", tx, now)
        await self.run_once(now)
        self.assertEqual(self.hint_row(tx).state, "pending")
        await self.run_once(now + timedelta(hours=2, minutes=1))
        row = self.hint_row(tx)
        self.assertEqual((row.state, row.reason), ("rejected", "not_found"))
        self.assertEqual(self.alerts(), [])

    async def test_found_on_one_source_only_held_after_two_hours(self):
        tx, now = self.tron_setup()
        self.serve("tron", tx, NOT_FOUND["tron"], which="b")
        await self.run_once(now)
        self.assertEqual(self.hint_row(tx).state, "pending")
        later = now + timedelta(hours=2, minutes=1)
        await self.run_once(later)
        row = self.hint_row(tx)
        self.assertEqual((row.state, row.reason), ("held", "single_source"))
        self.assertIsNotNone(row.alerted_at)
        self.assertEqual(len(self.alerts()), 1)
        self.ingest.assert_not_awaited()

    async def test_not_final_retries(self):
        tx = f.evm_hash("ethereum", "token")
        bundle = f.evm_bundle("ethereum", "token", depth=-5)
        self.serve("ethereum", tx, bundle)
        await self.run_hint("ethereum", tx, _at(_block_time(bundle)))
        row = self.hint_row(tx)
        self.assertEqual(
            (row.state, row.reason, row.attempts), ("pending", "not_final", 1)
        )
        self.ingest.assert_not_awaited()

    async def test_one_source_below_wait_depth_retries(self):
        tx = f.evm_hash("base", "token")
        self.serve("base", tx, f.evm_bundle("base", "token"), which="a")
        self.serve("base", tx, f.evm_bundle("base", "token", depth=-1), which="b")
        await self.run_hint("base", tx, _at(_block_time(f.evm_bundle("base", "token"))))
        self.assertEqual(self.hint_row(tx).reason, "not_final")
        self.ingest.assert_not_awaited()

    async def test_below_cutover_floor_rejected(self):
        tx, now = self.tron_setup()
        with patch.dict(os.environ, {"QUICKNODE_START_BLOCK_TRON": "88157473"}):
            await self.run_once(now)
        row = self.hint_row(tx)
        self.assertEqual((row.state, row.reason), ("rejected", "below_start_block"))
        self.ingest.assert_not_awaited()

    async def test_reverted_rejected(self):
        tx = f.evm_hash("ethereum", "token")
        bundle = f.evm_bundle("ethereum", "token")
        bundle["receipt"]["status"] = "0x0"
        self.serve("ethereum", tx, bundle)
        await self.run_hint("ethereum", tx, _at(_block_time(bundle)))
        self.assertEqual(self.hint_row(tx).reason, "reverted")
        self.ingest.assert_not_awaited()

    async def test_wrong_recipient_and_fake_token_rejected(self):
        tx = f.evm_hash("ethereum", "token")
        bundle = f.evm_bundle("ethereum", "token")
        bundle["receipt"]["logs"][0]["address"] = "0x" + "11" * 20
        self.serve("ethereum", tx, bundle)
        await self.run_hint("ethereum", tx, _at(_block_time(bundle)))
        self.assertEqual(self.hint_row(tx).reason, "not_allowlisted")
        tx2 = f.evm_hash("base", "token")
        b2 = f.evm_bundle("base", "token")
        b2["receipt"]["logs"][0]["topics"][2] = "0x" + "0" * 24 + "22" * 20
        self.serve("base", tx2, b2)
        await self.run_hint("base", tx2, _at(_block_time(b2)))
        self.assertEqual(self.hint_row(tx2).reason, "no_tracked_transfer")
        self.ingest.assert_not_awaited()

    async def test_btc_change_rejected_as_self_transfer(self):
        self.env_wallets(
            [{"chain": "bitcoin", "address": f.BTC_SENDER, "owner": "GTO"}]
        )
        txid = f.btc_txid()
        bundle = f.btc_bundle()
        self.serve("bitcoin", txid, bundle)
        await self.run_hint("bitcoin", txid, _at(bundle["tx"]["status"]["block_time"]))
        self.assertEqual(self.hint_row(txid).reason, "self_transfer")
        self.ingest.assert_not_awaited()

    def env_wallets(self, extra):
        os.environ["QUICKNODE_TRACKED_WALLETS"] = f.wallets_json(extra)

    async def test_btc_posts_per_vout(self):
        txid = f.btc_txid()
        self.serve("bitcoin", txid, f.btc_bundle("mempool"), which="a")
        self.serve("bitcoin", txid, f.btc_bundle("blockstream"), which="b")
        bt = f.btc_bundle()["tx"]["status"]["block_time"]
        await self.run_hint("bitcoin", txid, _at(bt))
        kwargs = self.ingest.await_args.kwargs
        # 0.00300136 BTC * 60172.71 = 180.597..., rounded down.
        self.assertEqual(kwargs["amount"], "180.59")
        self.assertEqual(kwargs["source_external_id"], f"{txid}_0")
        self.assertEqual(kwargs["from_address"], f.BTC_SENDER)
        self.assertEqual(kwargs["method_owner"], "vaughn")

    async def test_solana_usdc_and_sol_post(self):
        for kind in ("usdc", "sol"):
            self.serve("solana", f.solana_sig(kind), f.solana_bundle(kind))
        now = _at(f.solana_bundle("usdc")["result"]["blockTime"])
        self.hint("solana", f.solana_sig("usdc"), now)
        self.hint("solana", f.solana_sig("sol"), now)
        await self.run_once(now)
        posted = {
            c.kwargs["token_symbol"]: c.kwargs for c in self.ingest.await_args_list
        }
        self.assertEqual(posted["USDC"]["amount"], "50.00")
        self.assertEqual(posted["USDC"]["alert_name"], "QuickNode RT solana")
        self.assertEqual(posted["USDC"]["method_owner"], "round-table")
        self.assertEqual(
            posted["USDC"]["from_address"],
            "5QtyKPHtWUf45bNb5buQ6UxpL2ekSJxBCK9g8xMCsc9U",
        )
        # 0.4091 SOL * 70.44 = 28.816...
        self.assertEqual(posted["SOL"]["amount"], "28.81")

    async def test_two_transfers_in_one_tx_post_separately(self):
        tx = f.evm_hash("ethereum", "token")
        bundle = f.evm_bundle("ethereum", "token")
        log = dict(bundle["receipt"]["logs"][0], data="0x" + format(3_000_000, "064x"))
        bundle["receipt"]["logs"].append(log)
        self.serve("ethereum", tx, bundle)
        await self.run_hint("ethereum", tx, _at(_block_time(bundle)))
        keys = [c.kwargs["source_external_id"] for c in self.ingest.await_args_list]
        self.assertEqual(keys, [f"{tx}_0", f"{tx}_1"])
        self.assertEqual(
            [c.kwargs["amount"] for c in self.ingest.await_args_list], ["1.12", "3.00"]
        )

    async def test_price_feeds_disagree_held(self):
        self.cg["BNB"] = Decimal("600")
        tx = f.evm_hash("bsc", "native")
        bundle = f.evm_bundle("bsc", "native")
        self.serve("bsc", tx, bundle)
        await self.run_hint("bsc", tx, _at(_block_time(bundle)))
        (row,) = self.transfers()
        self.assertEqual((row.state, row.reason), ("held", "price_feeds_disagree"))
        self.assertEqual(self.hint_row(tx).state, "done")
        self.assertEqual(len(self.alerts()), 1)
        self.ingest.assert_not_awaited()

    async def test_depeg_and_feed_down_held(self):
        self.cb["USDT"] = Decimal("0.97")
        tx, now = self.tron_setup()
        await self.run_once(now)
        self.assertEqual(self.transfers()[0].reason, "depeg")
        self.cb["USDC"] = None
        tx2 = f.evm_hash("base", "token")
        b2 = f.evm_bundle("base", "token")
        self.serve("base", tx2, b2)
        await self.run_hint("base", tx2, _at(_block_time(b2)))
        self.assertEqual(self.transfers()[1].reason, "price_feed_down")
        self.ingest.assert_not_awaited()

    async def test_stale_verification_held(self):
        tx = f.TRON_USDT_TX
        self.serve("tron", tx, f.tron_bundle(tx))
        await self.run_hint("tron", tx, _at(f.TRON_USDT_BLOCK_TIME, seconds=31 * 60))
        self.assertEqual(self.transfers()[0].reason, "stale_price")
        self.ingest.assert_not_awaited()

    async def test_ingest_raises_after_commit_marks_notify_failed(self):
        tx, now = self.tron_setup()

        async def boom(**kwargs):
            with self.factory() as s:
                s.add(
                    CryptoPayment(
                        id=41,
                        method_owner="vaughn",
                        amount_cents=2200,
                        token_symbol="USDT",
                        chain="tron",
                        from_address=kwargs["from_address"],
                        to_address=kwargs["to_address"],
                        transaction_hash=kwargs["transaction_hash"],
                        source_external_id=kwargs["source_external_id"],
                        alert_name=kwargs["alert_name"],
                        alert_scope="clubgto",
                    )
                )
                s.commit()
            raise RuntimeError("telegram down")

        self.ingest.side_effect = boom
        await self.run_once(now)
        (row,) = self.transfers()
        self.assertEqual((row.state, row.crypto_payment_id), ("notify_failed", 41))
        self.assertEqual(self.hint_row(tx).state, "done")
        self.assertTrue(any("NOTIFY FAILED" in a for a in self.alerts()))

    async def test_ingest_raises_before_commit_retries(self):
        tx, now = self.tron_setup()
        self.ingest.side_effect = RuntimeError("db down")
        await self.run_once(now)
        self.assertEqual(self.transfers(), [])
        row = self.hint_row(tx)
        self.assertEqual(row.state, "pending")
        self.assertTrue(row.reason.startswith("post_failed"))

    async def test_shadow_mode_does_not_ingest(self):
        os.environ["QUICKNODE_POST_CHAINS"] = "ethereum"
        tx, now = self.tron_setup()
        await self.run_once(now)
        self.ingest.assert_not_awaited()
        (row,) = self.transfers()
        self.assertEqual((row.state, row.amount_usd), ("shadow", "22.00"))
        (msg,) = self.alerts()
        self.assertTrue(msg.startswith("[shadow] would post"))
        self.assertIn("tronscan.org/#/transaction/" + tx, msg)

    async def test_test_posts_flag(self):
        os.environ["QUICKNODE_TEST_POSTS"] = "1"
        _, now = self.tron_setup()
        await self.run_once(now)
        self.assertIs(self.ingest.await_args.kwargs["test"], True)

    async def test_lease_blocks_second_claim(self):
        tx, now = self.tron_setup()
        claimed = store.claim_due_hints(now, limit=25, lease=timedelta(minutes=5))
        self.assertEqual(len(claimed), 1)
        self.assertEqual(
            store.claim_due_hints(
                now + timedelta(minutes=4), limit=25, lease=timedelta(minutes=5)
            ),
            [],
        )
        self.assertEqual(
            len(
                store.claim_due_hints(
                    now + timedelta(minutes=6), limit=25, lease=timedelta(minutes=5)
                )
            ),
            1,
        )

    async def test_bad_wallet_config_stops_with_one_alert(self):
        os.environ["QUICKNODE_TRACKED_WALLETS"] = (
            '[{"chain":"evm","address":"0xabc","owner":"RT"}]'
        )
        _, now = self.tron_setup()
        self.assertEqual(await self.run_once(now), {})
        await self.run_once(now)
        self.assertEqual(len(self.alerts()), 1)
        self.assertEqual(self.hint_row(f.TRON_USDT_TX).state, "pending")


class DecideTestCase(unittest.TestCase):
    def test_error_after_two_hours_keeps_retrying_until_give_up(self):
        from bot.services.quicknode.decode import TxView

        err = TxView(status="error")
        nf = TxView(status="not_found")
        self.assertEqual(
            verifier.decide(err, nf, age=timedelta(hours=3), floor=0).action, "retry"
        )
        self.assertEqual(
            verifier.decide(err, nf, age=timedelta(hours=25), floor=0).reason, "timeout"
        )


if __name__ == "__main__":
    unittest.main()
