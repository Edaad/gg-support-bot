"""Pure decoder tests on real chain responses (tests/fixtures/quicknode)."""

from __future__ import annotations

import unittest

from bot.services.quicknode.addresses import tron_hex_to_base58
from bot.services.quicknode.decode import (
    FOUND,
    NOT_FOUND,
    PENDING,
    SPL_TOKEN_PROGRAM,
    TRANSFER_TOPIC,
)
from bot.services.quicknode.sources import decode_bundle
from tests.support import quicknode as f

FAKE_TOKEN = "0x1111111111111111111111111111111111111111"


def _topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().removeprefix("0x")


def _word(value: int) -> str:
    return "0x" + format(value, "064x")


class EvmDecodeTestCase(unittest.TestCase):
    def setUp(self):
        self.wallets = f.wallet_map()

    def decode(self, chain, kind, bundle=None, tx_hash=None):
        return decode_bundle(
            chain,
            tx_hash or f.evm_hash(chain, kind),
            bundle or f.evm_bundle(chain, kind),
            self.wallets,
        )

    def test_genuine_tokens_and_natives(self):
        expected = {
            ("ethereum", "token"): ("USDT", 1120000, 6),
            ("ethereum", "native"): ("ETH", 18505900000000, 18),
            ("bsc", "token"): ("USDT", 500 * 10**18, 18),
            ("bsc", "native"): ("BNB", 294151565944543700, 18),
            ("base", "token"): ("USDC", 50830000, 6),
            ("base", "native"): ("ETH", 2365952847260, 18),
        }
        for (chain, kind), (asset, raw, decimals) in expected.items():
            with self.subTest(chain=chain, kind=kind):
                view = self.decode(chain, kind)
                self.assertEqual(view.status, FOUND)
                self.assertTrue(view.success)
                self.assertEqual(len(view.transfers), 1)
                t = view.transfers[0]
                self.assertEqual((t.asset, t.raw, t.decimals), (asset, raw, decimals))
                self.assertEqual(t.to, f.EVM_RECIPIENTS[(chain, kind)][0])
                suffix = "_native" if kind == "native" else "_0"
                self.assertEqual(t.key, f.evm_hash(chain, kind) + suffix)

    def test_key_uses_position_in_tx_not_block_log_index(self):
        bundle = f.evm_bundle("ethereum", "token")
        self.assertEqual(int(bundle["receipt"]["logs"][0]["logIndex"], 16), 5)
        view = self.decode("ethereum", "token", bundle)
        self.assertTrue(view.transfers[0].key.endswith("_0"))

    def test_fake_same_symbol_token_ignored(self):
        bundle = f.evm_bundle("ethereum", "token")
        bundle["receipt"]["logs"][0]["address"] = FAKE_TOKEN
        view = self.decode("ethereum", "token", bundle)
        self.assertEqual(view.transfers, ())
        self.assertIn("not_allowlisted", view.skipped)

    def test_fake_usdt_on_base(self):
        # The real Ethereum USDT contract address is not allowlisted on Base.
        bundle = f.evm_bundle("base", "token")
        bundle["receipt"]["logs"][0]["address"] = (
            "0xdac17f958d2ee523a2206206994597c13d831ec7"
        )
        self.assertEqual(self.decode("base", "token", bundle).transfers, ())

    def test_wrong_recipient(self):
        bundle = f.evm_bundle("bsc", "token")
        bundle["receipt"]["logs"][0]["topics"][2] = _topic(FAKE_TOKEN)
        self.assertEqual(self.decode("bsc", "token", bundle).transfers, ())
        native = f.evm_bundle("bsc", "native")
        native["tx"]["to"] = FAKE_TOKEN
        self.assertEqual(self.decode("bsc", "native", native).transfers, ())

    def test_reverted(self):
        bundle = f.evm_bundle("ethereum", "token")
        bundle["receipt"]["status"] = "0x0"
        view = self.decode("ethereum", "token", bundle)
        self.assertEqual(view.status, FOUND)
        self.assertFalse(view.success)
        self.assertEqual(view.transfers, ())

    def test_wait_depth(self):
        eth = f.evm_bundle("ethereum", "token", depth=-1)
        self.assertEqual(self.decode("ethereum", "token", eth).status, PENDING)
        eth = f.evm_bundle("ethereum", "token", depth=0)
        self.assertEqual(self.decode("ethereum", "token", eth).status, FOUND)
        bsc = f.evm_bundle("bsc", "token", depth=-3)
        self.assertEqual(self.decode("bsc", "token", bsc).status, PENDING)

    def test_mempool_and_orphaned_block(self):
        bundle = f.evm_bundle("base", "token")
        bundle["receipt"] = None
        self.assertEqual(self.decode("base", "token", bundle).status, PENDING)
        bundle = f.evm_bundle("base", "token")
        bundle["block"]["hash"] = "0x" + "ab" * 32
        self.assertEqual(self.decode("base", "token", bundle).status, PENDING)
        self.assertEqual(self.decode("base", "token", {"tx": None}).status, NOT_FOUND)

    def test_native_with_spoofed_token_calldata(self):
        # A plain ETH send whose calldata mimics transfer(wallet, 1e12): only the
        # tx value counts, and it is ETH, never USDT.
        bundle = f.evm_bundle("bsc", "native")
        to = f.EVM_RECIPIENTS[("bsc", "native")][0]
        bundle["tx"]["input"] = "0xa9059cbb" + _topic(to)[2:] + _word(10**12)[2:]
        view = self.decode("bsc", "native", bundle)
        self.assertEqual(len(view.transfers), 1)
        self.assertEqual(view.transfers[0].asset, "BNB")
        self.assertEqual(view.transfers[0].raw, 294151565944543700)

    def test_zero_value_native_is_nothing(self):
        bundle = f.evm_bundle("bsc", "native")
        bundle["tx"]["value"] = "0x0"
        self.assertEqual(self.decode("bsc", "native", bundle).transfers, ())

    def test_two_transfers_in_one_tx(self):
        bundle = f.evm_bundle("ethereum", "token")
        log = dict(bundle["receipt"]["logs"][0])
        log["data"] = _word(3_000_000)
        log["logIndex"] = "0x9"
        bundle["receipt"]["logs"].append(log)
        view = self.decode("ethereum", "token", bundle)
        keys = [t.key for t in view.transfers]
        tx = f.evm_hash("ethereum", "token")
        self.assertEqual(keys, [f"{tx}_0", f"{tx}_1"])
        self.assertEqual([t.raw for t in view.transfers], [1120000, 3000000])

    def test_self_transfer(self):
        sender = f.load("ethereum_token_tx.json")["result"]["from"]
        wallets = f.wallet_map(
            [{"chain": "evm", "address": _checksum(sender), "owner": "RT"}]
        )
        view = decode_bundle(
            "ethereum",
            f.evm_hash("ethereum", "token"),
            f.evm_bundle("ethereum", "token"),
            wallets,
        )
        self.assertEqual(view.transfers, ())
        self.assertIn("self_transfer", view.skipped)

    def test_token_from_tracked_wallet_is_self_transfer(self):
        bundle = f.evm_bundle("ethereum", "token")
        other = f.EVM_RECIPIENTS[("bsc", "token")][0]
        bundle["receipt"]["logs"][0]["topics"][1] = _topic(other)
        view = self.decode("ethereum", "token", bundle)
        self.assertEqual(view.transfers, ())
        self.assertIn("self_transfer", view.skipped)

    def test_transfer_topic_and_shape_required(self):
        bundle = f.evm_bundle("ethereum", "token")
        self.assertEqual(bundle["receipt"]["logs"][0]["topics"][0], TRANSFER_TOPIC)
        bundle["receipt"]["logs"][0]["topics"].append(_word(1))
        self.assertEqual(self.decode("ethereum", "token", bundle).transfers, ())

    def test_eighteen_decimal_precision(self):
        bundle = f.evm_bundle("bsc", "token")
        bundle["receipt"]["logs"][0]["data"] = _word(1000000000000000001)
        view = self.decode("bsc", "token", bundle)
        self.assertEqual(view.transfers[0].raw, 1000000000000000001)


