"""Tests for hourly RT/CC group-photo backfill helpers."""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.group_photo_backfill import (
    PhotoBackfillCandidate,
    backfill_one_group,
    classify_group_photo_action,
    exclude_processed,
    match_idle_support_groups,
    setup_group_photo_backfill_job,
    tick_async,
)


def _cand(
    row_id: int,
    club_key: str,
    chat_id: int,
    title: str = "RT / 1 / x",
) -> PhotoBackfillCandidate:
    return PhotoBackfillCandidate(
        row_id=row_id,
        club_key=club_key,
        telegram_chat_id=chat_id,
        title=title,
    )


class TestClassifyGroupPhotoAction(unittest.TestCase):
    def test_not_in_group(self) -> None:
        self.assertEqual(
            classify_group_photo_action(entity_found=False, has_photo=False),
            "admin_not_in_group",
        )

    def test_already_has_photo(self) -> None:
        self.assertEqual(
            classify_group_photo_action(entity_found=True, has_photo=True),
            "already_has_photo",
        )

    def test_needs_photo(self) -> None:
        self.assertEqual(
            classify_group_photo_action(entity_found=True, has_photo=False),
            "needs_photo",
        )


class TestMatchIdleSupportGroups(unittest.TestCase):
    def test_joins_idle_to_rt_cc_and_skips_gto(self) -> None:
        matched = match_idle_support_groups(
            idle_chat_ids=[-100111, -222],
            sgc_rows=[
                _cand(1, "round_table", -100111),
                _cand(2, "creator_club", -100222, "CC / 1 / y"),
                _cand(3, "clubgto", -100111, "GTO / 1 / z"),
                _cand(4, "round_table", -100999),
            ],
        )
        ids = {(r.club_key, r.telegram_chat_id) for r in matched}
        self.assertEqual(
            ids,
            {("round_table", -100111), ("creator_club", -100222)},
        )

    def test_matches_chat_id_variants(self) -> None:
        matched = match_idle_support_groups(
            idle_chat_ids=[-1234567890],
            sgc_rows=[_cand(1, "round_table", -1001234567890)],
        )
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].telegram_chat_id, -1001234567890)

    def test_dedupes_same_chat_variants(self) -> None:
        matched = match_idle_support_groups(
            idle_chat_ids=[-1001234567890, -1234567890],
            sgc_rows=[
                _cand(1, "round_table", -1001234567890),
                _cand(2, "round_table", -1234567890),
            ],
        )
        self.assertEqual(len(matched), 1)


class TestExcludeProcessed(unittest.TestCase):
    def test_drops_processed_variant(self) -> None:
        remaining = exclude_processed(
            [_cand(1, "round_table", -1001234567890), _cand(2, "creator_club", -1002)],
            processed=[("round_table", -1234567890)],
        )
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].club_key, "creator_club")


class TestBackfillOneGroup(unittest.IsolatedAsyncioTestCase):
    async def test_skips_existing_photo_and_updates_db_path(self) -> None:
        client = MagicMock()
        cfg = MagicMock()
        cfg.group_photo_path = "assets/group_photos/round_table.jpg"
        entity = MagicMock()
        cand = _cand(9, "round_table", -1001)

        with (
            patch(
                "bot.services.group_photo_backfill.resolve_group_entity",
                new_callable=AsyncMock,
                return_value=entity,
            ),
            patch(
                "bot.services.mtproto_group_create.entity_has_group_photo",
                return_value=True,
            ),
            patch(
                "bot.services.support_group_chats.update_support_group_chat_row",
                return_value=(True, None),
            ) as upd,
            patch(
                "bot.services.mtproto_group_create._apply_group_photo_entity",
                new_callable=AsyncMock,
            ) as apply_photo,
        ):
            status = await backfill_one_group(client, cfg, cand)

        self.assertEqual(status, "already_has_photo")
        upd.assert_called_once_with(9, group_photo_path=cfg.group_photo_path)
        apply_photo.assert_not_awaited()

    async def test_applies_when_missing(self) -> None:
        client = MagicMock()
        cfg = MagicMock()
        cfg.group_photo_path = "assets/group_photos/creator_club.jpg"
        entity = MagicMock()
        cand = _cand(4, "creator_club", -1004)
        photo_abs = MagicMock()
        photo_abs.exists.return_value = True

        with (
            patch(
                "bot.services.group_photo_backfill.resolve_group_entity",
                new_callable=AsyncMock,
                return_value=entity,
            ),
            patch(
                "bot.services.mtproto_group_create.entity_has_group_photo",
                return_value=False,
            ),
            patch(
                "bot.services.mtproto_group_create.resolve_repo_path",
                return_value=photo_abs,
            ),
            patch(
                "bot.services.mtproto_group_create._with_single_flood_retry",
                new_callable=AsyncMock,
            ) as flood,
            patch(
                "bot.services.support_group_chats.update_support_group_chat_row",
                return_value=(True, None),
            ) as upd,
        ):
            status = await backfill_one_group(client, cfg, cand)

        self.assertEqual(status, "applied")
        flood.assert_awaited_once()
        upd.assert_called_once_with(4, group_photo_path=cfg.group_photo_path)

    async def test_admin_not_in_group(self) -> None:
        client = MagicMock()
        cfg = MagicMock()
        cand = _cand(1, "round_table", -1001)
        with patch(
            "bot.services.group_photo_backfill.resolve_group_entity",
            new_callable=AsyncMock,
            return_value=None,
        ):
            status = await backfill_one_group(client, cfg, cand)
        self.assertEqual(status, "admin_not_in_group")


