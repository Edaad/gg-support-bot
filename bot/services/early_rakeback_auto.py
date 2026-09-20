"""Stage orchestration for the automated ``/earlyrb`` flow.

Three systems, in a fixed order: the ClubGG RPA bot reads the player's fee for
the week, Elevate quotes what is still owed on it, and — only if the player taps
Claim — Elevate records the payout and the RPA bot adds the chips.

Record-then-add is deliberate. If the chip-add then fails, ``DELETE bot/record``
undoes the Elevate write so the player is not left owing chips the bot will
never deliver. A rollback that itself fails is the remaining one-way door, which
is what ``early_rakeback_claims`` and the loud Slack alert are for.

Player-facing copy says **fee** and **feeback**, never rake or rakeback.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from api.club_slug import CLUB_LABEL_TO_SLUG
from bot.services import elevate_early_rakeback_api as elevate
from bot.services.club import (
    get_early_rakeback_max_auto_amount,
    get_escalate_auto_early_rakeback,
    invalidate_pending_one_time_bypasses,
    record_activity_for_chat,
)
from bot.services.clubgg_deposit_api import (
    LABEL_FEEBACK,
    deposit_api_dry_run,
    resolve_clubgg_club_name,
    run_auto_chip_add,
    run_rake_check,
)
from bot.services.escalation_notification import (
    notify_earlyrb_auto_added,
    notify_earlyrb_auto_failed,
    notify_earlyrb_auto_over_max,
    notify_earlyrb_chips_not_added,
)
from db.connection import get_db
from db.models import EarlyRakebackClaim

logger = logging.getLogger(__name__)

FINDING_FEE_COPY = "Finding your total fee for this week..."
CALCULATING_COPY = "Calculating your remaining feeback for this week..."
ADMIN_SHORTLY_COPY = "An Admin will be with you shortly."
NO_FEE_COPY = "You don't have any fee recorded for this week yet."
UPLINE_INELIGIBLE_COPY = (
    "Unfortunately, members under an agency or a super agency are ineligible for "
    "early feeback. Please contact your agent for more information. If you have "
    "any questions, let us know!"
)
NOTHING_REMAINING_COPY = "You've already claimed all of your feeback for this week."
CLAIM_CANCELLED_COPY = "No problem — your feeback is still there whenever you want it."

# There is no daily limit on claiming, so the only thing the player needs warning
# about before tapping Claim is the effect on their cashout timer.
CASHOUT_TIMER_NOTICE = (
    "Early feeback counts as a deposit and will reset the cashout timer"
)

# Elevate's record endpoint serialises per member, and the screen robot queues
# behind live deposits, so a retry that waits is worth more than a fast failure.
_RECORD_RETRY_DELAYS_SEC = (3.0, 8.0)


@dataclass(frozen=True)
class ClubTarget:
    """Where a claim lands: one ClubGG club and the Elevate slug for the same club."""

    clubgg_club: str
    elevate_slug: str
    union_shorthand: Optional[str]


def resolve_club_target(
    club_name: Optional[str], union_shorthand: Optional[str]
) -> Optional[ClubTarget]:
    """Map a dashboard club (+ chosen union) to its ClubGG club and Elevate slug.

    No new config: ``resolve_clubgg_club_name`` already produces the ClubGG club
    label and ``CLUB_LABEL_TO_SLUG`` already maps that label to Elevate's slug.
    """
    clubgg_club = resolve_clubgg_club_name(club_name, union_shorthand)
    if not clubgg_club:
        return None
    slug = CLUB_LABEL_TO_SLUG.get(clubgg_club.strip().lower())
    if not slug:
        return None
    return ClubTarget(
        clubgg_club=clubgg_club,
        elevate_slug=slug,
        union_shorthand=(union_shorthand or "").strip().upper() or None,
    )


def format_feeback_amount(amount: Decimal, decimal_places: int = 2) -> str:
    """Player-facing money, at the precision Elevate says this club displays."""
    places = decimal_places if decimal_places in (0, 1, 2) else 2
    quantum = Decimal(1).scaleb(-places)
    return f"${amount.quantize(quantum):,.{places}f}"


def format_already_claimed_amount(quote: Any) -> Optional[str]:
    """Elevate's ``totalAlreadyGiven`` for this week, or None when unknown/zero."""
    claimed = getattr(quote, "total_already_given", None)
    if claimed is None or claimed <= 0:
        return None
    return format_feeback_amount(claimed, quote.display_decimal_places)


