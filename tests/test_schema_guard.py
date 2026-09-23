"""Boot-time check that the database is at the Alembic head revision."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine, text

from db.schema_guard import (
    SchemaNotAtHeadError,
    ensure_schema_at_head,
    expected_heads,
)


def _stamp(engine, revision: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
            {"rev": revision},
        )


class SchemaGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")

    def test_single_head(self) -> None:
        self.assertEqual(len(expected_heads()), 1)

    def test_unstamped_database_refuses(self) -> None:
        with self.assertRaises(SchemaNotAtHeadError):
            ensure_schema_at_head(self.engine)

    def test_older_revision_refuses(self) -> None:
        _stamp(self.engine, "0001_baseline")
        with self.assertRaisesRegex(SchemaNotAtHeadError, "alembic upgrade head"):
            ensure_schema_at_head(self.engine)

    def test_head_revision_passes(self) -> None:
        (head,) = expected_heads()
        _stamp(self.engine, head)
        ensure_schema_at_head(self.engine)


if __name__ == "__main__":
    unittest.main()
