"""Move chips between the two unions of one club (the ``/transfer`` command).

A transfer is two independent ClubGG RPA operations with no transaction around
them: claim from the source union, then add to the destination union. Claiming
first is deliberate — a player asking to move more chips than they hold fails the
claim leg, so nothing has been added and nothing is lost.

The dangerous outcome is a successful claim followed by a failed add: the chips
have left the source and are not in the destination. That is never auto-reversed
(the add may silently have landed); it escalates with the amount and both clubs so
an AM can finish it by hand.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from bot.services.club import get_auto_claim_enabled, get_club_by_id
from bot.services.clubgg_deposit_api import (
    load_config,
    resolve_clubgg_club_name,
    run_auto_chip_add,
    run_auto_claim,
)
from bot.services.player_details import gg_player_id_from_title
from bot.services.round_table_unions import deposit_unions_for_club

logger = logging.getLogger(__name__)

# Legs, in the order they run.
LEG_PRECHECK = "precheck"
LEG_CLAIM = "claim"
LEG_ADD = "add"


@dataclass(frozen=True)
class TransferPlan:
    """A resolved, ready-to-run transfer between two unions of one club."""

    club_id: int
    chat_id: int
    source_shorthand: str
    destination_shorthand: str
    source_label: str
    destination_label: str
    source_clubgg: str
    destination_clubgg: str


@dataclass(frozen=True)
class TransferResult:
    """Outcome of one transfer attempt. Never raises; always returned."""

    ok: bool
    failed_leg: Optional[str] = None
    status: str = ""
    reason: str = ""
    # Set once chips have left the source club. When this is not None and ok is
    # False, the chips are in limbo and a human must finish the move.
    claimed_amount: Optional[Decimal] = None

    @property
    def chips_in_limbo(self) -> bool:
        return not self.ok and self.claimed_amount is not None


def build_transfer_plan(
    *,
    club_id: int,
    chat_id: int,
    destination_shorthand: str,
) -> Optional[TransferPlan]:
    """Resolve source/destination for a chosen destination union.

    The club's union pair drives everything, so the four supported moves fall out
    of the data: Round Table RT<->AT and Creator Club CC<->AT. Returns None when
    the club has no unions or the destination is not one of them.
    """
    unions = deposit_unions_for_club(int(club_id))
    if not unions or len(unions) != 2:
        return None

    dest = (destination_shorthand or "").strip().upper()
    by_shorthand = {u["shorthand"]: u for u in unions}
    if dest not in by_shorthand:
        return None
    src = next(s for s in by_shorthand if s != dest)

    club = get_club_by_id(int(club_id))
    club_name = club.name if club else None
    source_clubgg = resolve_clubgg_club_name(club_name, src)
    destination_clubgg = resolve_clubgg_club_name(club_name, dest)
    if not source_clubgg or not destination_clubgg:
        logger.warning(
            "transfer: could not map club=%r unions %s->%s to ClubGG names",
            club_name,
            src,
            dest,
        )
        return None

    return TransferPlan(
        club_id=int(club_id),
        chat_id=int(chat_id),
        source_shorthand=src,
        destination_shorthand=dest,
        source_label=by_shorthand[src]["label"],
        destination_label=by_shorthand[dest]["label"],
        source_clubgg=source_clubgg,
        destination_clubgg=destination_clubgg,
    )


def transfer_blocked_reason(club_id: int, group_title: str | None) -> Optional[str]:
    """Why this group cannot run a transfer right now, or None when it can.

    Checked before the player is asked for anything, so no chips move on a setup
    problem we could have seen coming.
    """
    if load_config() is None:
        return "deposit API is not configured"
    if not get_auto_claim_enabled(int(club_id)):
        return "auto claim is disabled for this club"
    if not gg_player_id_from_title(group_title):
        return "group title has no readable GG player id"
    return None


def new_transfer_key(chat_id: int) -> str:
    """One key per transfer; each leg derives a stable request id from it."""
    return f"{int(chat_id)}-{uuid.uuid4().hex[:12]}"


async def run_transfer(
    *,
    plan: TransferPlan,
    amount: Decimal,
    transfer_key: str,
    group_title: str | None = None,
    on_claimed=None,
) -> TransferResult:
    """Claim from the source union, then add to the destination union.

    ``on_claimed`` is awaited after a successful claim, so the caller can tell the
    player chips are being added before the second (slow) leg starts.
    """
    claim_request_id = f"transfer-claim-{transfer_key}"
    add_request_id = f"transfer-add-{transfer_key}"

    try:
        claim = await run_auto_claim(
            club_id=plan.club_id,
            chat_id=plan.chat_id,
            job_id=0,
            amount=amount,
            group_title=group_title,
            union_shorthand=plan.source_shorthand,
            request_id=claim_request_id,
        )
    except Exception:
        logger.exception(
            "transfer: claim crashed chat_id=%s %s->%s amount=%s",
            plan.chat_id,
            plan.source_shorthand,
            plan.destination_shorthand,
            amount,
        )
        return TransferResult(
            ok=False,
            failed_leg=LEG_CLAIM,
            status="error",
            reason="Claim crashed unexpectedly.",
        )

    if not claim.ok:
        # Includes "uncertain": the claim may have gone through, so the add must
        # not run and the claim must not be retried.
        uncertain = claim.status == "uncertain"
        reason = claim.reason or "no detail"
        return TransferResult(
            ok=False,
            failed_leg=LEG_CLAIM,
            status=claim.status,
            reason=(
                f"Claim from {plan.source_clubgg} UNCERTAIN: {reason} — may have "
                f"claimed; verify on ClubGG and do not re-claim. Nothing was added "
                f"to {plan.destination_clubgg}."
                if uncertain
                else f"Claim from {plan.source_clubgg} {claim.status}: {reason}"
            ),
            claimed_amount=amount if uncertain else None,
        )

    if on_claimed is not None:
        try:
            await on_claimed()
        except Exception:
            logger.warning(
                "transfer: on_claimed callback failed chat_id=%s", plan.chat_id,
                exc_info=True,
            )

    try:
        add_ok, add_status = await run_auto_chip_add(
            club_id=plan.club_id,
            chat_id=plan.chat_id,
            amount=amount,
            request_id=add_request_id,
            group_title=group_title,
            union_shorthand=plan.destination_shorthand,
        )
    except Exception:
        logger.exception(
            "transfer: add crashed chat_id=%s %s->%s amount=%s",
            plan.chat_id,
            plan.source_shorthand,
            plan.destination_shorthand,
            amount,
        )
        return TransferResult(
            ok=False,
            failed_leg=LEG_ADD,
            status="error",
            reason="Add crashed unexpectedly after the chips were claimed.",
            claimed_amount=amount,
        )

    if not add_ok:
        detail = (
            f"Add to {plan.destination_clubgg} UNCERTAIN: it may have landed — "
            f"verify on ClubGG before adding again."
            if add_status == "uncertain"
            else f"Add to {plan.destination_clubgg} failed ({add_status})."
        )
        return TransferResult(
            ok=False,
            failed_leg=LEG_ADD,
            status=add_status,
            reason=detail,
            claimed_amount=amount,
        )

    logger.info(
        "transfer: SUCCESS chat_id=%s %s->%s amount=%s",
        plan.chat_id,
        plan.source_clubgg,
        plan.destination_clubgg,
        amount,
    )
    return TransferResult(ok=True, status="success", claimed_amount=amount)
