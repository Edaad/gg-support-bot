"""Refuse to start a process unless the database is at the Alembic head revision."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


class SchemaNotAtHeadError(RuntimeError):
    pass


def expected_heads() -> set[str]:
    return set(ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_heads())


def current_revisions(engine) -> set[str]:
    with engine.connect() as conn:
        return set(MigrationContext.configure(conn).get_current_heads())


def ensure_schema_at_head(engine) -> None:
    current = current_revisions(engine)
    heads = expected_heads()
    if current != heads:
        raise SchemaNotAtHeadError(
            f"Database schema is at {sorted(current) or 'no Alembic revision'}, "
            f"expected {sorted(heads)}. Run `alembic upgrade head` "
            "(the Heroku release phase does this on deploy)."
        )
