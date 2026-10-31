"""First-deposit bonus decision: announcement, cap, duplicates, and gap payments."""

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from bot.services.first_deposit_bonus import (
    BonusDecision,
    GroupView,
    PaymentView,
    bonus_amount,
    decide,
    format_bonus_message,
    payment_belongs,
)


def _group(
    chat_id: int, *, claimed: bool = False, link: str | None = None
) -> GroupView:
    return GroupView(
        chat_id=chat_id,
        title=f"RT / 1234-5678 / {chat_id}",
        claimed=claimed,
        link=link,
    )


class BonusDecisionTests(unittest.TestCase):
    def test_message_matches_agreed_copy(self):
        text = format_bonus_message(
            pct=25,
            bonus=Decimal("25.00"),
            amount=Decimal("100.00"),
        )
        self.assertEqual(
            text,
            "Congratulations on your first, you will get a "
            "25% first deposit bonus of $25.00 added to "
            "your deposit of $100.00 on us!",
        )

    def test_cap_limits_the_bonus(self):
        self.assertEqual(
            bonus_amount(Decimal("1000"), 25, Decimal("100")),
            Decimal("100.00"),
        )

    def test_zero_bonus_does_not_announce_or_mark(self):
        decision = decide(
            player_id="1234-5678",
            target=_group(1),
            groups=[_group(1)],
            payments=[
                PaymentView(
                    at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    amount=Decimal("0.01"),
                )
            ],
            bonus_enabled=True,
            pct=1,
            cap=None,
            dm_admin_id=9,
        )
        self.assertIsNone(decision.announcement)
        self.assertIsNone(decision.mark_chat_id)

    def test_earliest_payment_is_the_announcement_amount(self):
        decision = decide(
            player_id="1234-5678",
            target=_group(1),
            groups=[_group(1)],
            payments=[
                PaymentView(
                    at=datetime(2026, 10, 2, tzinfo=timezone.utc),
                    amount=Decimal("200"),
                ),
                PaymentView(
                    at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    amount=Decimal("100"),
                ),
            ],
            bonus_enabled=True,
            pct=25,
            cap=Decimal("100"),
            dm_admin_id=9,
        )
        self.assertIn("$25.00", decision.announcement or "")
        self.assertIn("$100.00", decision.announcement or "")
        self.assertEqual(decision.mark_chat_id, 1)

    def test_claimed_group_does_not_announce(self):
        decision = decide(
            player_id="1234-5678",
            target=_group(1, claimed=True),
            groups=[_group(1, claimed=True)],
            payments=[
                PaymentView(
                    at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    amount=Decimal("100"),
                )
            ],
            bonus_enabled=True,
            pct=25,
            cap=None,
            dm_admin_id=9,
        )
        self.assertIsNone(decision.announcement)

    def test_two_groups_dm_and_skip_the_bonus(self):
        groups = [
            _group(1, link="https://t.me/+invite"),
            _group(2, link="https://t.me/c/2"),
        ]
        decision = decide(
            player_id="1234-5678",
            target=groups[0],
            groups=groups,
            payments=[
                PaymentView(
                    at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    amount=Decimal("100"),
                )
            ],
            bonus_enabled=True,
            pct=25,
            cap=None,
            dm_admin_id=9,
        )
        self.assertIsNone(decision.announcement)
        self.assertEqual(decision.dm_admin_id, 9)
        self.assertIn(
            "Two support groups for 1234-5678. Run /delete confirm in one of them.",
            decision.dm_text or "",
        )
        self.assertIn("https://t.me/+invite", decision.dm_text or "")

    def test_bonus_off_skips_the_dm(self):
        groups = [_group(1), _group(2)]
        decision = decide(
            player_id="1234-5678",
            target=groups[0],
            groups=groups,
            payments=[],
            bonus_enabled=False,
            pct=25,
            cap=None,
            dm_admin_id=9,
        )
        self.assertEqual(decision, BonusDecision())

    def test_saved_title_counts_after_the_group_is_gone(self):
        self.assertTrue(
            payment_belongs(
                title="RT / 1234-5678 / Mayank",
                chat_id=-100,
                player_id="1234-5678",
                target_chat_id=-200,
            )
        )

    def test_gap_payment_counts_only_for_this_group(self):
        self.assertTrue(
            payment_belongs(
                title="RT / / Mayank",
                chat_id=-100,
                player_id="1234-5678",
                target_chat_id=-100,
            )
        )
        self.assertFalse(
            payment_belongs(
                title="RT / / Mayank",
                chat_id=-100,
                player_id="1234-5678",
                target_chat_id=-200,
            )
        )

    def test_other_player_title_does_not_count(self):
        self.assertFalse(
            payment_belongs(
                title="RT / 9999-0000 / Mayank",
                chat_id=-100,
                player_id="1234-5678",
                target_chat_id=-100,
            )
        )


if __name__ == "__main__":
    unittest.main()
