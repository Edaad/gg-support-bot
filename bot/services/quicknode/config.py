"""QuickNode verifier configuration: chains, token allowlist, tracked wallets, env knobs.

The token allowlist is hard-coded on purpose. Only these exact contracts / mints
count as a deposit; a token's symbol or name never does.

Sources (read 2026-11-23): https://tether.to/en/supported-protocols/ and
https://developers.circle.com/stablecoins/usdc-contract-addresses. The two BSC
tokens are Binance-Peg, which the business accepted.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import timedelta

from bot.services.quicknode.addresses import (
    eip55_checksum,
    is_valid_bitcoin,
    is_valid_eip55,
    is_valid_solana,
    is_valid_tron,
    tron_base58_to_hex,
)

ETHEREUM = "ethereum"
BSC = "bsc"
BASE = "base"
TRON = "tron"
SOLANA = "solana"
BITCOIN = "bitcoin"

EVM_CHAINS = (ETHEREUM, BSC, BASE)
CHAINS = (ETHEREUM, BSC, BASE, TRON, SOLANA, BITCOIN)

ETH_CONFIRMATIONS = 12
BTC_CONFIRMATIONS = 1

CLAIM_BATCH = 25
CLAIM_LEASE = timedelta(minutes=5)
NOT_FOUND_WINDOW = timedelta(hours=2)
GIVE_UP_WINDOW = timedelta(hours=24)
PRICE_MAX_AGE = timedelta(minutes=30)
JOB_INTERVAL = timedelta(seconds=60)

OWNER_RT = "RT"
OWNER_GTO = "GTO"
OWNER_METHOD = {OWNER_RT: "round-table", OWNER_GTO: "vaughn"}

DEFAULT_SLACK_CHANNEL_ID = "C0BDML0LS9X"


@dataclass(frozen=True)
class Token:
    chain: str
    symbol: str
    contract: str  # canonical form: lowercase 0x for EVM, base58 for Tron/Solana
    decimals: int
    stable: bool = True


@dataclass(frozen=True)
class Native:
    symbol: str
    decimals: int


ALLOWLIST: tuple[Token, ...] = (
    Token(ETHEREUM, "USDT", "0xdac17f958d2ee523a2206206994597c13d831ec7", 6),
    Token(ETHEREUM, "USDC", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6),
    Token(BASE, "USDC", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", 6),
    Token(BSC, "USDT", "0x55d398326f99059ff775485246999027b3197955", 18),
    Token(BSC, "USDC", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", 18),
    Token(TRON, "USDT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", 6),
    Token(SOLANA, "USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
    Token(SOLANA, "USDT", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
)

TRON_USDT_HEX = "41a614f803b6fd780986a42c78ec9c7f77e6ded13c"

NATIVES: dict[str, Native] = {
    ETHEREUM: Native("ETH", 18),
    BASE: Native("ETH", 18),
    BSC: Native("BNB", 18),
    TRON: Native("TRX", 6),
    SOLANA: Native("SOL", 9),
    BITCOIN: Native("BTC", 8),
}


def token_for(chain: str, contract: str) -> Token | None:
    """Exact contract match only. EVM compare is case-insensitive."""
    key = contract.lower() if chain in EVM_CHAINS else contract
    for token in ALLOWLIST:
        if token.chain == chain and token.contract == key:
            return token
    return None


# ------------------------------------------------------------ tracked wallets

TRACKED_WALLETS_ENV = "QUICKNODE_TRACKED_WALLETS"


@dataclass(frozen=True)
class TrackedWallet:
    chain: str
    address: str  # canonical: lowercase 0x (EVM), base58 T... (Tron), as-is otherwise
    owner: str  # RT | GTO


class WalletMap:
    """Lookup of tracked wallets per chain, by canonical address."""

    def __init__(self, wallets: list[TrackedWallet]):
        self._by_chain: dict[str, dict[str, TrackedWallet]] = {c: {} for c in CHAINS}
        for wallet in wallets:
            self._by_chain[wallet.chain][wallet.address] = wallet

    def get(self, chain: str, address: str | None) -> TrackedWallet | None:
        if not address:
            return None
        return self._by_chain.get(chain, {}).get(canonical_address(chain, address))

    def is_tracked(self, chain: str, address: str | None) -> bool:
        return self.get(chain, address) is not None

    def chains(self) -> list[str]:
        return [c for c in CHAINS if self._by_chain[c]]


def canonical_address(chain: str, address: str) -> str:
    raw = (address or "").strip()
    if chain in EVM_CHAINS:
        return raw.lower()
    if chain == BITCOIN and raw.lower().startswith("bc1"):
        return raw.lower()
    return raw


def parse_tracked_wallets(raw: str) -> WalletMap:
    """Parse and checksum-validate ``QUICKNODE_TRACKED_WALLETS`` JSON.

    ``[{"chain": "evm"|"ethereum"|..., "address": "...", "owner": "RT"|"GTO"}]``.
    An EVM address applies to ethereum, bsc and base. Raises ValueError on any bad
    entry so a typo stops the verifier instead of silently missing deposits.
    """
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        raise ValueError(f"{TRACKED_WALLETS_ENV} must be a JSON list")
    wallets: list[TrackedWallet] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"{TRACKED_WALLETS_ENV}[{i}] must be an object")
        chain = str(entry.get("chain") or "").strip().lower()
        address = str(entry.get("address") or "").strip()
        owner = str(entry.get("owner") or "").strip().upper()
        if owner not in OWNER_METHOD:
            raise ValueError(f"{TRACKED_WALLETS_ENV}[{i}]: owner must be RT or GTO")
        if chain in ("evm", *EVM_CHAINS):
            if not is_valid_eip55(address):
                hint = ""
                if len(address) == 42:
                    try:
                        hint = f" (checksummed form: {eip55_checksum(address)})"
                    except ValueError:
                        hint = ""
                raise ValueError(
                    f"{TRACKED_WALLETS_ENV}[{i}]: EVM address fails EIP-55{hint}"
                )
            for evm_chain in EVM_CHAINS:
                wallets.append(TrackedWallet(evm_chain, address.lower(), owner))
            continue
        if chain == TRON:
            valid = is_valid_tron(address)
        elif chain == SOLANA:
            valid = is_valid_solana(address)
        elif chain == BITCOIN:
            valid = is_valid_bitcoin(address)
        else:
            raise ValueError(f"{TRACKED_WALLETS_ENV}[{i}]: unknown chain {chain!r}")
        if not valid:
            raise ValueError(
                f"{TRACKED_WALLETS_ENV}[{i}]: invalid {chain} address checksum"
            )
        wallets.append(TrackedWallet(chain, canonical_address(chain, address), owner))
    return WalletMap(wallets)


def load_tracked_wallets() -> WalletMap:
    return parse_tracked_wallets(os.getenv(TRACKED_WALLETS_ENV) or "[]")


def tron_hex_of(wallet_address: str) -> str:
    return tron_base58_to_hex(wallet_address)


# --------------------------------------------------------------- env knobs


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


PUBLIC_RPC_DEFAULTS = {
    ETHEREUM: "https://ethereum-rpc.publicnode.com",
    BSC: "https://bsc-dataseed.bnbchain.org",
    BASE: "https://mainnet.base.org",
    TRON: "https://api.trongrid.io",
    SOLANA: "https://api.mainnet-beta.solana.com",
    BITCOIN: "https://blockstream.info/api",
}
BTC_PRIMARY_DEFAULT = "https://mempool.space/api"


def source_a_url(chain: str) -> str:
    """Primary source. QuickNode for every chain but Bitcoin (mempool.space)."""
    url = _env(f"QUICKNODE_RPC_URL_{chain.upper()}")
    if not url and chain == BITCOIN:
        url = BTC_PRIMARY_DEFAULT
    return url


def source_b_url(chain: str) -> str:
    """Independent public source; ``QUICKNODE_PUBLIC_RPC_URL_<CHAIN>`` overrides."""
    return (
        _env(f"QUICKNODE_PUBLIC_RPC_URL_{chain.upper()}") or PUBLIC_RPC_DEFAULTS[chain]
    )


def start_block(chain: str) -> int:
    raw = _env(f"QUICKNODE_START_BLOCK_{chain.upper()}")
    return int(raw) if raw else 0


def post_chains() -> frozenset[str]:
    return frozenset(
        c.strip().lower() for c in _env("QUICKNODE_POST_CHAINS").split(",") if c.strip()
    )


def test_posts() -> bool:
    return _env("QUICKNODE_TEST_POSTS") in ("1", "true", "yes")


def verifier_enabled() -> bool:
    """Off unless the env names at least one tracked wallet."""
    return bool(_env(TRACKED_WALLETS_ENV))


def webhook_secrets() -> list[str]:
    return [s.strip() for s in _env("QUICKNODE_WEBHOOK_SECRET").split(",") if s.strip()]


def trongrid_api_key() -> str:
    return _env("TRONGRID_API_KEY")


def solana_max_tx_version() -> int:
    """``maxSupportedTransactionVersion`` for getTransaction.

    Defaults to 1: in Nov 2026 about 40% of mainnet txs are v1, and an RPC asked
    for version 0 refuses them (-32015), so those deposits would never verify.
    Set ``QUICKNODE_SOLANA_MAX_TX_VERSION=0`` to go back to v0 only.
    """
    raw = _env("QUICKNODE_SOLANA_MAX_TX_VERSION")
    return int(raw) if raw else 1


def slack_channel_id() -> str | None:
    """``QUICKNODE_SLACK_CHANNEL_ID``, else the crypto alerts channel if a bot token is set."""
    explicit = _env("QUICKNODE_SLACK_CHANNEL_ID")
    if explicit:
        return explicit
    if _env("SLACK_OPS_BOT_TOKEN"):
        return DEFAULT_SLACK_CHANNEL_ID
    return None


EXPLORER_TX_URLS = {
    ETHEREUM: "https://etherscan.io/tx/{tx}",
    BSC: "https://bscscan.com/tx/{tx}",
    BASE: "https://basescan.org/tx/{tx}",
    TRON: "https://tronscan.org/#/transaction/{tx}",
    SOLANA: "https://solscan.io/tx/{tx}",
    BITCOIN: "https://mempool.space/tx/{tx}",
}


def explorer_url(chain: str, tx_hash: str) -> str:
    return EXPLORER_TX_URLS[chain].format(tx=tx_hash)
