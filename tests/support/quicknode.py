"""Fixtures and a test wallet map for the QuickNode verifier tests.

Every address here is a public recipient taken from a real tx in
``tests/fixtures/quicknode/``. None of them is a club wallet.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from bot.services.quicknode.addresses import eip55_checksum, tron_hex_to_base58
from bot.services.quicknode.config import parse_tracked_wallets

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "quicknode"

TRON_USDT_TX = "70146ca6d949b2a6cff49191acb5e09b6de836b3317104e1b5af5e36c35318ea"
TRON_DUST_TX = "13524cd2b103269747eb996ede273b5dd59e516e35ea9008cf9457914fb9aed3"
TRON_WALLET = tron_hex_to_base58("fe35757c13a5293dba30a40514ff1de95db57a38")
TRON_USDT_BLOCK_TIME = 1795146501

EVM_RECIPIENTS = {
    ("ethereum", "token"): ("0x4c9d4a9f1fa2e1648e5b3de7f90d366a40bfabd5", "RT"),
    ("ethereum", "native"): ("0xea7f2d6e29772a610212e6731fa5d2265c3a8437", "GTO"),
    ("bsc", "token"): ("0xab17f841eb8ede1a1e39dd41abba863727aa9871", "RT"),
    ("bsc", "native"): ("0xaa46a55200b212e1ae74725c98a732908c307a33", "GTO"),
    ("base", "token"): ("0x786abf52bc51e4ad624d48c4592db44394b32fa8", "RT"),
    ("base", "native"): ("0xb3306bd9a818a479af482f794013d3daf24521b8", "RT"),
}
SOL_USDC_WALLET = "CPE1P6Zo7nSBM1QLSHWsnSSaK2iEhb1Ut2oqu51iY329"
SOL_SOL_WALLET = "9Sf83KdDFDS8ozM68vj6EQFV8rRN576F3q6WgD39LHy"
BTC_WALLET = "bc1q8fv7r260v42g4yt2szn2f6xsvtxe0yn98zaf8c"
BTC_SENDER = "bc1qszfu7qa9ylms987se2445s63hahh589h7w74gs"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


def wallet_entries(extra: list[dict] | None = None) -> list[dict]:
    entries = [
        {"chain": "evm", "address": eip55_checksum(addr), "owner": owner}
        for addr, owner in EVM_RECIPIENTS.values()
    ]
    entries += [
        {"chain": "tron", "address": TRON_WALLET, "owner": "GTO"},
        {"chain": "solana", "address": SOL_USDC_WALLET, "owner": "RT"},
        {"chain": "solana", "address": SOL_SOL_WALLET, "owner": "GTO"},
        {"chain": "bitcoin", "address": BTC_WALLET, "owner": "GTO"},
    ]
    return entries + (extra or [])


def wallets_json(extra: list[dict] | None = None) -> str:
    return json.dumps(wallet_entries(extra))


def wallet_map(extra: list[dict] | None = None):
    return parse_tracked_wallets(wallets_json(extra))


def evm_bundle(chain: str, kind: str, *, depth: int = 50) -> dict:
    """Raw bundle as ``fetch_raw`` returns it; ``depth`` = final_height - tx block."""
    tx = load(f"{chain}_{kind}_tx.json")["result"]
    receipt = load(f"{chain}_{kind}_receipt.json")["result"]
    block = load(f"{chain}_{kind}_block.json")["result"]
    return copy.deepcopy(
        {
            "tx": tx,
            "receipt": receipt,
            "block": block,
            "final_height": int(tx["blockNumber"], 16) + depth,
        }
    )


def evm_hash(chain: str, kind: str) -> str:
    return load(f"{chain}_{kind}_tx.json")["result"]["hash"].lower()


def tron_bundle(tx_hash: str) -> dict:
    prefix = tx_hash[:8]
    return copy.deepcopy(
        {
            "tx": load(f"tron_{prefix}_gettransactionbyid.json"),
            "info": load(f"tron_{prefix}_gettransactioninfobyid.json"),
        }
    )


def solana_bundle(kind: str) -> dict:
    return copy.deepcopy({"result": load(f"solana_{kind}_tx.json")["result"]})


def solana_sig(kind: str) -> str:
    return load(f"solana_{kind}_tx.json")["result"]["transaction"]["signatures"][0]


def btc_bundle(source: str = "mempool", *, confirmations: int = 1) -> dict:
    tx = load(f"bitcoin_tx_{source}.json")
    height = tx["status"]["block_height"]
    return copy.deepcopy({"tx": tx, "tip_height": height + confirmations - 1})


def btc_txid() -> str:
    return load("bitcoin_tx_mempool.json")["txid"]
