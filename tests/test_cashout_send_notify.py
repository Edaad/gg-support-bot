"""Money-send proof + player notify queue, and owed-pin clearing."""

from __future__ import annotations

import contextlib
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_admin
from api.routes.cashout_records import router
from bot.services import cashout_send_notify as notify
from bot.services import staff_cashout_records as svc
from bot.services.mtproto_group_cash import format_cash_owed, format_sent_caption
from db.connection import get_db_dependency
from db.models import (
    StaffCashoutMoneySend,
    StaffCashoutPayment,
    StaffCashoutRecord,
    StaffCashoutSendProof,
)

PNG = b"\x89PNG\r\n\x1a\nfake"
CFG = SimpleNamespace(club_key="round_table")


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (
        StaffCashoutRecord,
        StaffCashoutPayment,
        StaffCashoutMoneySend,
        StaffCashoutSendProof,
    ):
        model.__table__.create(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class _DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.Session = _session_factory()

        @contextlib.contextmanager
        def fake_get_db():
            session = self.Session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        patches = [
            patch("bot.services.staff_cashout_records.get_db", fake_get_db),
            patch("db.connection.get_db", fake_get_db),
            patch(
                "bot.services.staff_cashout_records.get_club_gc_config_by_link_club_id",
                side_effect=lambda cid: CFG if int(cid) == 2 else None,
            ),
            patch(
                "bot.services.cashout_send_notify.get_club_gc_config_by_link_club_id",
                side_effect=lambda cid: CFG if int(cid) == 2 else None,
            ),
            patch(
                "bot.services.staff_cashout_records.get_method_by_id",
                side_effect=lambda mid: {
                    7: {"id": 7, "name": "Crypto", "slug": "crypto"},
                    8: {"id": 8, "name": "Venmo", "slug": "venmo"},
                }.get(int(mid)),
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _record(self, *, chat_id=-100123, club_id=2, amount="200") -> int:
        with self.Session() as session:
            record = StaffCashoutRecord(
                club_id=club_id,
                chat_id=chat_id,
                group_title="RT / 2427-3267 / Samin",
                amount=Decimal(amount),
                trigger="group_cash",
                tracks_money_sent=True,
            )
            session.add(record)
            session.commit()
            return int(record.id)

    def _get(self, model, pk):
        with self.Session() as session:
            return session.get(model, pk)


class AddSendTestCase(_DbTestCase):
    def test_notify_queued_with_screenshot_when_connected(self) -> None:
        rid = self._record()
        out = svc.add_staff_cashout_send(
            rid,
            {
                "sender_name": "Rtsupport",
                "amount": "100",
                "method_display_name": "Zelle",
                "notify_player": True,
                "proof": {
                    "content": PNG,
                    "content_type": "image/png",
                    "filename": "s.png",
                },
            },
        )
        self.assertTrue(out["chat_connected"])
        send = out["sends"][0]
        self.assertTrue(send["notify_player"])
        self.assertEqual(send["notify_status"], "pending")
        self.assertTrue(send["has_proof"])
        self.assertIsNone(out["owed_clear_status"])  # partial send leaves the pin
        proof = svc.get_staff_cashout_send_proof(rid, send["id"])
        self.assertEqual(proof["content"], PNG)

    def test_notify_ignored_when_not_connected(self) -> None:
        rid = self._record(chat_id=None)
        out = svc.add_staff_cashout_send(
            rid,
            {
                "sender_name": "Rtsupport",
                "amount": "50",
                "method_display_name": "Zelle",
                "notify_player": True,
            },
        )
        self.assertFalse(out["chat_connected"])
        self.assertFalse(out["sends"][0]["notify_player"])
        self.assertIsNone(out["sends"][0]["notify_status"])

    def test_notify_ignored_when_club_has_no_account(self) -> None:
        rid = self._record(club_id=3)
        out = svc.add_staff_cashout_send(
            rid,
            {
                "sender_name": "x",
                "amount": "50",
                "method_display_name": "Zelle",
                "notify_player": True,
            },
        )
        self.assertFalse(out["chat_connected"])
        self.assertIsNone(out["sends"][0]["notify_status"])

    def test_crypto_takes_link_not_screenshot(self) -> None:
        rid = self._record()
        with self.assertRaises(ValueError):
            svc.add_staff_cashout_send(
                rid,
                {
                    "sender_name": "x",
                    "amount": "50",
                    "payment_method_id": 7,
                    "proof": {"content": PNG, "content_type": "image/png"},
                },
            )
        with self.assertRaises(ValueError):
            svc.add_staff_cashout_send(
                rid,
                {
                    "sender_name": "x",
                    "amount": "50",
                    "method_display_name": "Crypto / USDT",
                    "proof_link": "not a url",
                },
            )
        out = svc.add_staff_cashout_send(
            rid,
            {
                "sender_name": "x",
                "amount": "50",
                "method_display_name": "Crypto / USDT",
                "proof_link": "https://tronscan.org/#/transaction/abc",
            },
        )
        self.assertEqual(
            out["sends"][0]["proof_link"], "https://tronscan.org/#/transaction/abc"
        )

    def test_non_crypto_rejects_link_and_bad_image(self) -> None:
        rid = self._record()
        with self.assertRaises(ValueError):
            svc.add_staff_cashout_send(
                rid,
                {
                    "sender_name": "x",
                    "amount": "50",
                    "payment_method_id": 8,
                    "proof_link": "https://example.com",
                },
            )
        with self.assertRaises(ValueError):
            svc.add_staff_cashout_send(
                rid,
                {
                    "sender_name": "x",
                    "amount": "50",
                    "method_display_name": "Zelle",
                    "proof": {"content": b"%PDF", "content_type": "application/pdf"},
                },
            )

    def test_owed_clear_queued_only_when_fully_sent(self) -> None:
        rid = self._record()
        base = {"sender_name": "x", "method_display_name": "Zelle"}
        out = svc.add_staff_cashout_send(rid, {**base, "amount": "150"})
        self.assertIsNone(out["owed_clear_status"])
        out = svc.add_staff_cashout_send(rid, {**base, "amount": "50"})
        self.assertEqual(out["status"], "cleared")
        self.assertEqual(out["owed_clear_status"], "pending")
        # Deleting a send before the worker ran reopens the balance → withdraw.
        out = svc.delete_staff_cashout_send(rid, out["sends"][1]["id"])
        self.assertIsNone(out["owed_clear_status"])

    def test_owed_clear_not_requeued_after_done(self) -> None:
        rid = self._record()
        base = {"sender_name": "x", "method_display_name": "Zelle"}
        out = svc.add_staff_cashout_send(rid, {**base, "amount": "200"})
        with self.Session() as session:
            session.get(StaffCashoutRecord, rid).owed_clear_status = "done"
            session.commit()
        out = svc.delete_staff_cashout_send(rid, out["sends"][0]["id"])
        self.assertEqual(out["owed_clear_status"], "done")
        out = svc.add_staff_cashout_send(rid, {**base, "amount": "200"})
        self.assertEqual(out["owed_clear_status"], "done")

    def test_no_owed_clear_without_chat(self) -> None:
        rid = self._record(chat_id=None)
        out = svc.add_staff_cashout_send(
            rid, {"sender_name": "x", "method_display_name": "Zelle", "amount": "200"}
        )
        self.assertEqual(out["status"], "cleared")
        self.assertIsNone(out["owed_clear_status"])


def _fake_client():
    client = MagicMock()
    client.is_connected.return_value = True
    client.send_file = AsyncMock()
    client.send_message = AsyncMock()
    client.edit_message = AsyncMock()
    client.unpin_message = AsyncMock()
    return client


class WorkerTickTestCase(_DbTestCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _DbTestCase.setUp(self)
        self.client = _fake_client()
        p = patch(
            "bot.services.mtproto_dm_gc_listener.get_listener_client",
            side_effect=lambda key: self.client,
        )
        p.start()
        self.addCleanup(p.stop)

    async def test_sends_screenshot_with_caption(self) -> None:
        rid = self._record()
        out = svc.add_staff_cashout_send(
            rid,
            {
                "sender_name": "x",
                "amount": "100",
                "method_display_name": "Zelle",
                "notify_player": True,
                "proof": {
                    "content": PNG,
                    "content_type": "image/png",
                    "filename": "s.png",
                },
            },
        )
        sid = out["sends"][0]["id"]
        summary = await notify.process_pending_sends()
        self.assertEqual(summary["sent"], 1)
        args, kwargs = self.client.send_file.call_args
        self.assertEqual(args[0], -100123)
        self.assertEqual(args[1].getvalue(), PNG)
        self.assertEqual(kwargs["caption"], "Sent $100!")
        row = self._get(StaffCashoutMoneySend, sid)
        self.assertEqual(row.notify_status, "sent")
        self.assertIsNotNone(row.notified_at)

    async def test_crypto_link_message(self) -> None:
        rid = self._record()
        svc.add_staff_cashout_send(
            rid,
            {
                "sender_name": "x",
                "amount": "75.50",
                "method_display_name": "Crypto / BTC",
                "notify_player": True,
                "proof_link": "https://mempool.space/tx/abc",
            },
        )
        await notify.process_pending_sends()
        args, _ = self.client.send_message.call_args
        self.assertEqual(args[1], "Sent $75.50!\nhttps://mempool.space/tx/abc")

    async def test_listener_down_leaves_pending(self) -> None:
        rid = self._record()
        out = svc.add_staff_cashout_send(
            rid,
            {
                "sender_name": "x",
                "amount": "10",
                "method_display_name": "Zelle",
                "notify_player": True,
            },
        )
        self.client.is_connected.return_value = False
        summary = await notify.process_pending_sends()
        self.assertEqual(summary["waiting"], 1)
        row = self._get(StaffCashoutMoneySend, out["sends"][0]["id"])
        self.assertEqual(row.notify_status, "pending")

    async def test_failure_marks_failed(self) -> None:
        rid = self._record()
        out = svc.add_staff_cashout_send(
            rid,
            {
                "sender_name": "x",
                "amount": "10",
                "method_display_name": "Zelle",
                "notify_player": True,
            },
        )
        self.client.send_message.side_effect = RuntimeError("chat gone")
        await notify.process_pending_sends()
        row = self._get(StaffCashoutMoneySend, out["sends"][0]["id"])
        self.assertEqual(row.notify_status, "failed")
        self.assertIn("chat gone", row.notify_error)

    async def test_owed_clear_edits_and_unpins_stored_message(self) -> None:
        rid = self._record()
        with self.Session() as session:
            session.get(StaffCashoutRecord, rid).owed_message_id = 555
            session.commit()
        svc.add_staff_cashout_send(
            rid, {"sender_name": "x", "method_display_name": "Zelle", "amount": "200"}
        )
        summary = await notify.process_pending_owed_clears()
        self.assertEqual(summary["cleared"], 1)
        self.client.edit_message.assert_awaited_once_with(-100123, 555, "$0 owed")
        self.client.unpin_message.assert_awaited_once_with(-100123, 555)
        record = self._get(StaffCashoutRecord, rid)
        self.assertEqual(record.owed_clear_status, "done")
        self.assertIsNotNone(record.owed_cleared_at)

    async def test_owed_clear_falls_back_to_pinned_lookup(self) -> None:
        rid = self._record()
        pinned = [
            SimpleNamespace(id=900, message="Welcome"),
            SimpleNamespace(id=777, message="$200 owed"),
        ]

        async def iter_messages(*_a, **_k):
            for m in pinned:
                yield m

        self.client.iter_messages = iter_messages
        svc.add_staff_cashout_send(
            rid, {"sender_name": "x", "method_display_name": "Zelle", "amount": "200"}
        )
        await notify.process_pending_owed_clears()
        self.client.edit_message.assert_awaited_once_with(-100123, 777, "$0 owed")
        record = self._get(StaffCashoutRecord, rid)
        self.assertEqual(record.owed_message_id, 777)
        self.assertEqual(record.owed_clear_status, "done")

    async def test_owed_clear_not_found(self) -> None:
        rid = self._record()

        async def iter_messages(*_a, **_k):
            return
            yield  # pragma: no cover

        self.client.iter_messages = iter_messages
        svc.add_staff_cashout_send(
            rid, {"sender_name": "x", "method_display_name": "Zelle", "amount": "200"}
        )
        await notify.process_pending_owed_clears()
        self.client.edit_message.assert_not_awaited()
        record = self._get(StaffCashoutRecord, rid)
        self.assertEqual(record.owed_clear_status, "failed")
        self.assertEqual(record.owed_clear_error, "Owed message not found")


class FormatTestCase(unittest.TestCase):
    def test_captions(self) -> None:
        self.assertEqual(format_sent_caption(Decimal("200")), "Sent $200!")
        self.assertEqual(format_sent_caption(Decimal("1500.00")), "Sent $1,500!")
        self.assertEqual(format_sent_caption(Decimal("200.5")), "Sent $200.50!")
        self.assertEqual(format_cash_owed(Decimal("0")), "$0 owed")


class OwedMessageIdTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_execute_cash_flow_saves_owed_message_id(self) -> None:
        from bot.services import mtproto_group_cash as cash

        client = MagicMock()
        owed_msg = MagicMock(id=4321)
        owed_msg.pin = AsyncMock()
        client.send_message = AsyncMock(return_value=owed_msg)
        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.get_listener_client",
                return_value=client,
            ),
            patch.object(cash, "_save_owed_message_id") as save,
        ):
            await cash._execute_cash_flow(
                CFG, -100123, Decimal("200"), send_asap=False, record_id=9
            )
        save.assert_called_once_with(9, 4321)


class AddSendRouteTestCase(unittest.TestCase):
    def _app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: "admin"

        def db():
            yield MagicMock()

        app.dependency_overrides[get_db_dependency] = db
        return app

    def _record_out(self) -> dict:
        return {
            "id": 1,
            "club_id": 2,
            "group_title": "RT / 1 / A",
            "amount": Decimal("200"),
            "trigger": "group_cash",
            "created_at": None,
            "updated_at": None,
            "payments": [],
            "sends": [],
        }

    def test_multipart_passes_proof_and_flags(self) -> None:
        with (
            patch(
                "api.routes.cashout_records.add_staff_cashout_send",
                return_value=self._record_out(),
            ) as add,
            patch("api.routes.cashout_records._club_name_map", return_value={}),
            patch("api.routes.cashout_records._load_and_assert_gto", return_value=None),
        ):
            resp = TestClient(self._app()).post(
                "/api/cashout-records/1/sends",
                data={
                    "sender_name": "Rtsupport",
                    "amount": "100",
                    "method_display_name": "Zelle",
                    "notify_player": "true",
                },
                files={"proof": ("s.png", PNG, "image/png")},
            )
        self.assertEqual(resp.status_code, 201, resp.text)
        pdata = add.call_args.args[1]
        self.assertTrue(pdata["notify_player"])
        self.assertEqual(pdata["proof"]["content"], PNG)
        self.assertEqual(pdata["proof"]["content_type"], "image/png")
        self.assertEqual(pdata["amount"], Decimal("100"))

    def test_response_carries_notify_fields(self) -> None:
        out = self._record_out()
        out.update(chat_connected=True, owed_clear_status="pending")
        out["sends"] = [
            {
                "id": 3,
                "sender_name": "x",
                "amount": Decimal("100"),
                "method_display_name": "Zelle",
                "notify_player": True,
                "notify_status": "sent",
                "has_proof": True,
                "created_at": None,
            }
        ]
        with (
            patch(
                "api.routes.cashout_records.get_staff_cashout_record", return_value=out
            ),
            patch("api.routes.cashout_records._club_name_map", return_value={}),
            patch("api.routes.cashout_records._load_and_assert_gto", return_value=None),
        ):
            body = TestClient(self._app()).get("/api/cashout-records/1").json()
        self.assertTrue(body["chat_connected"])
        self.assertEqual(body["owed_clear_status"], "pending")
        self.assertEqual(body["sends"][0]["notify_status"], "sent")
        self.assertTrue(body["sends"][0]["has_proof"])

    def test_proof_download(self) -> None:
        with (
            patch(
                "api.routes.cashout_records.get_staff_cashout_send_proof",
                return_value={
                    "content": PNG,
                    "content_type": "image/png",
                    "filename": "s.png",
                },
            ),
            patch("api.routes.cashout_records._load_and_assert_gto", return_value=None),
        ):
            resp = TestClient(self._app()).get("/api/cashout-records/1/sends/3/proof")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, PNG)
        self.assertEqual(resp.headers["content-type"], "image/png")


if __name__ == "__main__":
    unittest.main()