def format_claim_prompt(quote: Any) -> str:
    """The Claim / Cancel question shown once a quote clears every gate."""
    amount = format_feeback_amount(quote.remaining, quote.display_decimal_places)
    claimed = format_already_claimed_amount(quote)
    claimed_line = (
        f"You've already claimed {claimed} of feeback this week.\n\n" if claimed else ""
    )
    return (
        f"{claimed_line}"
        f"Your total remaining feeback for this week is: {amount}\n\n"
        f"Would you like to claim?\n\n"
        f"{CASHOUT_TIMER_NOTICE}"
    )


def format_nothing_remaining_copy(quote: Any) -> str:
    claimed = format_already_claimed_amount(quote)
    if claimed:
        return f"You've already claimed all of your feeback for this week ({claimed})."
    return NOTHING_REMAINING_COPY


def date_filter_is_suspect(fee: Any) -> bool:
    """True when the week filter looks like it never applied.

    Overall and filtered coming back identical on both rake and PnL means the
    date picker probably did nothing — unless everything is zero, in which case
    they match for the honest reason that the player has no history at all.
    """
    if fee.rake_overall is None or fee.pnl_overall is None:
        return False
    if fee.rake_overall != fee.rake_filtered or fee.pnl_overall != fee.pnl_filtered:
        return False
    return bool(fee.rake_overall) or bool(fee.pnl_overall)


@dataclass(frozen=True)
class FeeStage:
    """Outcome of the fee lookup. ``kind`` is ok | no_fee | has_upline | escalate."""

    kind: str
    fee: Any = None
    detail: str = ""


async def check_fee(
    *,
    club_id: int,
    chat_id: int,
    group_title: Optional[str],
    union_shorthand: Optional[str],
) -> FeeStage:
    """Read this week's fee via the RPA bot and sanity-check the date window."""
    fee = await run_rake_check(
        club_id=club_id,
        chat_id=chat_id,
        request_id=f"earlyrb-rake-{uuid.uuid4().hex[:12]}",
        group_title=group_title,
        union_shorthand=union_shorthand,
    )

    if not fee.ok or fee.rake_filtered is None:
        return FeeStage(
            "escalate",
            fee,
            f"fee lookup {fee.status}: {fee.reason or 'no filtered fee returned'}",
        )

    # An agency member's feeback is their agent's to pay out, so eligibility is
    # settled before the week's numbers matter. An upline the bot could not read
    # is nobody's answer: say nothing to the player and let an admin look.
    if fee.has_upline is None:
        return FeeStage(
            "escalate",
            fee,
            "fee lookup did not report has_upline — cannot tell whether this "
            "member sits under an agency",
        )
    if fee.has_upline:
        return FeeStage("has_upline", fee)

    if date_filter_is_suspect(fee):
        return FeeStage(
            "escalate",
            fee,
            (
                "week filter looks unapplied — overall and filtered figures are "
                f"identical (fee {fee.rake_overall}, PnL {fee.pnl_overall}) for "
                f"range {fee.range_start} to {fee.range_end}"
            ),
        )

    if fee.rake_filtered <= 0:
        return FeeStage("no_fee", fee)

    return FeeStage("ok", fee)


@dataclass(frozen=True)
class QuoteStage:
    """Outcome of the Elevate quote.

    ``kind`` is eligible | nothing_remaining | below_minimum | over_max | escalate.
    """

    kind: str
    quote: Any = None
    detail: str = ""
    player_message: str = ""


