"""QuickNode deposit verifier job.

Every 60 s: lease due hints, re-fetch each tx from two independent sources,
decode both, and credit only when they agree, the tx is at the chain's wait depth,
and every amount comes from on-chain data. Prices come from two feeds. Anything
that passed the chain checks but cannot be priced or posted is held for staff,
never dropped.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot.services.crypto_payments import ingest_crypto_payment
from bot.services.quicknode import config as qc
from bot.services.quicknode import sources, store
from bot.services.quicknode.decode import (
    ERROR,
    FOUND,
    NOT_FOUND,
    PENDING,
    Transfer,
    TxView,
)
from bot.services.quicknode.pricing import DUST_USD, PriceBook, usd_cents_string

logger = logging.getLogger(__name__)

SLACK_SOURCE = "quicknode"
BACKOFF_BASE = timedelta(seconds=60)
BACKOFF_MAX = timedelta(minutes=15)

# Source label -> when it went down. One Slack alert per outage, not per row.
_OUTAGES: dict[str, datetime] = {}


class PostFailed(Exception):
    """ingest raised before a payment row existed; safe to retry the hint."""


@dataclass(frozen=True)
class Decision:
    action: str  # retry | reject | hold | verified
    reason: str
    view: TxView | None = None


def decide(a: TxView, b: TxView, *, age: timedelta, floor: int) -> Decision:
    """Pure rule on two sources' views of one tx."""
    if a.status == FOUND and b.status == FOUND:
        if a.agreement_key() != b.agreement_key():
            return Decision("hold", "sources_disagree")
        if not a.success:
            return Decision("reject", "reverted")
        if a.block_number is None or a.block_number < floor:
            return Decision("reject", "below_start_block")
        if not a.transfers:
            reason = a.skipped[0] if a.skipped else "no_tracked_transfer"
            return Decision("reject", reason)
        return Decision("verified", "ok", a)

    statuses = {a.status, b.status}
    if age >= qc.NOT_FOUND_WINDOW:
        if statuses == {NOT_FOUND}:
            return Decision("reject", "not_found")
        if NOT_FOUND in statuses and statuses & {FOUND, PENDING}:
            return Decision("hold", "single_source")
    if age >= qc.GIVE_UP_WINDOW:
        return Decision("hold", "timeout")
    if ERROR in statuses:
        return Decision("retry", "source_error")
    if PENDING in statuses or FOUND in statuses:
        return Decision("retry", "not_final")
    return Decision("retry", "not_found_yet")


def backoff(attempts: int) -> timedelta:
    return min(BACKOFF_BASE * (2 ** min(attempts, 10)), BACKOFF_MAX)


async def alert(text: str) -> None:
    from bot.services.slack_ops_notify import notify_slack_ops_channel

    try:
        await notify_slack_ops_channel(
            text, source=SLACK_SOURCE, channel_id=qc.slack_channel_id()
        )
    except Exception:
        logger.exception("quicknode: slack alert failed")


