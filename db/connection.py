import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

_engine = None
_SessionLocal = None


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def init_engine():
    global _engine, _SessionLocal
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    _SessionLocal = sessionmaker(bind=_engine)
    return _engine


def get_engine():
    global _engine
    if _engine is None:
        init_engine()
    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal()


@contextmanager
def get_db():
    """Context manager that yields a SQLAlchemy session with auto-commit/rollback."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_dependency():
    """FastAPI dependency that yields a database session."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