async def quote_feeback(
    *,
    club_id: int,
    target: ClubTarget,
    gg_player_id: str,
    fee: Any,
    nickname: Optional[str] = None,
) -> QuoteStage:
    """Ask Elevate what is still owed, then apply our own max-amount gate.

    Warnings and member type never block: the club asked for everything Elevate
    calls eligible to auto-claim, and the warnings ride along to Slack.
    """
    result = await elevate.quote_early_rakeback(
        club_slug=target.elevate_slug,
        gg_player_id=gg_player_id,
        rake=fee.rake_filtered,
        pl=fee.pnl_filtered if fee.pnl_filtered is not None else Decimal("0"),
        nickname=nickname,
    )
    if not result.ok:
        return QuoteStage(
            "escalate", detail=f"quote failed ({result.error_code}): {result.detail}"
        )

    quote = result.quote
    if not quote.eligible:
        if quote.reason == elevate.REASON_NOTHING_REMAINING:
            return QuoteStage(
                "nothing_remaining",
                quote,
                player_message=format_nothing_remaining_copy(quote),
            )
        return QuoteStage(
            "escalate", quote, detail=f"quote not eligible: {quote.reason}"
        )

    if quote.below_minimum:
        minimum = quote.minimum_threshold or Decimal("0")
        remaining_str = format_feeback_amount(
            quote.remaining, quote.display_decimal_places
        )
        minimum_str = format_feeback_amount(minimum, quote.display_decimal_places)
        return QuoteStage(
            "below_minimum",
            quote,
            player_message=(
                f"Sorry! Your remaining feeback ({remaining_str}) for this week is "
                f"below the {minimum_str} minimum, please reach back again later "
                f"when it's over {minimum_str} and we will get it added for you!"
            ),
        )

    max_amount = await asyncio.to_thread(
        get_early_rakeback_max_auto_amount, int(club_id)
    )
    if max_amount is not None and quote.remaining > max_amount:
        return QuoteStage(
            "over_max",
            quote,
            detail=f"remaining {quote.remaining} over auto-claim max {max_amount}",
            player_message=ADMIN_SHORTLY_COPY,
        )

    return QuoteStage("eligible", quote)


def new_idempotency_key(chat_id: int) -> str:
    """One key per Claim press, reused across record retries so it can't double-pay."""
    return f"tg:{int(chat_id)}:{uuid.uuid4().hex}"


def create_claim_row(
    *,
    club_id: int,
    chat_id: int,
    user_id: Optional[int],
    group_title: Optional[str],
    gg_player_id: str,
    nickname: Optional[str],
    target: ClubTarget,
    fee: Any,
    quote: Any,
    idempotency_key: str,
) -> Optional[int]:
    """Persist the attempt before we touch Elevate. Returns the claim row id."""
    try:
        with get_db() as session:
            claim = EarlyRakebackClaim(
                club_id=int(club_id),
                telegram_chat_id=int(chat_id),
                telegram_user_id=int(user_id) if user_id is not None else None,
                group_title=group_title,
                gg_player_id=gg_player_id,
                nickname=nickname,
                union_shorthand=target.union_shorthand,
                clubgg_club=target.clubgg_club,
                elevate_club_slug=target.elevate_slug,
                rake_overall=fee.rake_overall,
                rake_filtered=fee.rake_filtered,
                pnl_overall=fee.pnl_overall,
                pnl_filtered=fee.pnl_filtered,
                range_start=fee.range_start,
                range_end=fee.range_end,
                quoted_amount=quote.remaining,
                rakeback_percentage=quote.percentage,
                member_type=quote.member_type,
                warnings=list(quote.warnings) or None,
                idempotency_key=idempotency_key,
                rpa_rake_job_id=fee.job_id,
                status="quoted",
            )
            session.add(claim)
            session.flush()
            return int(claim.id)
    except Exception:
        logger.exception(
            "earlyrb_auto: could not persist claim row chat_id=%s key=%s",
            chat_id,
            idempotency_key,
        )
        return None


def update_claim_row(claim_id: Optional[int], **fields: Any) -> None:
    """Best-effort claim row update; never blocks the money flow."""
    if claim_id is None:
        return
    try:
        with get_db() as session:
            claim = session.get(EarlyRakebackClaim, int(claim_id))
            if claim is None:
                return
            for key, value in fields.items():
                setattr(claim, key, value)
    except Exception:
        logger.exception("earlyrb_auto: could not update claim row id=%s", claim_id)


def reset_cashout_timer(club_id: int, chat_id: int, user_id: Optional[int]) -> None:
    """Record the claim as a deposit, which resets the 24h cashout timer.

    Only called once the claim has actually stuck — chips added, or chips failed
    but the Elevate rollback also failed, so the ledger still holds the money.
    A below-minimum quote, a technical failure, or a rolled-back chip-add never
    costs the player a cashout window.
    """
    try:
        record_activity_for_chat(
            int(club_id),
            int(chat_id),
            "deposit",
            telegram_user_id=int(user_id) if user_id is not None else None,
        )
    except Exception:
        logger.exception(
            "earlyrb_auto: could not record deposit activity chat_id=%s", chat_id
        )
    try:
        invalidate_pending_one_time_bypasses(int(club_id), int(chat_id))
    except Exception:
        logger.exception(
            "earlyrb_auto: could not invalidate one-time bypasses chat_id=%s", chat_id
        )


