"""Tests for issue report draft context extraction."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from bot.services.issue_report_drafts import DraftContext, draft_to_context


class TestDraftToContext(unittest.TestCase):
    def test_copies_fields_from_orm_object(self) -> None:
        draft = MagicMock()
        draft.id = 7
        draft.club_id = 3
        draft.group_title = "RT AT / 3333-3333 / @jz034"
        draft.telegram_chat_id = -100123

        ctx = draft_to_context(draft)
        self.assertEqual(
            ctx,
            DraftContext(
                id=7,
                club_id=3,
                group_title="RT AT / 3333-3333 / @jz034",
                telegram_chat_id=-100123,
            ),
        )

        # Simulate detached session: ORM access would fail; context is safe.
        draft.id = property(lambda self: (_ for _ in ()).throw(RuntimeError("detached")))
        self.assertEqual(ctx.id, 7)


if __name__ == "__main__":
    unittest.main()
