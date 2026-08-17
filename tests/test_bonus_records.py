"""Tests for bonus record dashboard CRUD and Zapier-on-create."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import get_current_admin
from api.routes.bonus import router
from bot.services.bonus_player_resolve import BonusPlayerContext
from bot.services.bonus_records import build_bonus_zapier_payload
from db.connection import get_db_dependency
from db.models import BonusRecord, BonusType, Club


def _sample_player_ctx() -> BonusPlayerContext:
    return BonusPlayerContext(
        group_title="CC / 8190-5287 / Jacob",
        gg_player_id="8190-5287",
        club_id=1,
        chat_id=None,
        player_details_id=10,
        zapier_name="CC / 8190-5287 / Jacob",
    )


def _sample_record_dict(**overrides):
    data = {
        "id": 1,
        "player_username": "CC / 8190-5287 / Jacob",
        "amount": Decimal("50"),
        "bonus_type_id": 2,
        "bonus_type_name": "Referral",
        "custom_description": None,
        "club_id": 1,
        "club_name": "Club CC",
        "gg_player_id": "8190-5287",
        "group_title": "CC / 8190-5287 / Jacob",
        "chat_id": None,
        "player_details_id": 10,
        "admin_telegram_user_id": None,
        "created_at": None,
        "player_resolved": True,
    }
    data.update(overrides)
    return data


def _session_cm(session):
    cm = MagicMock()
    cm.__enter__.return_value = session
    cm.__exit__.return_value = False
    return cm


def _club():
    club = MagicMock(spec=Club)
    club.id = 1
    club.name = "Club CC"
    return club


def _bonus_type():
    bt = MagicMock(spec=BonusType)
    bt.id = 2
    bt.name = "Referral"
    bt.is_active = True
    return bt


def _make_create_session(club, bonus_type=None):
    session = MagicMock()

    def getter(model, ident):
        if model is Club:
            return club if ident == 1 else None
        if model is BonusType:
            return bonus_type if bonus_type is not None and ident == bonus_type.id else None
        return None

    session.get.side_effect = getter

    def flush():
        rec = session.add.call_args[0][0]
        rec.id = 1
        rec.created_at = None
        rec.bonus_type = bonus_type
        rec.club = club

    session.flush.side_effect = flush
    return session


def _make_api_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def override_admin():
        return "admin"

    def override_db():
        yield MagicMock()

    app.dependency_overrides[get_current_admin] = override_admin
    app.dependency_overrides[get_db_dependency] = override_db
    return app


class ZapierPayloadTestCase(unittest.TestCase):
    def test_dashboard_admin_id_is_empty_string(self) -> None:
        payload = build_bonus_zapier_payload(
            {
                "player_username": "Jacob",
                "amount": Decimal("10"),
                "admin_user_id": "",
            }
        )
        self.assertEqual(payload["admin_telegram_user_id"], "")
        self.assertEqual(payload["amount"], "10")


class BonusRecordServiceTestCase(unittest.TestCase):
    def test_create_resolved_fires_zapier(self) -> None:
        club = _club()
        bt = _bonus_type()
        session = _make_create_session(club, bt)
        with patch("bot.services.bonus_records.get_db", return_value=_session_cm(session)), patch(
            "bot.services.bonus_records.resolve_bonus_player",
            return_value=_sample_player_ctx(),
        ), patch(
            "bot.services.bonus_records.fire_bonus_zapier_webhook"
        ) as zap, patch(
            "bot.services.bonus_records.build_zapier_name",
            return_value="CC / 8190-5287 / Jacob",
        ):
            from bot.services.bonus_records import create_bonus_record

            data = create_bonus_record(
                club_id=1,
                group_title="CC / 8190-5287 / Jacob",
                amount=Decimal("50"),
                bonus_type_id=2,
            )
        self.assertTrue(data["player_resolved"])
        self.assertEqual(data["gg_player_id"], "8190-5287")
        zap.assert_called_once()
        rec = session.add.call_args[0][0]
        self.assertIsInstance(rec, BonusRecord)
        self.assertIsNone(rec.admin_telegram_user_id)

    def test_create_unresolved_still_fires_zapier(self) -> None:
        club = _club()
        bt = _bonus_type()
        session = _make_create_session(club, bt)
        with patch("bot.services.bonus_records.get_db", return_value=_session_cm(session)), patch(
            "bot.services.bonus_records.resolve_bonus_player",
            return_value=None,
        ), patch(
            "bot.services.bonus_records.fire_bonus_zapier_webhook"
        ) as zap, patch(
            "bot.services.bonus_records.build_zapier_name",
            return_value=None,
        ):
            from bot.services.bonus_records import create_bonus_record

            data = create_bonus_record(
                club_id=1,
                group_title="Jacob",
                amount=Decimal("25"),
                bonus_type_id=2,
            )
        self.assertFalse(data["player_resolved"])
        self.assertEqual(data["group_title"], "Jacob")
        zap.assert_called_once()

    def test_other_requires_description(self) -> None:
        club = _club()
        session = _make_create_session(club)
        with patch("bot.services.bonus_records.get_db", return_value=_session_cm(session)), patch(
            "bot.services.bonus_records.fire_bonus_zapier_webhook"
        ) as zap:
            from bot.services.bonus_records import create_bonus_record

            with self.assertRaises(ValueError) as ctx:
                create_bonus_record(
                    club_id=1,
                    group_title="Jacob",
                    amount=Decimal("10"),
                    bonus_type_id=None,
                    custom_description="",
                )
        self.assertIn("Description", str(ctx.exception))
        zap.assert_not_called()
        session.add.assert_not_called()

    def test_update_does_not_fire_zapier(self) -> None:
        club = _club()
        bt = _bonus_type()
        record = BonusRecord(
            player_username="Jacob",
            amount=Decimal("10"),
            bonus_type_id=2,
            custom_description=None,
            club_id=1,
            group_title="Jacob",
            admin_telegram_user_id=None,
        )
        record.id = 1
        record.bonus_type = bt
        record.club = club
        record.gg_player_id = None
        record.player_details_id = None
        record.chat_id = None
        record.created_at = None
        session = MagicMock()
        session.get.return_value = record
        with patch("bot.services.bonus_records.get_db", return_value=_session_cm(session)), patch(
            "bot.services.bonus_records.resolve_bonus_player",
            return_value=None,
        ), patch(
            "bot.services.bonus_records.fire_bonus_zapier_webhook"
        ) as zap, patch(
            "bot.services.bonus_records.build_zapier_name",
            return_value=None,
        ):
            from bot.services.bonus_records import update_bonus_record

            update_bonus_record(1, amount=Decimal("20"))
        zap.assert_not_called()
        self.assertEqual(record.amount, Decimal("20"))

    def test_delete_does_not_fire_zapier(self) -> None:
        record = MagicMock()
        session = MagicMock()
        session.get.return_value = record
        with patch("bot.services.bonus_records.get_db", return_value=_session_cm(session)), patch(
            "bot.services.bonus_records.fire_bonus_zapier_webhook"
        ) as zap:
            from bot.services.bonus_records import delete_bonus_record

            ok = delete_bonus_record(1)
        self.assertTrue(ok)
        session.delete.assert_called_once_with(record)
        zap.assert_not_called()


class BonusRecordsApiTestCase(unittest.TestCase):
    def test_create_resolved(self) -> None:
        with patch(
            "api.routes.bonus.create_bonus_record",
            return_value=_sample_record_dict(),
        ) as mock_create:
            client = TestClient(_make_api_app())
            resp = client.post(
                "/api/bonus/records",
                json={
                    "club_id": 1,
                    "group_title": "CC / 8190-5287 / Jacob",
                    "amount": "50",
                    "bonus_type_id": 2,
                },
            )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.json()["player_resolved"])
        mock_create.assert_called_once()

    def test_create_unresolved_still_201(self) -> None:
        with patch(
            "api.routes.bonus.create_bonus_record",
            return_value=_sample_record_dict(
                player_resolved=False,
                gg_player_id=None,
                group_title="Jacob",
                player_username="Jacob",
            ),
        ):
            client = TestClient(_make_api_app())
            resp = client.post(
                "/api/bonus/records",
                json={
                    "club_id": 1,
                    "group_title": "Jacob",
                    "amount": "25",
                    "bonus_type_id": 2,
                },
            )
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(resp.json()["player_resolved"])

    def test_other_without_description_400(self) -> None:
        with patch(
            "api.routes.bonus.create_bonus_record",
            side_effect=ValueError("Description is required for Other"),
        ):
            client = TestClient(_make_api_app())
            resp = client.post(
                "/api/bonus/records",
                json={
                    "club_id": 1,
                    "group_title": "Jacob",
                    "amount": "10",
                    "bonus_type_id": None,
                    "custom_description": "",
                },
            )
        self.assertEqual(resp.status_code, 400)

    def test_patch_does_not_call_zapier(self) -> None:
        with patch(
            "api.routes.bonus.update_bonus_record",
            return_value=_sample_record_dict(amount=Decimal("75")),
        ), patch(
            "bot.services.bonus_records.fire_bonus_zapier_webhook"
        ) as zap:
            client = TestClient(_make_api_app())
            resp = client.patch("/api/bonus/records/1", json={"amount": "75"})
        self.assertEqual(resp.status_code, 200)
        zap.assert_not_called()

    def test_delete_does_not_call_zapier(self) -> None:
        with patch(
            "api.routes.bonus.delete_bonus_record",
            return_value=True,
        ), patch(
            "bot.services.bonus_records.fire_bonus_zapier_webhook"
        ) as zap:
            client = TestClient(_make_api_app())
            resp = client.delete("/api/bonus/records/1")
        self.assertEqual(resp.status_code, 204)
        zap.assert_not_called()