async def _record_with_retries(
    *,
    target: ClubTarget,
    gg_player_id: str,
    fee: Any,
    expected_amount: Decimal,
    idempotency_key: str,
    nickname: Optional[str],
) -> Any:
    """Record on Elevate, retrying only outcomes that are safe to repeat.

    ``record_in_progress`` and transport failures may both mean the record
    landed, so the retry reuses the same key: Elevate replies ``already_recorded``
    rather than paying twice.
    """
    pl = fee.pnl_filtered if fee.pnl_filtered is not None else Decimal("0")
    result = None
    for attempt, delay in enumerate((0.0, *_RECORD_RETRY_DELAYS_SEC)):
        if delay:
            await asyncio.sleep(delay)
        result = await elevate.record_early_rakeback(
            club_slug=target.elevate_slug,
            gg_player_id=gg_player_id,
            rake=fee.rake_filtered,
            pl=pl,
            expected_amount=expected_amount,
            idempotency_key=idempotency_key,
            nickname=nickname,
        )
        if result.ok or result.code not in (
            elevate.CODE_RECORD_IN_PROGRESS,
            "request_failed",
        ):
            return result
        logger.warning(
            "earlyrb_auto: record retry %s after %s key=%s",
            attempt + 1,
            result.code,
            idempotency_key,
        )
    return result


async def _delete_with_retries(
    *,
    target: ClubTarget,
    idempotency_key: str,
    record_id: Optional[str],
) -> Any:
    """Undo an Elevate record. Safe to retry: ``already_deleted`` is a success."""
    result = None
    for attempt, delay in enumerate((0.0, *_RECORD_RETRY_DELAYS_SEC)):
        if delay:
            await asyncio.sleep(delay)
        result = await elevate.delete_early_rakeback(
            club_slug=target.elevate_slug,
            idempotency_key=idempotency_key,
            record_id=record_id,
        )
        if result.ok or result.code not in ("request_failed",):
            return result
        logger.warning(
            "earlyrb_auto: delete retry %s after %s key=%s",
            attempt + 1,
            result.code,
            idempotency_key,
        )
    return result


@dataclass(frozen=True)
class ClaimStage:
    """Outcome of a Claim press.

    ``kind`` is added | chips_failed | amount_changed | escalate.
    """

    kind: str
    amount: Optional[Decimal] = None
    quote: Any = None
    detail: str = ""
    player_message: str = ""


