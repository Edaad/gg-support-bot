"""Database sessions for MCP tools.

``read_session`` runs on the shared engine (prod Postgres caps connections at 20) but
starts a ``READ ONLY`` transaction with a ``statement_timeout`` so a read tool can
never write or hog the dyno past Heroku's 30 s router timeout. ``write_session`` is
``db.connection.get_db`` plus the same timeout. Tests set ``session_factory``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from mcp_server.settings import load_settings

session_factory: Callable[[], Session] | None = None


def _new_session() -> Session:
    if session_factory is not None:
        return session_factory()
    from db.connection import get_session

    return get_session()


def _is_postgres(session: Session) -> bool:
    try:
        bind = session.get_bind()
    except Exception:
        return False
    return bool(bind is not None and bind.dialect.name == "postgresql")


def _timeout_ms(timeout_ms: int | None) -> int:
    return int(timeout_ms or load_settings().statement_timeout_ms)


@contextmanager
def read_session(timeout_ms: int | None = None) -> Iterator[Session]:
    session = _new_session()
    try:
        if _is_postgres(session):
            session.execute(text("SET TRANSACTION READ ONLY"))
            session.execute(
                text(f"SET LOCAL statement_timeout = {_timeout_ms(timeout_ms)}")
            )
        yield session
    finally:
        session.rollback()
        session.close()


@contextmanager
def write_session(timeout_ms: int | None = None) -> Iterator[Session]:
    session = _new_session()
    try:
        if _is_postgres(session):
            session.execute(
                text(f"SET LOCAL statement_timeout = {_timeout_ms(timeout_ms)}")
            )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
