"""Pure decoders: raw chain responses -> transfers to tracked wallets.

Every decoder takes what one source returned and gives a ``TxView``. Nothing here
does I/O, so the same function runs on source A's and source B's responses and the
verifier compares the two results.

Rules shared by every chain:
- only an exact allowlisted contract / mint is a token deposit;
- a transfer whose sender (or, for Bitcoin, any input) is a tracked wallet is a
  self-transfer or change, never a deposit;
- zero amounts are dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.services.quicknode.addresses import tron_hex_to_base58
from bot.services.quicknode.config import (
    BITCOIN,
    EVM_CHAINS,
    NATIVES,
    SOLANA,
    TRON,
    TRON_USDT_HEX,
    WalletMap,
    token_for,
)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

FOUND = "found"
NOT_FOUND = "not_found"
PENDING = "pending"  # seen, but not at the chain's wait depth yet
ERROR = "error"


@dataclass(frozen=True)
class Transfer:
    key: str
    to: str
    from_address: str | None
    asset: str
    contract: str | None
    raw: int
    decimals: int


@dataclass(frozen=True)
class TxView:
    status: str
    transfers: tuple[Transfer, ...] = ()
    block_number: int | None = None
    block_hash: str | None = None
    block_time: int | None = None
    success: bool = False
    final: bool = False
    skipped: tuple[str, ...] = field(default=())
    error: str | None = None

    def agreement_key(self) -> tuple:
        return (
            self.block_number,
            self.block_hash,
            self.success,
            frozenset(self.transfers),
        )


def error_view(message: str) -> TxView:
    return TxView(status=ERROR, error=message)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
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


def _topic_address(topic: Any) -> str | None:
    """32-byte topic with 12 zero bytes -> 0x + 40 hex, else None."""
    if not isinstance(topic, str):
        return None
    raw = topic.lower()
    raw = raw[2:] if raw.startswith("0x") else raw
    if len(raw) != 64 or raw[:24] != "0" * 24:
        return None
    try:
        int(raw, 16)
    except ValueError:
        return None
    return "0x" + raw[24:]


def _uint256(data: Any) -> int | None:
    if not isinstance(data, str):
        return None
    raw = data.lower()
    raw = raw[2:] if raw.startswith("0x") else raw
    if len(raw) != 64:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


# ------------------------------------------------------------------- EVM


def decode_evm(
    chain: str,
    tx_hash: str,
    *,
    tx: dict | None,
    receipt: dict | None,
    block: dict | None,
    final_height: int | None,
    wallets: WalletMap,
) -> TxView:
    """``final_height`` is the highest block this source counts as final.

    Ethereum: latest - 11 (12 confirmations); BSC: ``finalized``; Base: ``safe``.
    ``block`` is the canonical block at the receipt's height, so a tx left in an
    orphaned block does not match.
    """
    if chain not in EVM_CHAINS:
        raise ValueError(chain)
    if not tx:
        return TxView(status=NOT_FOUND)
    if (tx.get("hash") or "").lower() != tx_hash:
        return error_view("tx hash mismatch")
    if not receipt or receipt.get("blockHash") is None:
        return TxView(status=PENDING)
    if (receipt.get("transactionHash") or "").lower() != tx_hash:
        return error_view("receipt hash mismatch")
    block_number = _int(receipt.get("blockNumber"))
    block_hash = (receipt.get("blockHash") or "").lower()
    if block_number is None or not block_hash:
        return TxView(status=PENDING)
    if (tx.get("blockHash") or "").lower() != block_hash:
        return error_view("tx and receipt block differ")
    if not block or (block.get("hash") or "").lower() != block_hash:
        # The canonical block at this height is another one: reorged out, or the
        # source is behind. Treat as not final and look again later.
        return TxView(status=PENDING, block_number=block_number)
    block_time = _int(block.get("timestamp"))
    final = final_height is not None and block_number <= final_height
    success = _int(receipt.get("status")) == 1

    transfers: list[Transfer] = []
    skipped: list[str] = []
    sender = (tx.get("from") or "").lower() or None
    if wallets.is_tracked(chain, sender):
        skipped.append("self_transfer")
    else:
        native = NATIVES[chain]
        value = _int(tx.get("value")) or 0
        to = (tx.get("to") or "").lower()
        if to and value > 0 and wallets.is_tracked(chain, to):
            transfers.append(
                Transfer(
                    key=f"{tx_hash}_native",
                    to=to,
                    from_address=sender,
                    asset=native.symbol,
                    contract=None,
                    raw=value,
                    decimals=native.decimals,
                )
            )
        for position, log in enumerate(receipt.get("logs") or []):
            if not isinstance(log, dict) or log.get("removed"):
                continue
            topics = log.get("topics") or []
            if len(topics) != 3 or str(topics[0]).lower() != TRANSFER_TOPIC:
                continue
            log_to = _topic_address(topics[2])
            if not log_to or not wallets.is_tracked(chain, log_to):
                continue
            token = token_for(chain, str(log.get("address") or ""))
            if token is None:
                skipped.append("not_allowlisted")
                continue
            log_from = _topic_address(topics[1])
            if wallets.is_tracked(chain, log_from):
                skipped.append("self_transfer")
                continue
            amount = _uint256(log.get("data"))
            if not amount:
                skipped.append("zero")
                continue
            transfers.append(
                Transfer(
                    key=f"{tx_hash}_{position}",
                    to=log_to,
                    from_address=log_from,
                    asset=token.symbol,
                    contract=token.contract,
                    raw=amount,
                    decimals=token.decimals,
                )
            )
    return TxView(
        status=FOUND if final else PENDING,
        transfers=tuple(transfers) if success else (),
        block_number=block_number,
        block_hash=block_hash,
        block_time=block_time,
        success=success,
        final=final,
        skipped=tuple(skipped),
    )


# ------------------------------------------------------------------- Tron


def _tron_log_address(value: Any) -> str | None:
    """Tron log addresses come as 40 hex (no 41 prefix) or 42 hex; -> 41 + 40 hex."""
    raw = str(value or "").lower().removeprefix("0x")
    if len(raw) == 40:
        return "41" + raw
    if len(raw) == 42 and raw.startswith("41"):
        return raw
    return None


def decode_tron(
    tx_hash: str,
    *,
    tx: dict | None,
    info: dict | None,
    wallets: WalletMap,
) -> TxView:
    """``walletsolidity`` responses only: a solidified tx is final, ``{}`` is not yet.

    Tron's tx info carries no block hash. A solidified block cannot reorg, so the
    block identity compared across sources is ``<number>:<timestamp>``.
    """
    if not tx or not tx.get("txID"):
        return TxView(status=NOT_FOUND)
    if str(tx.get("txID")).lower() != tx_hash:
        return error_view("tx hash mismatch")
    if not info or info.get("blockNumber") is None:
        return TxView(status=PENDING)
    if str(info.get("id") or "").lower() != tx_hash:
        return error_view("tx info hash mismatch")
    block_number = _int(info.get("blockNumber"))
    ts_ms = _int(info.get("blockTimeStamp"))
    block_time = ts_ms // 1000 if ts_ms is not None else None
    block_hash = f"{block_number}:{ts_ms}"

    contracts = (tx.get("raw_data") or {}).get("contract") or []
    rets = tx.get("ret") or []
    contract_ret = (rets[0] or {}).get("contractRet") if rets else None
    base = dict(
        status=FOUND,
        block_number=block_number,
        block_hash=block_hash,
        block_time=block_time,
        final=True,
    )
    if len(contracts) != 1:
        return TxView(**base, success=False, skipped=("multi_contract",))
    contract = contracts[0] or {}
    ctype = contract.get("type")
    value = (contract.get("parameter") or {}).get("value") or {}
    owner_hex = str(value.get("owner_address") or "")
    try:
        sender = tron_hex_to_base58(owner_hex) if owner_hex else None
    except ValueError:
        sender = None

    if ctype == "TransferContract":
        success = contract_ret == "SUCCESS"
        if wallets.is_tracked(TRON, sender):
            return TxView(**base, success=success, skipped=("self_transfer",))
        try:
            to = tron_hex_to_base58(str(value.get("to_address") or ""))
        except ValueError:
            return TxView(**base, success=success, skipped=("bad_address",))
        amount = _int(value.get("amount")) or 0
        transfers: tuple[Transfer, ...] = ()
        if success and amount > 0 and wallets.is_tracked(TRON, to):
            native = NATIVES[TRON]
            transfers = (
                Transfer(
                    key=f"{tx_hash}_native",
                    to=to,
                    from_address=sender,
                    asset=native.symbol,
                    contract=None,
                    raw=amount,
                    decimals=native.decimals,
                ),
            )
        return TxView(**base, success=success, transfers=transfers)

    if ctype == "TriggerSmartContract":
        receipt = info.get("receipt") or {}
        success = contract_ret == "SUCCESS" and receipt.get("result") == "SUCCESS"
        if not success:
            return TxView(**base, success=False)
        found: list[Transfer] = []
        skipped: list[str] = []
        sender_tracked = wallets.is_tracked(TRON, sender)
        for position, log in enumerate(info.get("log") or []):
            if not isinstance(log, dict):
                continue
            topics = [
                "0x" + str(t).lower().removeprefix("0x")
                for t in log.get("topics") or []
            ]
            if len(topics) != 3 or topics[0] != TRANSFER_TOPIC:
                continue
            to_hex = _topic_address(topics[2])
            if not to_hex:
                continue
            to = tron_hex_to_base58(to_hex)
            if not wallets.is_tracked(TRON, to):
                continue
            if _tron_log_address(log.get("address")) != TRON_USDT_HEX:
                skipped.append("not_allowlisted")
                continue
            from_hex = _topic_address(topics[1])
            log_from = tron_hex_to_base58(from_hex) if from_hex else None
            if sender_tracked or wallets.is_tracked(TRON, log_from):
                skipped.append("self_transfer")
                continue
            amount = _uint256(log.get("data"))
            if not amount:
                skipped.append("zero")
                continue
            token = token_for(TRON, tron_hex_to_base58(TRON_USDT_HEX))
            assert token is not None
            found.append(
                Transfer(
                    key=f"{tx_hash}_{position}",
                    to=to,
                    from_address=log_from,
                    asset=token.symbol,
                    contract=token.contract,
                    raw=amount,
                    decimals=token.decimals,
                )
            )
        return TxView(
            **base, success=True, transfers=tuple(found), skipped=tuple(skipped)
        )

    # TransferAssetContract (TRC10) and everything else: never a deposit.
    return TxView(
        **base, success=contract_ret == "SUCCESS", skipped=(f"unsupported:{ctype}",)
    )


# ----------------------------------------------------------------- Solana


def _account_keys(tx: dict) -> list[str]:
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    out: list[str] = []
    for key in keys:
        out.append(key.get("pubkey") if isinstance(key, dict) else str(key))
    return out


def decode_solana(sig: str, *, result: dict | None, wallets: WalletMap) -> TxView:
    """``getTransaction`` at ``finalized`` / ``jsonParsed``; null means not final yet.

    Solana's getTransaction has no block hash; a finalized slot cannot reorg, so
    the block identity compared across sources is ``<slot>:<blockTime>``.
    """
    if not result:
        return TxView(status=NOT_FOUND)
    sigs = (result.get("transaction") or {}).get("signatures") or []
    if not sigs or sigs[0] != sig:
        return error_view("signature mismatch")
    meta = result.get("meta") or {}
    slot = _int(result.get("slot"))
    block_time = _int(result.get("blockTime"))
    base = dict(
        status=FOUND,
        block_number=slot,
        block_hash=f"{slot}:{block_time}",
        block_time=block_time,
        final=True,
    )
    if meta.get("err") is not None:
        return TxView(**base, success=False)

    keys = _account_keys(result)
    fee_payer = keys[0] if keys else None
    if wallets.is_tracked(SOLANA, fee_payer):
        return TxView(**base, success=True, skipped=("self_transfer",))

    deltas: dict[tuple[str, str], int] = {}
    decimals_seen: dict[tuple[str, str], int] = {}
    skipped: list[str] = []
    for sign, entries in (
        (-1, meta.get("preTokenBalances")),
        (1, meta.get("postTokenBalances")),
    ):
        for entry in entries or []:
            owner = entry.get("owner")
            mint = entry.get("mint")
            ui = entry.get("uiTokenAmount") or {}
            amount = _int(ui.get("amount"))
            if not owner or not mint or amount is None:
                continue
            program = entry.get("programId")
            if program is not None and program != SPL_TOKEN_PROGRAM:
                mint = f"{program}:{mint}"
            pair = (owner, mint)
            deltas[pair] = deltas.get(pair, 0) + sign * amount
            if ui.get("decimals") is not None:
                decimals_seen[pair] = int(ui["decimals"])

    transfers: list[Transfer] = []
    for (owner, mint), delta in sorted(deltas.items()):
        if delta <= 0 or not wallets.is_tracked(SOLANA, owner):
            continue
        token = token_for(SOLANA, mint)
        if token is None or decimals_seen.get((owner, mint)) != token.decimals:
            skipped.append("not_allowlisted")
            continue
        senders = [o for (o, m), d in deltas.items() if m == mint and d < 0]
        source_owner = senders[0] if len(senders) == 1 else fee_payer
        if wallets.is_tracked(SOLANA, source_owner):
            skipped.append("self_transfer")
            continue
        transfers.append(
            Transfer(
                key=f"{sig}_{mint}_{owner}",
                to=owner,
                from_address=source_owner,
                asset=token.symbol,
                contract=token.contract,
                raw=delta,
                decimals=token.decimals,
            )
        )

    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    native = NATIVES[SOLANA]
    for i, key in enumerate(keys):
        if i >= len(pre) or i >= len(post) or not wallets.is_tracked(SOLANA, key):
            continue
        delta = int(post[i]) - int(pre[i])
        if delta > 0:
            transfers.append(
                Transfer(
                    key=f"{sig}_SOL_{key}",
                    to=key,
                    from_address=fee_payer,
                    asset=native.symbol,
                    contract=None,
                    raw=delta,
                    decimals=native.decimals,
                )
            )
    return TxView(
        **base, success=True, transfers=tuple(transfers), skipped=tuple(skipped)
    )


# ---------------------------------------------------------------- Bitcoin


def decode_bitcoin(
    txid: str,
    *,
    tx: dict | None,
    tip_height: int | None,
    min_conf: int,
    wallets: WalletMap,
) -> TxView:
    """Esplora ``/tx/<txid>``; one transfer per vout to a tracked address."""
    if not tx:
        return TxView(status=NOT_FOUND)
    if str(tx.get("txid") or "").lower() != txid:
        return error_view("txid mismatch")
    status = tx.get("status") or {}
    if not status.get("confirmed"):
        return TxView(status=PENDING)
    height = _int(status.get("block_height"))
    if height is None or tip_height is None:
        return TxView(status=PENDING)
    final = tip_height - height + 1 >= min_conf
    base = dict(
        status=FOUND if final else PENDING,
        block_number=height,
        block_hash=str(status.get("block_hash") or "").lower() or None,
        block_time=_int(status.get("block_time")),
        final=final,
        success=True,
    )
    inputs = [
        ((vin.get("prevout") or {}).get("scriptpubkey_address"))
        for vin in tx.get("vin") or []
    ]
    if any(wallets.is_tracked(BITCOIN, a) for a in inputs if a):
        return TxView(**base, skipped=("self_transfer",))
    sender = inputs[0] if inputs else None
    native = NATIVES[BITCOIN]
    transfers: list[Transfer] = []
    for i, vout in enumerate(tx.get("vout") or []):
        addr = vout.get("scriptpubkey_address")
        tracked = wallets.get(BITCOIN, addr)
        value = _int(vout.get("value")) or 0
        if tracked is None or value <= 0:
            continue
        transfers.append(
            Transfer(
                key=f"{txid}_{i}",
                to=tracked.address,
                from_address=sender,
                asset=native.symbol,
                contract=None,
                raw=value,
                decimals=native.decimals,
            )
        )
    return TxView(**base, transfers=tuple(transfers))
