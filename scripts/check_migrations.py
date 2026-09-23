#!/usr/bin/env python
"""Migration gate (pre-push): revision rules plus a real upgrade on a throwaway Postgres.

Checks:
  - exactly one head; revision ids fit alembic_version.version_num (<= 32 chars)
  - revisions use migrations.helpers (guarded DDL), never raw ``op.*``
  - revisions are forward-only (downgrade raises NotImplementedError)
  - ``upgrade head`` on an empty database, then every revision re-run (idempotency)
  - ``alembic check``: db/models.py matches the migrated schema

Usage:
  python scripts/check_migrations.py            # all checks (needs PostgreSQL 17)
  python scripts/check_migrations.py --static-only
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts._temp_postgres import temp_postgres

_PREFIX = "[migrations]"
_MAX_REVISION_LEN = 32


def _uses_raw_op(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "alembic":
            if any(alias.name == "op" for alias in node.names):
                return True
        if isinstance(node, ast.Import):
            if any(alias.name == "alembic.op" for alias in node.names):
                return True
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "op"
        ):
            return True
    return False


def _config() -> Config:
    return Config(str(_REPO_ROOT / "alembic.ini"))


def static_problems() -> list[str]:
    problems: list[str] = []
    scripts = ScriptDirectory.from_config(_config())
    heads = scripts.get_heads()
    if len(heads) != 1:
        problems.append(f"expected exactly one head, found {heads}")
    for rev in scripts.walk_revisions():
        name = Path(rev.path).name
        if len(rev.revision) > _MAX_REVISION_LEN:
            problems.append(
                f"{name}: revision id {rev.revision!r} is longer than {_MAX_REVISION_LEN} chars"
            )
        source = Path(rev.path).read_text()
        if _uses_raw_op(source):
            problems.append(f"{name}: uses raw op.*; use migrations.helpers instead")
        downgrade = source.split("def downgrade", 1)[-1]
        if "raise NotImplementedError" not in downgrade:
            problems.append(f"{name}: downgrade() must raise NotImplementedError")
    return problems


def database_checks() -> None:
    cfg = _config()
    with temp_postgres() as url:
        os.environ["DATABASE_URL"] = url
        print(f"{_PREFIX} upgrade head on an empty database...", flush=True)
        command.upgrade(cfg, "head")
        print(f"{_PREFIX} re-running every revision over head...", flush=True)
        command.stamp(cfg, "base")
        command.upgrade(cfg, "head")
        print(f"{_PREFIX} alembic check (models vs migrated schema)...", flush=True)
        command.check(cfg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()

    problems = static_problems()
    if problems:
        for problem in problems:
            print(f"{_PREFIX} {problem}", file=sys.stderr)
        return 1
    print(f"{_PREFIX} revision rules ok", flush=True)

    if not args.static_only:
        try:
            database_checks()
        except Exception as exc:
            print(f"{_PREFIX} database check failed: {exc}", file=sys.stderr)
            return 1
        print(f"{_PREFIX} upgrade, re-run and alembic check ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
