"""Reference tools: list_clubs, get_club, list_club_groups, find_group,
list_payment_methods against an in-memory SQLite database."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import JSON

import mcp_server.db as mcp_db
from db.models import (
    Club,
    ClubPaymentMethod,
    ClubPaymentSubOption,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    Group,
    PlayerDetails,
    SupportGroupChat,
)
from mcp_server.tools.reference import (
    find_group,
    get_club,
    list_club_groups,
    list_clubs,
    list_payment_methods,
)
from tests.support.sqlite_db import make_sqlite_session_factory

TABLES = [
    "clubs",
    "groups",
    "player_details",
    "support_group_chats",
    "club_linked_accounts",
    "custom_commands",
    "club_payment_methods",
    "club_payment_tiers",
    "club_payment_tier_variants",
    "club_payment_sub_options",
    "plaid_items",
]

GTO_CHAT = -1001234567001
GTO_CHAT_2 = -1001234567002
RT_CHAT = -1001234567003
GTO_TITLE = "GTO / 8190-5287 / ThePirate343"
GTO_TITLE_2 = "GTO / 1111-2222 / Second Player"
RT_TITLE = "RT / 3333-4444 / Round Guy"


class ReferenceToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(
            os.environ,
            {"MCP_API_TOKENS": "dev:" + "x" * 32, "MCP_ACTOR": "tester"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        # The SQLite helper swaps ARRAY for JSON in the DDL only; the ORM still
        # binds through the mapped column, so give it the same JSON type while
        # these tests run (restored afterwards).
        chat_ids_type = patch.object(PlayerDetails.__table__.c.chat_ids, "type", JSON())
        chat_ids_type.start()
        self.addCleanup(chat_ids_type.stop)
        self.factory = make_sqlite_session_factory(TABLES)
        mcp_db.session_factory = self.factory
        self.addCleanup(setattr, mcp_db, "session_factory", None)
        self._seed()

    def _seed(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.factory() as session:
            gto = Club(id=1, name="ClubGTO", telegram_user_id=1001)
            rt = Club(id=2, name="Round Table", telegram_user_id=1002)
            session.add_all([gto, rt])
            session.flush()
            session.add_all(
                [
                    Group(
                        chat_id=GTO_CHAT,
                        club_id=1,
                        name=GTO_TITLE,
                        added_at=now - timedelta(days=2),
                    ),
                    Group(
                        chat_id=GTO_CHAT_2,
                        club_id=1,
                        name=GTO_TITLE_2,
                        added_at=now - timedelta(days=1),
                    ),
                    Group(chat_id=RT_CHAT, club_id=2, name=RT_TITLE, added_at=now),
                    PlayerDetails(
                        club_id=1,
                        gg_player_id="8190-5287",
                        gg_nickname="Pirate",
                        chat_ids=[GTO_CHAT, -1009999],
                    ),
                    PlayerDetails(
                        club_id=2,
                        gg_player_id="5555-6666",
                        gg_nickname="Orphan",
                        chat_ids=[-1008888],
                    ),
                    SupportGroupChat(
                        club_key="clubgto",
                        club_display_name="ClubGTO",
                        player_telegram_user_id=777,
                        player_username="pirate343",
                        player_display_name="The Pirate",
                        telegram_chat_id=GTO_CHAT,
                        telegram_chat_title=GTO_TITLE,
                        invite_link="https://t.me/+secret",
                    ),
                ]
            )
            session.commit()

    # ── list_clubs / get_club ─────────────────────────────────────────────

    def test_list_clubs(self) -> None:
        out = list_clubs()
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["returned"], 2)
        self.assertFalse(out["truncated"])
        by_name = {item["name"]: item for item in out["items"]}
        gto = by_name["ClubGTO"]
        self.assertEqual(gto["ref"]["key"], "clubgto")
        self.assertEqual(gto["ref"]["slug"], "clubgto")
        self.assertEqual(gto["ref"]["shorthands"], ["GTO"])
        self.assertEqual(gto["group_count"], 2)
        rt = by_name["Round Table"]
        self.assertEqual(rt["ref"]["key"], "round_table")
        self.assertEqual(sorted(rt["ref"]["shorthands"]), ["AT", "RT"])
        for item in out["items"]:
            self.assertFalse(any(k.endswith("_file_id") for k in item))

    def test_get_club_by_id_key_and_slug(self) -> None:
        by_id = get_club("1")["item"]
        self.assertEqual(by_id["name"], "ClubGTO")
        self.assertEqual(by_id["ref"]["id"], 1)
        self.assertEqual(by_id["linked_accounts"], [])
        self.assertEqual(by_id["custom_commands"], [])
        self.assertFalse(any(k.endswith("_file_id") for k in by_id))

        by_key = get_club("clubgto")["item"]
        self.assertEqual(by_key["id"], 1)

        by_slug = get_club("round-table")["item"]
        self.assertEqual(by_slug["id"], 2)
        self.assertEqual(by_slug["name"], "Round Table")

        # aces-table resolves to Round Table; shorthands work too.
        self.assertEqual(get_club("aces-table")["item"]["id"], 2)
        self.assertEqual(get_club("GTO")["item"]["id"], 1)

    def test_get_club_bad_reference(self) -> None:
        with self.assertRaises(ToolError) as cm:
            get_club("nope")
        self.assertIn("unknown club", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            get_club("99")
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError):
            get_club("")

    # ── list_club_groups ──────────────────────────────────────────────────

    def test_list_club_groups_all(self) -> None:
        out = list_club_groups()
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["returned"], 3)
        # Newest first.
        self.assertEqual(out["items"][0]["chat_id"], RT_CHAT)
        self.assertEqual(out["items"][0]["club_name"], "Round Table")
        self.assertIn("first_deposit_claimed", out["items"][0])
        self.assertTrue(out["pii_masked"])

    def test_list_club_groups_q_filter(self) -> None:
        out = list_club_groups(q="pirate")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["items"][0]["name"], GTO_TITLE)
        self.assertEqual(list_club_groups(q="zzz")["total"], 0)

    def test_list_club_groups_club_filter_and_paging(self) -> None:
        out = list_club_groups(club="clubgto")
        self.assertEqual(out["total"], 2)
        self.assertTrue(all(i["club_id"] == 1 for i in out["items"]))
        paged = list_club_groups(club="1", limit=1, offset=0)
        self.assertEqual(paged["returned"], 1)
        self.assertTrue(paged["truncated"])
        self.assertEqual(paged["limit"], 1)
        with self.assertRaises(ToolError):
            list_club_groups(club="nope")

    # ── find_group ────────────────────────────────────────────────────────

    def test_find_group_by_gg_player_id(self) -> None:
        out = find_group("8190-5287")
        self.assertEqual(out["returned"], 1)
        item = out["items"][0]
        self.assertEqual(item["chat_id"], GTO_CHAT)
        self.assertEqual(item["group_title"], GTO_TITLE)
        self.assertEqual(item["club_name"], "ClubGTO")
        self.assertEqual(item["gg_player_id"], "8190-5287")
        self.assertEqual(item["gg_nickname"], "Pirate")
        self.assertEqual(item["player_chat_ids"], [GTO_CHAT, -1009999])
        support = item["support_chat"]
        self.assertEqual(support["player_telegram_user_id"], 777)
        self.assertFalse(support["is_internal"])
        # PII in the support chat is masked by default.
        self.assertEqual(support["player_username"], "@pi***")
        self.assertEqual(support["player_display_name"], "T***")
        self.assertEqual(out["notes"], [])

    def test_find_group_include_pii(self) -> None:
        item = find_group("8190-5287", include_pii=True)["items"][0]
        self.assertEqual(item["support_chat"]["player_username"], "pirate343")
        self.assertEqual(item["support_chat"]["player_display_name"], "The Pirate")

    def test_find_group_by_chat_id(self) -> None:
        out = find_group(str(GTO_CHAT_2))
        self.assertEqual(out["returned"], 1)
        item = out["items"][0]
        self.assertEqual(item["group_title"], GTO_TITLE_2)
        self.assertEqual(item["gg_player_id"], "1111-2222")
        self.assertIsNone(item["gg_nickname"])
        self.assertEqual(item["player_chat_ids"], [])
        self.assertIsNone(item["support_chat"])

    def test_find_group_by_title_fragment(self) -> None:
        out = find_group("pirate")
        self.assertEqual(out["returned"], 1)
        self.assertEqual(out["items"][0]["chat_id"], GTO_CHAT)
        out = find_group("GTO /")
        self.assertEqual(out["returned"], 2)
        out = find_group("GTO /", club="round-table")
        self.assertEqual(out["returned"], 0)

    def test_find_group_no_match_adds_note(self) -> None:
        out = find_group("nobody-here")
        self.assertEqual(out["returned"], 0)
        self.assertEqual(out["items"], [])
        self.assertEqual(len(out["notes"]), 1)
        self.assertIn("no matching group", out["notes"][0])

    def test_find_group_falls_back_to_player_details(self) -> None:
        out = find_group("5555-6666")
        self.assertEqual(out["returned"], 1)
        item = out["items"][0]
        self.assertIsNone(item["chat_id"])
        self.assertEqual(item["club_name"], "Round Table")
        self.assertEqual(item["gg_nickname"], "Orphan")
        self.assertEqual(item["player_chat_ids"], [-1008888])

    def test_find_group_requires_query(self) -> None:
        with self.assertRaises(ToolError):
            find_group("   ")

    # ── list_payment_methods ──────────────────────────────────────────────

    def _seed_payment_method(self) -> None:
        with self.factory() as session:
            method = ClubPaymentMethod(
                id=10,
                club_id=1,
                direction="deposit",
                name="Venmo",
                slug="venmo",
                sort_order=0,
            )
            inactive = ClubPaymentMethod(
                id=11,
                club_id=1,
                direction="cashout",
                name="Old",
                slug="old",
                is_active=False,
            )
            session.add_all([method, inactive])
            session.flush()
            tier = ClubPaymentTier(
                id=20, method_id=10, label="Any", response_file_id="f"
            )
            session.add(tier)
            session.flush()
            session.add(
                ClubPaymentTierVariant(
                    id=30,
                    method_id=10,
                    tier_id=20,
                    label="Main",
                    tag="@receiving-handle",
                    link="https://venmo.com/u/receiving",
                    response_file_id="file-xyz",
                )
            )
            session.add(
                ClubPaymentSubOption(
                    id=40, method_id=10, name="Net", slug="net", response_file_id="f2"
                )
            )
            session.commit()

    def test_list_payment_methods(self) -> None:
        self._seed_payment_method()
        release = MagicMock(name="release_expired_variant_pauses")
        with patch(
            "bot.services.variant_pause.release_expired_variant_pauses", release
        ):
            out = list_payment_methods("clubgto")
        release.assert_not_called()
        self.assertEqual(out["total"], 1)
        method = out["items"][0]
        self.assertEqual(method["slug"], "venmo")
        self.assertFalse(any(k.endswith("_file_id") for k in method))
        tier = method["tiers"][0]
        self.assertNotIn("response_file_id", tier)
        variant = tier["variants"][0]
        self.assertEqual(variant["id"], 30)
        self.assertEqual(variant["pause_state"], "active")
        self.assertIsNone(variant["paused_until"])
        self.assertNotIn("response_file_id", variant)
        # Receiving tag/link are PII keys: masked by default.
        self.assertEqual(variant["tag"], "@re***")
        self.assertEqual(variant["link"], "[redacted]")
        self.assertIn("pause_state: active|paused|disabled", out["notes"])

    def test_list_payment_methods_include_pii_and_inactive(self) -> None:
        self._seed_payment_method()
        out = list_payment_methods("1", include_pii=True, include_inactive=True)
        self.assertEqual(out["total"], 2)
        venmo = next(m for m in out["items"] if m["slug"] == "venmo")
        self.assertEqual(venmo["tiers"][0]["variants"][0]["tag"], "@receiving-handle")
        self.assertFalse(out["pii_masked"])

    def test_list_payment_methods_paused_state(self) -> None:
        self._seed_payment_method()
        with self.factory() as session:
            variant = session.get(ClubPaymentTierVariant, 30)
            variant.is_active = False
            variant.paused_until = datetime.now(timezone.utc) + timedelta(hours=10)
            session.commit()
        out = list_payment_methods("clubgto")
        variant = out["items"][0]["tiers"][0]["variants"][0]
        self.assertEqual(variant["pause_state"], "paused")
        self.assertIsNotNone(variant["paused_until"])

    def test_list_payment_methods_direction_filter_and_validation(self) -> None:
        self._seed_payment_method()
        self.assertEqual(
            list_payment_methods("clubgto", direction="cashout")["total"], 0
        )
        self.assertEqual(
            list_payment_methods("clubgto", direction="cashout", include_inactive=True)[
                "total"
            ],
            1,
        )
        with self.assertRaises(ToolError):
            list_payment_methods("clubgto", direction="sideways")
        with self.assertRaises(ToolError):
            list_payment_methods("nope")


if __name__ == "__main__":
    unittest.main()
