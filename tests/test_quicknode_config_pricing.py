"""Address checksums, tracked-wallet loading, allowlist and pricing rules."""

from __future__ import annotations

import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from bot.services.quicknode import config as qc
from bot.services.quicknode.addresses import (
    eip55_checksum,
    is_valid_bitcoin,
    is_valid_eip55,
    keccak256,
    tron_base58_to_hex,
    tron_hex_to_base58,
)
from bot.services.quicknode.decode import TRANSFER_TOPIC
from bot.services.quicknode.pricing import decide_quote, usd_cents_string
from tests.support import quicknode as f


class AddressTestCase(unittest.TestCase):
    def test_keccak_vectors(self):
        self.assertEqual(
            keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )
        self.assertEqual(
            "0x" + keccak256(b"Transfer(address,address,uint256)").hex(),
            TRANSFER_TOPIC,
        )

    def test_allowlist_matches_issuer_checksums(self):
        for addr in (
            "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "0x55d398326f99059fF775485246999027B3197955",
            "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        ):
            self.assertTrue(is_valid_eip55(addr), addr)
            self.assertIsNotNone(
                next(t for t in qc.ALLOWLIST if t.contract == addr.lower())
            )
        self.assertEqual(
            tron_hex_to_base58(qc.TRON_USDT_HEX), "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        )
        self.assertEqual(
            tron_base58_to_hex("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"), qc.TRON_USDT_HEX
        )

    def test_token_matched_by_contract_only(self):
        self.assertEqual(
            qc.token_for("bsc", "0x55D398326F99059FF775485246999027B3197955").decimals,
            18,
        )
        self.assertIsNone(
            qc.token_for("base", "0xdac17f958d2ee523a2206206994597c13d831ec7")
        )
        self.assertIsNone(qc.token_for("tron", "USDT"))

    def test_bitcoin_checksums(self):
        self.assertTrue(is_valid_bitcoin(f.BTC_WALLET))
        self.assertFalse(is_valid_bitcoin(f.BTC_WALLET[:-1] + "d"))
        self.assertTrue(is_valid_bitcoin("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"))
        self.assertFalse(is_valid_bitcoin("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb"))


class TrackedWalletsTestCase(unittest.TestCase):
    def test_evm_entry_applies_to_three_chains(self):
        wallets = f.wallet_map()
        addr, owner = f.EVM_RECIPIENTS[("ethereum", "token")]
        for chain in qc.EVM_CHAINS:
            self.assertEqual(
                wallets.get(chain, addr.upper().replace("0X", "0x")).owner, owner
            )
        self.assertEqual(wallets.get("tron", f.TRON_WALLET).owner, "GTO")

    def test_bad_checksums_refused(self):
        lower = f.EVM_RECIPIENTS[("bsc", "token")][0]
        bad = [
            {"chain": "evm", "address": lower, "owner": "RT"},
            {"chain": "tron", "address": f.TRON_WALLET[:-1] + "E", "owner": "GTO"},
            {"chain": "bitcoin", "address": f.BTC_WALLET[:-1] + "d", "owner": "GTO"},
            {"chain": "solana", "address": "notbase58!", "owner": "RT"},
            {"chain": "evm", "address": eip55_checksum(lower), "owner": "mateos"},
            {"chain": "dogecoin", "address": "x", "owner": "RT"},
        ]
        for entry in bad:
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    qc.parse_tracked_wallets(json.dumps([entry]))

    def test_env_knobs(self):
        env = {
            "QUICKNODE_POST_CHAINS": " tron, Base ",
            "QUICKNODE_START_BLOCK_TRON": "88000000",
            "QUICKNODE_WEBHOOK_SECRET": "old, new",
            "QUICKNODE_PUBLIC_RPC_URL_BSC": "https://bsc.example",
            "QUICKNODE_RPC_URL_BASE": "https://qn.example/base",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(qc.post_chains(), {"tron", "base"})
            self.assertEqual(qc.start_block("tron"), 88000000)
            self.assertEqual(qc.start_block("solana"), 0)
            self.assertEqual(qc.webhook_secrets(), ["old", "new"])
            self.assertEqual(qc.source_b_url("bsc"), "https://bsc.example")
            self.assertEqual(
                qc.source_b_url("ethereum"), "https://ethereum-rpc.publicnode.com"
            )
            self.assertEqual(qc.source_a_url("base"), "https://qn.example/base")
            self.assertEqual(qc.source_a_url("bitcoin"), "https://mempool.space/api")
            self.assertEqual(qc.source_b_url("bitcoin"), "https://blockstream.info/api")

    def test_slack_channel_default_needs_bot_token(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(qc.slack_channel_id())
        with patch.dict(os.environ, {"SLACK_OPS_BOT_TOKEN": "x"}, clear=True):
            self.assertEqual(qc.slack_channel_id(), "C0BDML0LS9X")
        with patch.dict(
            os.environ,
            {"SLACK_OPS_BOT_TOKEN": "x", "QUICKNODE_SLACK_CHANNEL_ID": "C1"},
            clear=True,
        ):
            self.assertEqual(qc.slack_channel_id(), "C1")


class PricingTestCase(unittest.TestCase):
    def test_native_uses_lower_feed(self):
        q = decide_quote("ETH", Decimal("1685.95"), Decimal("1688.14"))
        self.assertTrue(q.ok)
        self.assertEqual(q.price, Decimal("1685.95"))

    def test_native_feeds_disagree(self):
        q = decide_quote("BTC", Decimal("60000"), Decimal("60700"))
        self.assertFalse(q.ok)
        self.assertEqual(q.reason, "price_feeds_disagree")

    def test_feed_down(self):
        q = decide_quote("SOL", Decimal("70"), None)
        self.assertEqual((q.ok, q.reason), (False, "price_feed_down"))

    def test_stable_one_to_one_and_depeg(self):
        q = decide_quote("USDT", Decimal("0.998805"), Decimal("0.999102"))
        self.assertEqual((q.ok, q.price), (True, Decimal("1")))
        q = decide_quote("USDC", Decimal("1"), Decimal("0.985"))
        self.assertEqual((q.ok, q.reason), (False, "depeg"))

    def test_round_down_to_cents(self):
        self.assertEqual(usd_cents_string(22_000_000, 6, Decimal("1")), "22.00")
        self.assertEqual(usd_cents_string(1_999_999, 6, Decimal("1")), "1.99")
        self.assertEqual(usd_cents_string(1, 6, Decimal("1")), "0.00")

    def test_eighteen_decimals_exact(self):
        self.assertEqual(
            usd_cents_string(1000000000000000001, 18, Decimal("1")), "1.00"
        )
        self.assertEqual(
            usd_cents_string(1000000000000000001, 18, Decimal("1685.95")), "1685.95"
        )
        # 2**256 - 1 wei must not lose precision before truncation.
        huge = usd_cents_string(2**256 - 1, 18, Decimal("1"))
        self.assertEqual(
            huge, f"{(2**256 - 1) // 10**16 // 100}.{(2**256 - 1) // 10**16 % 100:02d}"
        )


if __name__ == "__main__":
    unittest.main()
