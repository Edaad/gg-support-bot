"""Club serialization for the dashboard Clubs page."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes.clubs import _club_to_read
from db.models import Base, Club, ClubLinkedAccount, ClubPaymentMethod, Group


class ClubReadTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            engine,
            tables=[
                Club.__table__,
                Group.__table__,
                ClubLinkedAccount.__table__,
                ClubPaymentMethod.__table__,
            ],
        )
        self.session = sessionmaker(bind=engine)()

    def tearDown(self) -> None:
        self.session.close()

    def test_method_count_uses_club_payment_methods(self) -> None:
        club = Club(name="RT", telegram_user_id=1)
        self.session.add(club)
        self.session.flush()
        for slug in ("crypto", "venmo"):
            self.session.add(
                ClubPaymentMethod(
                    club_id=club.id, direction="deposit", name=slug, slug=slug
                )
            )
        self.session.add(Group(chat_id=-100, club_id=club.id, name="RT / 1-2 / x"))
        self.session.commit()

        read = _club_to_read(club)

        self.assertEqual(read.method_count, 2)
        self.assertEqual(read.group_count, 1)
        self.assertEqual(read.linked_account_count, 0)


if __name__ == "__main__":
    unittest.main()