class TestTickAsync(unittest.IsolatedAsyncioTestCase):
    async def test_caps_at_batch_and_uses_listener_client(self) -> None:
        rows = [_cand(i, "round_table", -1000 - i) for i in range(2)]
        client = MagicMock()
        client.is_connected.return_value = True
        cfg = MagicMock()
        cfg.group_photo_path = "assets/group_photos/round_table.jpg"

        with (
            patch(
                "bot.services.group_photo_backfill.is_group_photo_backfill_enabled",
                return_value=True,
            ),
            patch(
                "bot.services.group_photo_backfill.get_group_photo_backfill_batch_size",
                return_value=10,
            ),
            patch(
                "bot.services.group_photo_backfill.get_group_photo_backfill_chat_id",
                return_value=None,
            ),
            patch(
                "bot.services.group_photo_backfill.get_group_photo_backfill_delay_sec",
                return_value=0.0,
            ),
            patch(
                "bot.services.group_photo_backfill.load_photo_backfill_batch",
                return_value=rows,
            ) as load,
            patch(
                "bot.services.mtproto_dm_gc_listener.get_listener_client",
                return_value=client,
            ) as get_client,
            patch(
                "bot.services.group_photo_backfill.CLUB_GC_CONFIG",
                {"round_table": cfg},
            ),
            patch(
                "bot.services.group_photo_backfill.backfill_one_group",
                new_callable=AsyncMock,
                return_value="applied",
            ) as one,
            patch(
                "bot.services.group_photo_backfill.record_photo_backfill_outcome",
            ) as record,
        ):
            summary = await tick_async(limit=2, delay_seconds=0)

        load.assert_called_once_with(limit=2, chat_id=None)
        get_client.assert_called_with("round_table")
        self.assertEqual(one.await_count, 2)
        self.assertEqual(summary["applied"], 2)
        self.assertEqual(record.call_count, 2)

    async def test_listener_down_does_not_record(self) -> None:
        rows = [_cand(1, "round_table", -1001)]
        with (
            patch(
                "bot.services.group_photo_backfill.is_group_photo_backfill_enabled",
                return_value=True,
            ),
            patch(
                "bot.services.group_photo_backfill.get_group_photo_backfill_chat_id",
                return_value=None,
            ),
            patch(
                "bot.services.group_photo_backfill.load_photo_backfill_batch",
                return_value=rows,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.get_listener_client",
                return_value=None,
            ),
            patch(
                "bot.services.group_photo_backfill.CLUB_GC_CONFIG",
                {"round_table": MagicMock()},
            ),
            patch(
                "bot.services.group_photo_backfill.record_photo_backfill_outcome",
            ) as record,
        ):
            summary = await tick_async(limit=10, delay_seconds=0)

        record.assert_not_called()
        self.assertEqual(summary["skipped_listener_down"], 1)
        self.assertEqual(summary["applied"], 0)


class TestSetupJob(unittest.TestCase):
    def test_schedules_hourly_when_enabled(self) -> None:
        app = MagicMock()
        with (
            patch(
                "club_gc_settings.is_dm_gc_listener_enabled",
                return_value=True,
            ),
            patch(
                "bot.services.group_photo_backfill.is_group_photo_backfill_enabled",
                return_value=True,
            ),
            patch(
                "bot.services.group_photo_backfill.get_group_photo_backfill_interval_sec",
                return_value=3600,
            ),
            patch(
                "bot.services.group_photo_backfill.get_group_photo_backfill_first_delay_sec",
                return_value=300.0,
            ),
            patch(
                "bot.services.group_photo_backfill.get_group_photo_backfill_batch_size",
                return_value=10,
            ),
            patch(
                "bot.services.group_photo_backfill.remove_group_photo_backfill_job",
            ),
        ):
            setup_group_photo_backfill_job(app)

        kwargs = app.job_queue.run_repeating.call_args.kwargs
        self.assertEqual(kwargs["name"], "group_photo_backfill")
        self.assertEqual(kwargs["interval"], timedelta(seconds=3600))
        self.assertEqual(kwargs["first"], timedelta(seconds=300.0))

    def test_skips_when_disabled(self) -> None:
        app = MagicMock()
        with (
            patch(
                "club_gc_settings.is_dm_gc_listener_enabled",
                return_value=True,
            ),
            patch(
                "bot.services.group_photo_backfill.is_group_photo_backfill_enabled",
                return_value=False,
            ),
        ):
            setup_group_photo_backfill_job(app)
        app.job_queue.run_repeating.assert_not_called()


if __name__ == "__main__":
    unittest.main()
