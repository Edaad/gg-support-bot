"""Tests for MTProto club health status (Dashboard session display)."""

import unittest
from unittest.mock import patch

from bot.services.mtproto_club_health import (
    STATUS_AUTH_KEY_DUPLICATED,
    STATUS_CONNECTED,
    STATUS_NO_SESSION,
    STATUS_UNKNOWN,
    ClubHealthSnapshot,
    classify_mtproto_error,
    resolve_club_session_status,
)


class TestClassifyMtprotoError(unittest.TestCase):
    def test_auth_key_duplicated_by_type_name(self) -> None:
        class AuthKeyDuplicatedError(Exception):
            pass

        status, detail = classify_mtproto_error(AuthKeyDuplicatedError("dup"))
        self.assertEqual(status, STATUS_AUTH_KEY_DUPLICATED)
        self.assertIn("AuthKeyDuplicated", detail)

    def test_generic_error(self) -> None:
        status, detail = classify_mtproto_error(RuntimeError("boom"))
        self.assertEqual(status, "error")
        self.assertEqual(detail, "boom")


class TestResolveClubSessionStatus(unittest.TestCase):
    def test_no_session(self) -> None:
        out = resolve_club_session_status(
            "round_table",
            session_stored=False,
            mtproto_enabled=True,
            listener_enabled=True,
        )
        self.assertFalse(out["session_authorized"])
        self.assertEqual(out["worker_status"], STATUS_NO_SESSION)

    @patch("bot.services.mtproto_club_health.load_club_health")
    def test_connected_on_worker(self, mock_load) -> None:
        mock_load.return_value = ClubHealthSnapshot(
            club_key="round_table",
            worker_connected=True,
            session_valid=True,
            status=STATUS_CONNECTED,
            status_detail=None,
            telegram_user_id=1,
            checked_at=None,
        )
        out = resolve_club_session_status(
            "round_table",
            session_stored=True,
            mtproto_enabled=True,
            listener_enabled=True,
        )
        self.assertTrue(out["session_authorized"])

    @patch("bot.services.mtproto_club_health.load_club_health")
    def test_stored_but_duplicated(self, mock_load) -> None:
        mock_load.return_value = ClubHealthSnapshot(
            club_key="round_table",
            worker_connected=False,
            session_valid=False,
            status=STATUS_AUTH_KEY_DUPLICATED,
            status_detail="dup",
            telegram_user_id=None,
            checked_at=None,
        )
        out = resolve_club_session_status(
            "round_table",
            session_stored=True,
            mtproto_enabled=True,
            listener_enabled=True,
        )
        self.assertFalse(out["session_authorized"])
        self.assertEqual(out["worker_status"], STATUS_AUTH_KEY_DUPLICATED)

    def test_mtproto_disabled(self) -> None:
        out = resolve_club_session_status(
            "round_table",
            session_stored=True,
            mtproto_enabled=False,
            listener_enabled=True,
        )
        self.assertFalse(out["session_authorized"])
        self.assertEqual(out["worker_status"], "mtproto_disabled")

    @patch("bot.services.mtproto_club_health.load_club_health", return_value=None)
    def test_stored_unknown_health(self, _mock) -> None:
        out = resolve_club_session_status(
            "round_table",
            session_stored=True,
            mtproto_enabled=True,
            listener_enabled=True,
        )
        self.assertFalse(out["session_authorized"])
        self.assertEqual(out["worker_status"], STATUS_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
