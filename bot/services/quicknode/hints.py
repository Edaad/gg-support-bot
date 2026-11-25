"""QuickNode webhook: signature check and (chain, tx hash) hint extraction.

A webhook payload is an untrusted hint. Only the tx hashes are kept; amounts,
addresses and tokens in it are ignored, and the verifier re-reads everything
from the chain.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Any, Iterator

from bot.services.quicknode.config import BITCOIN, CHAINS, EVM_CHAINS, SOLANA, TRON

MAX_TIMESTAMP_SKEW_SEC = 300
MAX_HINTS_PER_DELIVERY = 1000
TRON_DUST_SUN = 1_000_000  # 1 TRX

_HEX64_0X = re.compile(r"^0x[0-9a-fA-F]{64}$")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_B58_SIG = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{64,88}$")

_HASH_KEYS = ("transactionHash", "hash", "txid")
_SIG_KEYS = ("signature", "signatures")


class SignatureError(Exception):
    """Missing header, stale timestamp, or HMAC mismatch."""


def verify_signature(
    *,
    body: bytes,
    nonce: str | None,
    timestamp: str | None,
    signature: str | None,
    secrets: list[str],
    now: float | None = None,
) -> None:
    """Raise SignatureError unless one secret signs nonce + timestamp + body."""
    if not secrets:
        raise SignatureError("no webhook secret configured")
    if not nonce or not timestamp or not signature:
        raise SignatureError("missing signature headers")
    try:
        ts = int(timestamp.strip())
    except ValueError as exc:
        raise SignatureError("bad timestamp") from exc
    current = time.time() if now is None else now
    if abs(current - ts) > MAX_TIMESTAMP_SKEW_SEC:
        raise SignatureError("stale timestamp")
    message = nonce.encode("utf-8") + timestamp.encode("utf-8") + body
    given = signature.strip().lower()
    for secret in secrets:
        expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, given):
            return
    raise SignatureError("signature mismatch")


def normalize_tx_hash(chain: str, value: Any) -> str | None:
    """Canonical hash for the chain, or None if the format does not fit.

    EVM: 0x + 64 lowercase hex. Tron: 64 lowercase hex, no 0x (how TronGrid and
    crypto_payments store it). Solana: base58 64-88 chars. Bitcoin: 64 hex.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if chain in EVM_CHAINS:
        return raw.lower() if _HEX64_0X.match(raw) else None
    if chain == TRON:
        if _HEX64_0X.match(raw):
            return raw[2:].lower()
        return raw.lower() if _HEX64.match(raw) else None
    if chain == SOLANA:
        return raw if _B58_SIG.match(raw) else None
    if chain == BITCOIN:
        return raw.lower() if _HEX64.match(raw) else None
    return None


def _walk(node: Any) -> Iterator[tuple[str, Any]]:
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key, value in cur.items():
                yield key, value
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(cur)


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.strip().lower()
        try:
            return int(raw, 16) if raw.startswith("0x") else int(raw)
        except ValueError:
            return None
    return None


def tron_dust_hashes(payload: Any) -> set[str]:
    """Hashes of Tron address-poisoning sends to pre-drop.

    A ``matchingTransactions`` entry with ``input == "0x"``, value under 1 TRX and
    no receipt for the same hash anywhere in the payload.
    """
    receipt_hashes: set[str] = set()
    candidates: set[str] = set()
    for key, value in _walk(payload):
        if key == "matchingReceipts" and isinstance(value, list):
            for receipt in value:
                if isinstance(receipt, dict):
                    norm = normalize_tx_hash(TRON, receipt.get("transactionHash"))
                    if norm:
                        receipt_hashes.add(norm)
        if key == "matchingTransactions" and isinstance(value, list):
            for tx in value:
                if not isinstance(tx, dict) or tx.get("input") != "0x":
                    continue
                amount = _int_value(tx.get("value"))
                norm = normalize_tx_hash(TRON, tx.get("hash"))
                if norm and amount is not None and amount < TRON_DUST_SUN:
                    candidates.add(norm)
    return candidates - receipt_hashes


def extract_hints(chain: str, payload: Any) -> list[str]:
    """Distinct canonical tx hashes in the payload, Tron dust dropped."""
    if chain not in CHAINS:
        raise ValueError(f"unknown chain {chain!r}")
    keys = _SIG_KEYS if chain == SOLANA else _HASH_KEYS
    found: list[str] = []
    seen: set[str] = set()
    for key, value in _walk(payload):
        if key not in keys:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            norm = normalize_tx_hash(chain, item)
            if norm and norm not in seen:
                seen.add(norm)
                found.append(norm)
    if chain == TRON:
        dust = tron_dust_hashes(payload)
        found = [h for h in found if h not in dust]
    return found[:MAX_HINTS_PER_DELIVERY]
