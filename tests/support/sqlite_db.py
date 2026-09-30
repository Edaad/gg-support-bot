"""In-memory SQLite schema for a subset of ``db.models`` tables.

Tables are copied into a private ``MetaData`` so Postgres-only bits (JSONB,
``'…'::jsonb`` server defaults) can be swapped for SQLite equivalents without
mutating the real models. ORM classes still work because table and column names
are unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import JSON, BigInteger, Integer, MetaData, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base


def make_sqlite_session_factory(table_names: list[str]):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    md = MetaData()
    for name in table_names:
        Base.metadata.tables[name].to_metadata(md)
    for table in md.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
            if col.primary_key and isinstance(col.type, BigInteger):
                # SQLite only autoincrements INTEGER PRIMARY KEY.
                col.type = Integer()
            default = col.server_default
            if default is not None and "::" in str(getattr(default, "arg", "")):
                col.server_default = None
    md.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db_for(factory):
    """A drop-in for ``db.connection.get_db`` bound to ``factory``."""

    @contextmanager
    def _get_db():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _get_db
