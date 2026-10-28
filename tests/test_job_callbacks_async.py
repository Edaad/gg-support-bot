"""PTB's JobQueue awaits job callbacks, so a plain ``def`` raises TypeError every tick."""

from __future__ import annotations

import asyncio
import inspect
import unittest
from unittest.mock import MagicMock, patch

from bot.services import cashout_send_notify, contact_restore, group_photo_backfill
from bot.services import mtproto_dm_gc_listener


class JobCallbacksAreCoroutinesTests(unittest.TestCase):
    def test_repeating_listener_callbacks_are_async(self):
        for callback in (
            cashout_send_notify.cashout_send_notify_job_callback,
            contact_restore.contact_restore_job_callback,
            group_photo_backfill.group_photo_backfill_job_callback,
        ):
            with self.subTest(callback=callback.__name__):
                self.assertTrue(inspect.iscoroutinefunction(callback))


class CashoutSendNotifyJobCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_early_without_listener_loop(self):
        with (
            patch.dict(mtproto_dm_gc_listener._loop_holder, {}, clear=True),
            patch.object(
                cashout_send_notify.asyncio, "run_coroutine_threadsafe"
            ) as submit,
        ):
            await cashout_send_notify.cashout_send_notify_job_callback(MagicMock())
        submit.assert_not_called()

    async def test_hands_tick_to_listener_loop(self):
        loop = asyncio.get_running_loop()
        tick_ran = asyncio.Event()

        async def fake_tick():
            tick_ran.set()
            return {}

        with (
            patch.dict(mtproto_dm_gc_listener._loop_holder, {"loop": loop}),
            patch.object(cashout_send_notify, "tick_async", fake_tick),
        ):
            await cashout_send_notify.cashout_send_notify_job_callback(MagicMock())
            await asyncio.wait_for(tick_ran.wait(), timeout=1)


if __name__ == "__main__":
    unittest.main()