def _checksum(addr):
    from bot.services.quicknode.addresses import eip55_checksum

    return eip55_checksum(addr)


class TronDecodeTestCase(unittest.TestCase):
    def setUp(self):
        self.wallets = f.wallet_map()

    def decode(self, tx_hash, bundle=None):
        return decode_bundle(
            "tron", tx_hash, bundle or f.tron_bundle(tx_hash), self.wallets
        )

    def test_genuine_usdt(self):
        view = self.decode(f.TRON_USDT_TX)
        self.assertEqual(view.status, FOUND)
        self.assertTrue(view.final)
        (t,) = view.transfers
        self.assertEqual(t.key, f"{f.TRON_USDT_TX}_0")
        self.assertEqual((t.asset, t.raw, t.decimals), ("USDT", 22_000_000, 6))
        self.assertEqual(t.to, f.TRON_WALLET)
        self.assertTrue(t.from_address.startswith("T"))
        self.assertEqual(t.contract, "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
        self.assertEqual(view.block_time, f.TRON_USDT_BLOCK_TIME)

    def test_dust_trx_decodes_as_one_sun(self):
        (t,) = self.decode(f.TRON_DUST_TX).transfers
        self.assertEqual((t.asset, t.raw), ("TRX", 1))

    def test_not_solidified_yet(self):
        self.assertEqual(
            self.decode(f.TRON_USDT_TX, {"tx": {}, "info": {}}).status, NOT_FOUND
        )
        bundle = f.tron_bundle(f.TRON_USDT_TX)
        bundle["info"] = {}
        self.assertEqual(self.decode(f.TRON_USDT_TX, bundle).status, PENDING)

    def test_trc10_transfer_asset_contract_rejected(self):
        bundle = f.tron_bundle(f.TRON_DUST_TX)
        contract = bundle["tx"]["raw_data"]["contract"][0]
        contract["type"] = "TransferAssetContract"
        contract["parameter"]["value"]["asset_name"] = "31303030303035"
        contract["parameter"]["value"]["amount"] = 50_000_000
        view = self.decode(f.TRON_DUST_TX, bundle)
        self.assertEqual(view.transfers, ())
        self.assertIn("unsupported:TransferAssetContract", view.skipped)

    def test_failed_trx_send(self):
        bundle = f.tron_bundle(f.TRON_DUST_TX)
        bundle["tx"]["ret"] = [{"contractRet": "REVERT"}]
        view = self.decode(f.TRON_DUST_TX, bundle)
        self.assertFalse(view.success)
        self.assertEqual(view.transfers, ())

    def test_two_contracts_rejected(self):
        bundle = f.tron_bundle(f.TRON_DUST_TX)
        contracts = bundle["tx"]["raw_data"]["contract"]
        contracts.append(dict(contracts[0]))
        self.assertEqual(self.decode(f.TRON_DUST_TX, bundle).transfers, ())

    def test_reverted_usdt(self):
        bundle = f.tron_bundle(f.TRON_USDT_TX)
        bundle["info"]["receipt"]["result"] = "REVERT"
        view = self.decode(f.TRON_USDT_TX, bundle)
        self.assertFalse(view.success)
        self.assertEqual(view.transfers, ())

    def test_fake_trc20_same_symbol(self):
        bundle = f.tron_bundle(f.TRON_USDT_TX)
        bundle["info"]["log"][0]["address"] = "11" * 20
        view = self.decode(f.TRON_USDT_TX, bundle)
        self.assertEqual(view.transfers, ())
        self.assertIn("not_allowlisted", view.skipped)

    def test_wrong_recipient(self):
        bundle = f.tron_bundle(f.TRON_USDT_TX)
        bundle["info"]["log"][0]["topics"][2] = "0" * 24 + "22" * 20
        self.assertEqual(self.decode(f.TRON_USDT_TX, bundle).transfers, ())

    def test_self_transfer(self):
        sender = tron_hex_to_base58("4185a08ed989045d6a4fe2b0d4926ce0960e3a6f26")
        wallets = f.wallet_map([{"chain": "tron", "address": sender, "owner": "RT"}])
        view = decode_bundle(
            "tron", f.TRON_USDT_TX, f.tron_bundle(f.TRON_USDT_TX), wallets
        )
        self.assertEqual(view.transfers, ())
        self.assertIn("self_transfer", view.skipped)


class SolanaDecodeTestCase(unittest.TestCase):
    def setUp(self):
        self.wallets = f.wallet_map()

    def decode(self, kind, bundle=None):
        return decode_bundle(
            "solana", f.solana_sig(kind), bundle or f.solana_bundle(kind), self.wallets
        )

    def test_genuine_usdc(self):
        (t,) = self.decode("usdc").transfers
        self.assertEqual((t.asset, t.raw, t.decimals), ("USDC", 50_000_000, 6))
        self.assertEqual(t.to, f.SOL_USDC_WALLET)
        self.assertEqual(
            t.key,
            f"{f.solana_sig('usdc')}_EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v_{f.SOL_USDC_WALLET}",
        )
        # A relayer paid the fee; the sender is the token owner whose balance fell.
        self.assertEqual(t.from_address, "5QtyKPHtWUf45bNb5buQ6UxpL2ekSJxBCK9g8xMCsc9U")

    def test_genuine_sol(self):
        (t,) = self.decode("sol").transfers
        self.assertEqual((t.asset, t.raw, t.decimals), ("SOL", 409_100_000, 9))
        self.assertEqual(t.key, f"{f.solana_sig('sol')}_SOL_{f.SOL_SOL_WALLET}")

    def test_not_final(self):
        self.assertEqual(self.decode("usdc", {"result": None}).status, NOT_FOUND)

    def test_failed_tx(self):
        bundle = f.solana_bundle("usdc")
        bundle["result"]["meta"]["err"] = {"InstructionError": [0, "Custom"]}
        view = self.decode("usdc", bundle)
        self.assertFalse(view.success)
        self.assertEqual(view.transfers, ())

    def test_token_2022_mint_rejected(self):
        bundle = f.solana_bundle("usdc")
        for key in ("preTokenBalances", "postTokenBalances"):
            for entry in bundle["result"]["meta"][key]:
                entry["programId"] = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
        view = self.decode("usdc", bundle)
        self.assertEqual(view.transfers, ())
        self.assertIn("not_allowlisted", view.skipped)
        self.assertNotEqual(
            SPL_TOKEN_PROGRAM, "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
        )

    def test_fake_mint_rejected(self):
        bundle = f.solana_bundle("usdc")
        for key in ("preTokenBalances", "postTokenBalances"):
            for entry in bundle["result"]["meta"][key]:
                entry["mint"] = "FakeUSDCmint1111111111111111111111111111111"
        self.assertEqual(self.decode("usdc", bundle).transfers, ())

    def test_wrong_recipient(self):
        wallets = f.wallet_map()
        wallets_without = f.wallet_map()
        del wallets_without._by_chain["solana"][f.SOL_USDC_WALLET]
        view = decode_bundle(
            "solana", f.solana_sig("usdc"), f.solana_bundle("usdc"), wallets_without
        )
        self.assertEqual(view.transfers, ())
        self.assertTrue(wallets.is_tracked("solana", f.SOL_USDC_WALLET))

    def test_self_transfer(self):
        wallets = f.wallet_map(
            [
                {
                    "chain": "solana",
                    "address": "5QtyKPHtWUf45bNb5buQ6UxpL2ekSJxBCK9g8xMCsc9U",
                    "owner": "RT",
                }
            ]
        )
        view = decode_bundle(
            "solana", f.solana_sig("usdc"), f.solana_bundle("usdc"), wallets
        )
        self.assertEqual(view.transfers, ())
        self.assertIn("self_transfer", view.skipped)


class BitcoinDecodeTestCase(unittest.TestCase):
    def setUp(self):
        self.wallets = f.wallet_map()

    def decode(self, bundle, wallets=None):
        return decode_bundle("bitcoin", f.btc_txid(), bundle, wallets or self.wallets)

    def test_genuine(self):
        view = self.decode(f.btc_bundle())
        self.assertEqual(view.status, FOUND)
        (t,) = view.transfers
        self.assertEqual(t.key, f"{f.btc_txid()}_0")
        self.assertEqual((t.asset, t.raw), ("BTC", 300136))
        self.assertEqual(t.from_address, f.BTC_SENDER)

    def test_both_esplora_sources_agree(self):
        a = self.decode(f.btc_bundle("mempool"))
        b = self.decode(f.btc_bundle("blockstream"))
        self.assertEqual(a.agreement_key(), b.agreement_key())

    def test_unconfirmed(self):
        bundle = f.btc_bundle()
        bundle["tx"]["status"] = {"confirmed": False}
        self.assertEqual(self.decode(bundle).status, PENDING)
        self.assertEqual(self.decode({"tx": None}).status, NOT_FOUND)

    def test_change_back_to_tracked_sender(self):
        wallets = f.wallet_map(
            [{"chain": "bitcoin", "address": f.BTC_SENDER, "owner": "GTO"}]
        )
        view = self.decode(f.btc_bundle(), wallets)
        self.assertEqual(view.transfers, ())
        self.assertIn("self_transfer", view.skipped)


if __name__ == "__main__":
    unittest.main()