async def claim_feeback(
    *,
    club_id: int,
    chat_id: int,
    user_id: Optional[int],
    group_title: Optional[str],
    gg_player_id: str,
    nickname: Optional[str],
    target: ClubTarget,
    fee: Any,
    quote: Any,
    idempotency_key: str,
    claim_id: Optional[int],
    allow_requote: bool = True,
) -> ClaimStage:
    """Record the feeback on Elevate, then add the chips. Never raises."""
    decimals = quote.display_decimal_places

    if deposit_api_dry_run():
        # Recording for real while no chips can move would leave a ledger entry
        # we then have to roll back, so a dry-run rollout stops before Elevate.
        amount_str = format_feeback_amount(quote.remaining, decimals)
        update_claim_row(
            claim_id, status="chips_added", chip_add_status="dry_run", detail="dry run"
        )
        await notify_earlyrb_auto_failed(
            club_id=club_id,
            chat_id=chat_id,
            title=group_title,
            detail=(
                f"DRY RUN: would have recorded {amount_str} of early feeback for "
                f"{target.clubgg_club} player {gg_player_id} and added the chips. "
                f"Nothing was written to Elevate."
            ),
        )
        return ClaimStage(
            "added",
            quote.remaining,
            quote,
            player_message=f"{amount_str} feeback added to your account!",
        )

    record = await _record_with_retries(
        target=target,
        gg_player_id=gg_player_id,
        fee=fee,
        expected_amount=quote.remaining,
        idempotency_key=idempotency_key,
        nickname=nickname,
    )

    if not record.ok:
        if record.code == elevate.CODE_AMOUNT_CHANGED and allow_requote:
            fresh = record.quote
            if fresh is not None:
                update_claim_row(
                    claim_id,
                    status="quoted",
                    quoted_amount=fresh.remaining,
                    detail="amount changed; re-quoted",
                )
                return ClaimStage("amount_changed", fresh.remaining, fresh)
        detail = f"Elevate record failed ({record.code}): {record.detail}"
        update_claim_row(claim_id, status="escalated", detail=detail)
        await notify_earlyrb_auto_failed(
            club_id=club_id, chat_id=chat_id, title=group_title, detail=detail
        )
        return ClaimStage("escalate", detail=detail, player_message=ADMIN_SHORTLY_COPY)

    amount = record.amount_recorded or quote.remaining
    update_claim_row(
        claim_id,
        status="recorded",
        recorded_amount=amount,
        elevate_record_id=record.record_id,
        elevate_entry_id=record.entry_id,
        elevate_total_given=record.total_given,
        detail="duplicate replay" if record.duplicate else None,
    )

    add_request_id = f"earlyrb-add-{claim_id or idempotency_key}"
    ok, status = await run_auto_chip_add(
        club_id=club_id,
        chat_id=chat_id,
        amount=amount,
        request_id=add_request_id,
        group_title=group_title,
        union_shorthand=target.union_shorthand,
        label=LABEL_FEEBACK,
    )
    amount_str = format_feeback_amount(amount, decimals)

    if ok:
        reset_cashout_timer(club_id, chat_id, user_id)
        update_claim_row(
            claim_id,
            status="chips_added",
            chip_add_status=status,
            rpa_add_request_id=add_request_id,
        )
        if get_escalate_auto_early_rakeback(int(club_id)):
            await notify_earlyrb_auto_added(
                club_id=club_id,
                chat_id=chat_id,
                title=group_title,
                gg_player_id=gg_player_id,
                amount=amount,
                clubgg_club=target.clubgg_club,
                rake=fee.rake_filtered,
                pl=fee.pnl_filtered,
            )
        return ClaimStage(
            "added",
            amount,
            quote,
            player_message=f"{amount_str} feeback added to your account!",
        )

    rollback = await _delete_with_retries(
        target=target,
        idempotency_key=idempotency_key,
        record_id=record.record_id,
    )
    if rollback.ok:
        update_claim_row(
            claim_id,
            status="rolled_back",
            chip_add_status=status,
            rpa_add_request_id=add_request_id,
            detail=f"chip add {status}; Elevate record rolled back ({rollback.code})",
        )
        await notify_earlyrb_auto_failed(
            club_id=club_id,
            chat_id=chat_id,
            title=group_title,
            detail=(
                f"Chip-add {status} for {amount_str} of early feeback on "
                f"{target.clubgg_club} player {gg_player_id}. Elevate record was "
                f"rolled back ({rollback.code}) — player can claim again."
            ),
        )
        return ClaimStage(
            "chips_failed",
            amount,
            quote,
            detail=f"chip add {status}; rolled back ({rollback.code})",
            player_message=ADMIN_SHORTLY_COPY,
        )

    reset_cashout_timer(club_id, chat_id, user_id)
    update_claim_row(
        claim_id,
        status="chips_failed",
        chip_add_status=status,
        rpa_add_request_id=add_request_id,
        detail=f"chip add {status}; Elevate rollback failed ({rollback.code})",
    )
    await notify_earlyrb_chips_not_added(
        club_id=club_id,
        chat_id=chat_id,
        title=group_title,
        gg_player_id=gg_player_id,
        amount=amount,
        clubgg_club=target.clubgg_club,
        detail=(
            f"Chip-add result: {status}. Elevate rollback failed "
            f"({rollback.code}): {rollback.detail}."
        ),
    )
    return ClaimStage(
        "chips_failed",
        amount,
        quote,
        detail=f"chip add {status}; rollback {rollback.code}",
        player_message=ADMIN_SHORTLY_COPY,
    )


async def escalate_fee_stage(
    *,
    club_id: int,
    chat_id: int,
    group_title: Optional[str],
    detail: str,
) -> None:
    """Slack for a pre-quote failure; nothing has been recorded."""
    await notify_earlyrb_auto_failed(
        club_id=club_id, chat_id=chat_id, title=group_title, detail=detail
    )


async def escalate_over_max(
    *,
    club_id: int,
    chat_id: int,
    group_title: Optional[str],
    gg_player_id: str,
    quote: Any,
    fee: Any,
) -> None:
    """Slack when the quote clears Elevate but exceeds our auto-claim limit."""
    max_amount = await asyncio.to_thread(
        get_early_rakeback_max_auto_amount, int(club_id)
    )
    await notify_earlyrb_auto_over_max(
        club_id=club_id,
        chat_id=chat_id,
        title=group_title,
        gg_player_id=gg_player_id,
        remaining=quote.remaining,
        max_amount=max_amount,
        rake=fee.rake_filtered,
        pl=fee.pnl_filtered,
    )