async def _track_source_health(
    chain: str, which: str, view: TxView, now: datetime
) -> None:
    label = sources.source_label(chain, which)
    if view.status == ERROR:
        if label not in _OUTAGES:
            _OUTAGES[label] = now
            await alert(f"[quicknode] source down: {label} ({view.error}). Retrying.")
    elif label in _OUTAGES:
        since = _OUTAGES.pop(label)
        minutes = int((now - since).total_seconds() // 60)
        await alert(f"[quicknode] source recovered: {label} after {minutes} min")


def _paid_at(block_time: int | None) -> str | None:
    if block_time is None:
        return None
    return datetime.fromtimestamp(block_time, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _describe(chain: str, owner: str, t: Transfer, amount_usd: str | None) -> str:
    usd = f"${amount_usd}" if amount_usd else "unpriced"
    return f"{owner} {chain} {t.asset} {usd} to {t.to} from {t.from_address or '?'}"


async def _handle_transfer(
    *,
    chain: str,
    tx_hash: str,
    view: TxView,
    t: Transfer,
    wallets: qc.WalletMap,
    prices: PriceBook,
    now: datetime,
) -> str:
    """Price, then post / shadow / hold / reject one transfer. Returns its state."""
    if store.transfer_exists(t.key):
        return "duplicate"
    wallet = wallets.get(chain, t.to)
    assert wallet is not None
    owner = wallet.owner
    explorer = qc.explorer_url(chain, tx_hash)
    row = dict(
        source_external_id=t.key,
        chain=chain,
        tx_hash=tx_hash,
        owner=owner,
        asset_symbol=t.asset,
        contract=t.contract,
        to_address=t.to,
        from_address=t.from_address,
        raw_amount=str(t.raw),
        decimals=t.decimals,
        explorer_url=explorer,
        block_number=view.block_number,
        block_hash=view.block_hash,
    )

    async def hold(reason: str, **extra) -> str:
        if store.insert_transfer(
            **row, **extra, state=store.TRANSFER_HELD, reason=reason
        ):
            await alert(
                f"[quicknode] HELD ({reason}): {_describe(chain, owner, t, extra.get('amount_usd'))}. "
                f"Verified on-chain; credit manually. {explorer}"
            )
        return store.TRANSFER_HELD

    if (
        view.block_time is None
        or now - datetime.fromtimestamp(view.block_time, timezone.utc)
        > qc.PRICE_MAX_AGE
    ):
        return await hold("stale_price")
    quote = await prices.quote(t.asset)
    if not quote.ok or quote.price is None:
        return await hold(
            quote.reason or "price_unavailable", price_sources=quote.sources
        )
    amount_usd = usd_cents_string(t.raw, t.decimals, quote.price)
    priced = dict(
        usd_price=str(quote.price), price_sources=quote.sources, amount_usd=amount_usd
    )
    if Decimal(amount_usd) < DUST_USD:
        store.insert_transfer(
            **row, **priced, state=store.TRANSFER_REJECTED, reason="dust"
        )
        return store.TRANSFER_REJECTED

    if chain not in qc.post_chains():
        if store.insert_transfer(
            **row, **priced, state=store.TRANSFER_SHADOW, reason=None
        ):
            await alert(
                f"[shadow] would post {_describe(chain, owner, t, amount_usd)} "
                f"as QuickNode {owner} {chain}. {explorer}"
            )
        return store.TRANSFER_SHADOW

    try:
        result = await ingest_crypto_payment(
            amount=amount_usd,
            token_symbol=t.asset,
            chain=chain,
            from_address=t.from_address or "unknown",
            to_address=t.to,
            transaction_hash=tx_hash,
            alert_name=f"QuickNode {owner} {chain}",
            method_owner=qc.OWNER_METHOD[owner],
            paid_at=_paid_at(view.block_time),
            source_external_id=t.key,
            test=qc.test_posts(),
            allow_auto_bind=False,
        )
    except Exception as exc:
        payment_id = store.crypto_payment_id_for(t.key)
        if payment_id is None:
            raise PostFailed(type(exc).__name__) from exc
        logger.exception("quicknode: ingest raised after commit key=%s", t.key)
        store.insert_transfer(
            **row,
            **priced,
            state=store.TRANSFER_NOTIFY_FAILED,
            reason=type(exc).__name__,
            crypto_payment_id=payment_id,
        )
        await alert(
            f"[quicknode] NOTIFY FAILED: payment #{payment_id} saved but the Telegram "
            f"post failed ({type(exc).__name__}); it will not be re-sent. "
            f"{_describe(chain, owner, t, amount_usd)}. {explorer}"
        )
        return store.TRANSFER_NOTIFY_FAILED
    store.insert_transfer(
        **row,
        **priced,
        state=store.TRANSFER_POSTED,
        reason=None if result.created else "already_ingested",
        crypto_payment_id=result.payment_id,
    )
    return store.TRANSFER_POSTED


async def process_hint(
    hint: store.ClaimedHint,
    *,
    wallets: qc.WalletMap,
    prices: PriceBook,
    now: datetime,
) -> str:
    chain, tx_hash = hint.chain, hint.tx_hash
    a, b = await asyncio.gather(
        sources.fetch_view(chain, sources.SOURCE_A, tx_hash, wallets),
        sources.fetch_view(chain, sources.SOURCE_B, tx_hash, wallets),
    )
    await _track_source_health(chain, sources.SOURCE_A, a, now)
    await _track_source_health(chain, sources.SOURCE_B, b, now)
    decision = decide(a, b, age=now - hint.first_seen_at, floor=qc.start_block(chain))
    attempts = hint.attempts + 1

    if decision.action == "retry":
        store.finish_hint(
            hint.id,
            state=store.HINT_PENDING,
            reason=decision.reason,
            attempts=attempts,
            next_check_at=now + backoff(hint.attempts),
        )
        return "retry"
    if decision.action == "reject":
        store.finish_hint(
            hint.id,
            state=store.HINT_REJECTED,
            reason=decision.reason,
            attempts=attempts,
        )
        return "rejected"
    if decision.action == "hold":
        alerted = None
        if hint.alerted_at is None:
            await alert(
                f"[quicknode] HELD tx ({decision.reason}): {chain} "
                f"{qc.explorer_url(chain, tx_hash)} source A={a.status} B={b.status}. "
                "Check by hand."
            )
            alerted = now
        store.finish_hint(
            hint.id,
            state=store.HINT_HELD,
            reason=decision.reason,
            attempts=attempts,
            alerted_at=alerted,
        )
        return "held"

    view = decision.view
    assert view is not None
    states: list[str] = []
    try:
        for t in view.transfers:
            states.append(
                await _handle_transfer(
                    chain=chain,
                    tx_hash=tx_hash,
                    view=view,
                    t=t,
                    wallets=wallets,
                    prices=prices,
                    now=now,
                )
            )
    except PostFailed as exc:
        logger.warning("quicknode: post failed, will retry tx=%s err=%s", tx_hash, exc)
        store.finish_hint(
            hint.id,
            state=store.HINT_PENDING,
            reason=f"post_failed:{exc}",
            attempts=attempts,
            next_check_at=now + backoff(hint.attempts),
        )
        return "retry"
    store.finish_hint(
        hint.id, state=store.HINT_DONE, reason=",".join(states), attempts=attempts
    )
    return "done"


_CONFIG_ALERTED = False


async def run_verifier_once(now: datetime | None = None) -> dict[str, int]:
    global _CONFIG_ALERTED
    current = now or datetime.now(timezone.utc)
    try:
        wallets = qc.load_tracked_wallets()
    except ValueError as exc:
        logger.error("quicknode: tracked wallets invalid: %s", exc)
        if not _CONFIG_ALERTED:
            _CONFIG_ALERTED = True
            await alert(f"[quicknode] verifier stopped: {exc}")
        return {}
    _CONFIG_ALERTED = False
    hints = await asyncio.to_thread(
        store.claim_due_hints, current, limit=qc.CLAIM_BATCH, lease=qc.CLAIM_LEASE
    )
    prices = PriceBook()
    counts: dict[str, int] = {}
    for hint in hints:
        try:
            outcome = await process_hint(
                hint, wallets=wallets, prices=prices, now=current
            )
        except Exception:
            logger.exception("quicknode: hint failed id=%s", hint.id)
            store.finish_hint(
                hint.id,
                state=store.HINT_PENDING,
                reason="internal_error",
                attempts=hint.attempts + 1,
                next_check_at=current + backoff(hint.attempts),
            )
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


async def quicknode_verifier_job_callback(context) -> None:
    del context
    try:
        counts = await run_verifier_once()
        if counts:
            logger.info("quicknode verifier run %s", counts)
    except Exception:
        logger.exception("quicknode verifier job failed")


def schedule_quicknode_verifier_job(app) -> None:
    if not qc.verifier_enabled():
        logger.info("quicknode verifier: off (%s unset)", qc.TRACKED_WALLETS_ENV)
        return
    if app.job_queue is None:
        logger.warning("quicknode verifier: job_queue unavailable")
        return
    app.job_queue.run_repeating(
        quicknode_verifier_job_callback,
        interval=qc.JOB_INTERVAL,
        first=qc.JOB_INTERVAL,
        name="quicknode_verifier",
    )
    logger.info(
        "quicknode verifier scheduled interval_sec=%s post_chains=%s",
        int(qc.JOB_INTERVAL.total_seconds()),
        ",".join(sorted(qc.post_chains())) or "none",
    )
