"""Discovery tools: clubs, groups, players and payment-method configuration."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy.orm import joinedload

from api.routes.clubs import _club_to_read
from api.schemas import GroupRead, LinkedAccountRead
from api.schemas_v2 import ClubPaymentMethodRead
from db.models import (
    ClubPaymentMethod,
    ClubPaymentTier,
    CustomCommand,
    Group,
    PlayerDetails,
    SupportGroupChat,
)
from mcp_server.db import read_session
from mcp_server.envelope import page, single
from mcp_server.errors import bad_input, translate_errors
from mcp_server.tools._common import (
    READ_ONLY,
    clamp_limit,
    clamp_offset,
    club_name_map,
    club_ref,
    resolve_club,
)

_FILE_ID_SUFFIX = "_file_id"


def _strip_file_ids(data: dict[str, Any]) -> dict[str, Any]:
    """Telegram file ids carry no meaning for a reader; drop them."""
    return {k: v for k, v in data.items() if not k.endswith(_FILE_ID_SUFFIX)}


@translate_errors
def list_clubs() -> dict[str, Any]:
    """List every club with its id, key, slug, title shorthands, feature flags and
    counts of payment methods, linked groups and backup staff accounts.
    Call this first to resolve club references used by other tools."""
    from db.models import Club

    with read_session() as session:
        clubs = session.query(Club).order_by(Club.id).all()
        items = []
        for club in clubs:
            data = _strip_file_ids(_club_to_read(club).model_dump())
            data["ref"] = club_ref(club)
            items.append(data)
    return page(items, total=len(items), limit=len(items) or 1, offset=0)


@translate_errors
def get_club(club: str) -> dict[str, Any]:
    """Full settings for one club (deposit/cashout flags, cooldowns, limits,
    welcome/list text) plus its backup staff accounts and custom command names.
    `club` accepts id, key (round_table|creator_club|clubgto) or slug."""
    with read_session() as session:
        row = resolve_club(session, club)
        if row is None:
            raise bad_input("club is required")
        data = _strip_file_ids(_club_to_read(row).model_dump())
        data["ref"] = club_ref(row)
        data["linked_accounts"] = [
            LinkedAccountRead.model_validate(a).model_dump()
            for a in row.linked_accounts
        ]
        commands = (
            session.query(CustomCommand)
            .filter(CustomCommand.club_id == row.id)
            .order_by(CustomCommand.command_name)
            .all()
        )
        data["custom_commands"] = [
            {
                "id": c.id,
                "command_name": c.command_name,
                "response_type": c.response_type,
                "is_active": getattr(c, "is_active", None),
                "customer_visible": getattr(c, "customer_visible", None),
            }
            for c in commands
        ]
    return single(data)


@translate_errors
def list_club_groups(
    club: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Linked Telegram support groups (one per player), newest first. `q` matches
    the group title (shorthand / gg_player_id / name). `club` accepts an id, key
    (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|
    aces-table); omit for all clubs."""
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        query = session.query(Group)
        club_row = resolve_club(session, club)
        if club_row is not None:
            query = query.filter(Group.club_id == club_row.id)
        if q and q.strip():
            query = query.filter(Group.name.ilike(f"%{q.strip()}%"))
        total = query.order_by(None).count()
        rows = (
            query.order_by(Group.added_at.desc().nullslast(), Group.chat_id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = club_name_map(session)
        items = []
        for g in rows:
            data = GroupRead.model_validate(g).model_dump()
            data["club_name"] = names.get(int(g.club_id))
            data["first_deposit_claimed"] = bool(g.first_deposit_claimed)
            data["last_deposit_union"] = g.last_deposit_union
            items.append(data)
    return page(items, total=total, limit=limit, offset=offset, include_pii=include_pii)


def _looks_like_gg_player_id(text: str) -> bool:
    digits = text.replace("-", "")
    return "-" in text and digits.isdigit() and 6 <= len(digits) <= 12


@translate_errors
def find_group(
    query: str,
    club: str | None = None,
    limit: int = 20,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Resolve a player: `query` may be a group title fragment ("GTO / 8190-5287",
    "ThePirate"), a gg_player_id ("8190-5287") or a numeric Telegram chat_id.
    Returns matching support groups with club, gg_player_id, nickname and the
    player's other chat ids. Use the returned chat_id / group title in other tools."""
    needle = (query or "").strip()
    if not needle:
        raise bad_input("query is required")
    limit = clamp_limit(limit)
    with read_session() as session:
        club_row = resolve_club(session, club)
        names = club_name_map(session)
        groups_q = session.query(Group)
        if club_row is not None:
            groups_q = groups_q.filter(Group.club_id == club_row.id)
        chat_id: int | None = None
        stripped = needle.lstrip("-")
        if stripped.isdigit() and len(stripped) >= 7:
            chat_id = int(needle)
            groups_q = groups_q.filter(Group.chat_id == chat_id)
        elif _looks_like_gg_player_id(needle):
            groups_q = groups_q.filter(
                Group.name.ilike(f"%/ {needle} /%") | Group.name.ilike(f"%/ {needle}")
            )
        else:
            groups_q = groups_q.filter(Group.name.ilike(f"%{needle}%"))
        groups = groups_q.order_by(Group.added_at.desc().nullslast()).limit(limit).all()

        items: list[dict[str, Any]] = []
        for g in groups:
            from bot.services.player_details import parse_group_title_parts

            parsed = parse_group_title_parts(g.name)
            gg_player_id = parsed.gg_player_id if parsed else None
            player: PlayerDetails | None = None
            if gg_player_id:
                player = (
                    session.query(PlayerDetails)
                    .filter(
                        PlayerDetails.club_id == g.club_id,
                        PlayerDetails.gg_player_id == gg_player_id,
                    )
                    .first()
                )
            support = (
                session.query(SupportGroupChat)
                .filter(SupportGroupChat.telegram_chat_id == g.chat_id)
                .order_by(SupportGroupChat.id.desc())
                .first()
            )
            items.append(
                {
                    "chat_id": int(g.chat_id),
                    "group_title": g.name,
                    "club_id": int(g.club_id),
                    "club_name": names.get(int(g.club_id)),
                    "gg_player_id": gg_player_id,
                    "gg_nickname": player.gg_nickname if player else None,
                    "player_chat_ids": list(player.chat_ids or []) if player else [],
                    "added_at": g.added_at,
                    "first_deposit_claimed": bool(g.first_deposit_claimed),
                    "last_deposit_union": g.last_deposit_union,
                    "support_chat": None
                    if support is None
                    else {
                        "player_telegram_user_id": support.player_telegram_user_id,
                        "player_username": support.player_username,
                        "player_display_name": support.player_display_name,
                        "is_internal": bool(support.is_internal),
                        "created_at": support.created_at,
                    },
                }
            )
        if not items and chat_id is None and _looks_like_gg_player_id(needle):
            # Fall back to player_details when the title does not carry the id.
            players = (
                session.query(PlayerDetails)
                .filter(PlayerDetails.gg_player_id == needle)
                .all()
            )
            for p in players:
                items.append(
                    {
                        "chat_id": None,
                        "group_title": None,
                        "club_id": int(p.club_id),
                        "club_name": names.get(int(p.club_id)),
                        "gg_player_id": p.gg_player_id,
                        "gg_nickname": p.gg_nickname,
                        "player_chat_ids": list(p.chat_ids or []),
                    }
                )
    notes = ["no matching group; consider list_club_groups with q"] if not items else []
    return page(
        items,
        total=len(items),
        limit=limit,
        offset=0,
        include_pii=include_pii,
        notes=notes,
    )


@translate_errors
def list_payment_methods(
    club: str,
    direction: str | None = None,
    include_inactive: bool = False,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Payment-method configuration for a club: methods -> tiers (amount bands) ->
    variants (receiving handles with weight and pause state) and crypto sub-options.
    `direction` is deposit or cashout. Does not alter pause timers (unlike the
    dashboard's GET). Receiving tags/links are masked unless include_pii."""
    from bot.services.variant_pause import card_state

    if direction is not None and direction not in ("deposit", "cashout"):
        raise bad_input("direction must be deposit or cashout")
    with read_session() as session:
        club_row = resolve_club(session, club)
        if club_row is None:
            raise bad_input("club is required")
        query = (
            session.query(ClubPaymentMethod)
            .options(
                joinedload(ClubPaymentMethod.tiers).joinedload(
                    ClubPaymentTier.variants
                ),
                joinedload(ClubPaymentMethod.sub_options),
            )
            .filter(ClubPaymentMethod.club_id == club_row.id)
        )
        if direction:
            query = query.filter(ClubPaymentMethod.direction == direction)
        if not include_inactive:
            query = query.filter(ClubPaymentMethod.is_active == True)  # noqa: E712
        methods = query.order_by(
            ClubPaymentMethod.sort_order, ClubPaymentMethod.id
        ).all()
        items = []
        for method in methods:
            data = _strip_file_ids(
                ClubPaymentMethodRead.model_validate(method).model_dump()
            )
            variant_by_id = {
                int(v.id): v for tier in method.tiers for v in tier.variants
            }
            for tier in data.get("tiers", []):
                tier.pop("response_file_id", None)
                for variant in tier.get("variants", []):
                    row = variant_by_id.get(int(variant["id"]))
                    variant.pop("response_file_id", None)
                    variant["pause_state"] = (
                        card_state(row) if row is not None else None
                    )
                    variant["paused_until"] = getattr(row, "paused_until", None)
            items.append(data)
    return page(
        items,
        total=len(items),
        limit=len(items) or 1,
        offset=0,
        include_pii=include_pii,
        notes=["amounts are dollars", "pause_state: active|paused|disabled"],
    )


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_clubs", annotations=READ_ONLY)(list_clubs)
    mcp.tool(name="get_club", annotations=READ_ONLY)(get_club)
    mcp.tool(name="list_club_groups", annotations=READ_ONLY)(list_club_groups)
    mcp.tool(name="find_group", annotations=READ_ONLY)(find_group)
    mcp.tool(name="list_payment_methods", annotations=READ_ONLY)(list_payment_methods)
