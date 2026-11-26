"""Fetch a tx from source A or B and decode it.

``fetch_raw`` does the HTTP and returns a plain dict bundle; ``fetch_view`` feeds
the bundle to the pure decoders. Tests replace ``fetch_raw``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bot.services.quicknode import config as qc
from bot.services.quicknode.decode import (
    TxView,
    decode_bitcoin,
    decode_evm,
    decode_solana,
    decode_tron,
    error_view,
)

logger = logging.getLogger(__name__)

SOURCE_A = "a"
SOURCE_B = "b"
HTTP_TIMEOUT_SEC = 15.0


class SourceError(Exception):
    """Transport or RPC error; the tx's state on this source is unknown."""


def source_url(chain: str, which: str) -> str:
    return qc.source_a_url(chain) if which == SOURCE_A else qc.source_b_url(chain)


def source_label(chain: str, which: str) -> str:
    """Name for logs and Slack without the URL (QuickNode URLs embed a token)."""
    if chain == qc.BITCOIN:
        return f"{chain}:{'mempool.space' if which == SOURCE_A else 'blockstream'}"
    return f"{chain}:{'quicknode' if which == SOURCE_A else 'public'}"


async def _rpc(client: httpx.AsyncClient, url: str, method: str, params: list) -> Any:
    resp = await client.post(
        url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise SourceError(f"{method}: bad response")
    if data.get("error"):
        raise SourceError(f"{method}: rpc error")
    return data.get("result")


async def _fetch_evm(
    client: httpx.AsyncClient, chain: str, url: str, tx_hash: str
) -> dict:
    tx = await _rpc(client, url, "eth_getTransactionByHash", [tx_hash])
    if not tx:
        return {"tx": None}
    receipt = await _rpc(client, url, "eth_getTransactionReceipt", [tx_hash])
    block = None
    if receipt and receipt.get("blockNumber"):
        block = await _rpc(
            client, url, "eth_getBlockByNumber", [receipt["blockNumber"], False]
        )
    if chain == qc.ETHEREUM:
        latest = int(await _rpc(client, url, "eth_blockNumber", []), 16)
        final_height = latest - (qc.ETH_CONFIRMATIONS - 1)
    else:
        tag = "finalized" if chain == qc.BSC else "safe"
        head = await _rpc(client, url, "eth_getBlockByNumber", [tag, False])
        if not head or not head.get("number"):
            raise SourceError(f"no {tag} block")
        final_height = int(head["number"], 16)
    return {"tx": tx, "receipt": receipt, "block": block, "final_height": final_height}


async def _fetch_tron(
    client: httpx.AsyncClient, url: str, tx_hash: str, which: str
) -> dict:
    headers = {}
    key = qc.trongrid_api_key()
    if which == SOURCE_B and key:
        headers["TRON-PRO-API-KEY"] = key
    base = url.rstrip("/")
    out: dict[str, Any] = {}
    for name, path in (
        ("tx", "gettransactionbyid"),
        ("info", "gettransactioninfobyid"),
    ):
        resp = await client.post(
            f"{base}/walletsolidity/{path}", json={"value": tx_hash}, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise SourceError(f"{path}: bad response")
        if data.get("Error") or data.get("error"):
            raise SourceError(f"{path}: api error")
        out[name] = data
    return out


async def _fetch_solana(client: httpx.AsyncClient, url: str, sig: str) -> dict:
    result = await _rpc(
        client,
        url,
        "getTransaction",
        [
            sig,
            {
                "commitment": "finalized",
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": qc.solana_max_tx_version(),
            },
        ],
    )
    return {"result": result}


async def _fetch_bitcoin(client: httpx.AsyncClient, url: str, txid: str) -> dict:
    base = url.rstrip("/")
    resp = await client.get(f"{base}/tx/{txid}")
    if resp.status_code in (400, 404):
        return {"tx": None, "tip_height": None}
    resp.raise_for_status()
    tx = resp.json()
    tip = await client.get(f"{base}/blocks/tip/height")
    tip.raise_for_status()
    return {"tx": tx, "tip_height": int(tip.text.strip())}


async def fetch_raw(chain: str, which: str, tx_hash: str) -> dict:
    url = source_url(chain, which)
    if not url:
        raise SourceError("source not configured")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
        if chain in qc.EVM_CHAINS:
            return await _fetch_evm(client, chain, url, tx_hash)
        if chain == qc.TRON:
            return await _fetch_tron(client, url, tx_hash, which)
        if chain == qc.SOLANA:
            return await _fetch_solana(client, url, tx_hash)
        if chain == qc.BITCOIN:
            return await _fetch_bitcoin(client, url, tx_hash)
    raise SourceError(f"unknown chain {chain}")


def decode_bundle(chain: str, tx_hash: str, raw: dict, wallets: qc.WalletMap) -> TxView:
    if chain in qc.EVM_CHAINS:
        return decode_evm(
            chain,
            tx_hash,
            tx=raw.get("tx"),
            receipt=raw.get("receipt"),
            block=raw.get("block"),
            final_height=raw.get("final_height"),
            wallets=wallets,
        )
    if chain == qc.TRON:
        return decode_tron(
            tx_hash, tx=raw.get("tx"), info=raw.get("info"), wallets=wallets
        )
    if chain == qc.SOLANA:
        return decode_solana(tx_hash, result=raw.get("result"), wallets=wallets)
    if chain == qc.BITCOIN:
        return decode_bitcoin(
            tx_hash,
            tx=raw.get("tx"),
            tip_height=raw.get("tip_height"),
            min_conf=qc.BTC_CONFIRMATIONS,
            wallets=wallets,
        )
    raise ValueError(chain)


async def fetch_view(
    chain: str, which: str, tx_hash: str, wallets: qc.WalletMap
) -> TxView:
    """Never raises: transport, RPC and decode failures become an ``error`` view."""
    try:
        raw = await fetch_raw(chain, which, tx_hash)
        return decode_bundle(chain, tx_hash, raw, wallets)
    except Exception as exc:
        logger.warning(
            "quicknode: fetch failed source=%s tx=%s err=%s",
            source_label(chain, which),
            tx_hash,
            type(exc).__name__,
        )
        return error_view(type(exc).__name__)
