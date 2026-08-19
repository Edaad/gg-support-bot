"""Club lookup for newly migrated /gc chats (basic vs -100… ids)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.services.club import get_club_for_chat, is_group_linked
from db.models import Base, Club, Group


class GetClubForChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[Club.__table__, Group.__table__],
        )
        self.Session = sessionmaker(bind=self.engine)
        session = self.Session()
        session.add(Club(id=2, name="Round Table", telegram_user_id=1001, is_active=True))
        session.add(Group(chat_id=-5287778428, club_id=2, name="RT / 1 / Test"))
        session.commit()
        session.close()

    def _patch_db(self):
        session = self.Session()
        ctx = MagicMock()
        ctx.__enter__.return_value = session
        ctx.__exit__.return_value = False
        return patch("bot.services.club.get_db", return_value=ctx), session

    def test_exact_basic_id(self) -> None:
        db_patch, session = self._patch_db()
        with db_patch:
            self.assertEqual(get_club_for_chat(-5287778428), 2)
        session.close()

    def test_supergroup_variant_of_stored_basic_id(self) -> None:
        db_patch, session = self._patch_db()
        with db_patch:
            self.assertEqual(get_club_for_chat(-1005287778428), 2)
            self.assertTrue(is_group_linked(-1005287778428))
        session.close()

    def test_unknown_chat_not_linked(self) -> None:
        db_patch, session = self._patch_db()
        with db_patch:
            self.assertFalse(is_group_linked(-1001111111111))
        session.close()

    def test_support_group_chat_fallback(self) -> None:
        sgc = MagicMock()
        sgc.club_key = "round_table"
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None
        session.query.return_value.filter.return_value.first.return_value = None
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            sgc
        )
        ctx = MagicMock()
        ctx.__enter__.return_value = session
        ctx.__exit__.return_value = False
        cfg = MagicMock(link_club_id=2)
        with (
            patch("bot.services.club.get_db", return_value=ctx),
            patch(
                "club_gc_settings.get_mtproto_session_config",
                return_value=cfg,
            ),
        ):
            self.assertEqual(get_club_for_chat(-1003959356975), 2)
