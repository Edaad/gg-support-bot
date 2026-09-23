"""Throwaway local PostgreSQL 17 cluster for migration checks. Never uses DATABASE_URL."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

_BIN_DIR_CANDIDATES = (
    "/opt/homebrew/opt/postgresql@17/bin",
    "/usr/local/opt/postgresql@17/bin",
    "/usr/lib/postgresql/17/bin",
)
_PORT = "55439"


def pg_bin_dir() -> Path:
    candidates = [os.getenv("PG17_BIN", ""), *_BIN_DIR_CANDIDATES]
    for candidate in candidates:
        if candidate and (Path(candidate) / "initdb").exists():
            return Path(candidate)
    raise SystemExit(
        "PostgreSQL 17 binaries not found. Install with `brew install postgresql@17` "
        "or set PG17_BIN to the directory containing initdb/pg_ctl/pg_dump."
    )


_PROD_SESSION_VARS = ("PGOPTIONS", "PGHOST")


def local_env() -> dict[str, str]:
    """Environment for local tools: drop settings meant for prod sessions."""
    return {k: v for k, v in os.environ.items() if k not in _PROD_SESSION_VARS}


@contextlib.contextmanager
def temp_postgres(dbname: str = "migration_check") -> Iterator[str]:
    """Yield a SQLAlchemy URL for an empty database on a throwaway cluster.

    In-process connections (e.g. Alembic) read libpq settings from os.environ, so
    prod-session variables are removed for the duration and restored afterwards.
    """
    bindir = pg_bin_dir()
    root = Path(tempfile.mkdtemp(prefix="ggpg-", dir="/tmp"))
    data = root / "data"
    env = local_env()
    saved = {k: os.environ.pop(k) for k in _PROD_SESSION_VARS if k in os.environ}

    def run(*args: str) -> None:
        subprocess.run(args, check=True, env=env, stdout=subprocess.DEVNULL)

    try:
        run(
            str(bindir / "initdb"),
            "-D",
            str(data),
            "-U",
            "postgres",
            "--auth=trust",
            "-E",
            "UTF8",
        )
        run(
            str(bindir / "pg_ctl"),
            "-D",
            str(data),
            "-o",
            f"-k {root} -p {_PORT} -c listen_addresses=''",
            "-l",
            str(root / "log"),
            "-w",
            "start",
        )
        run(
            str(bindir / "createdb"),
            "-h",
            str(root),
            "-p",
            _PORT,
            "-U",
            "postgres",
            dbname,
        )
        yield f"postgresql://postgres@/{dbname}?host={root}&port={_PORT}"
    finally:
        subprocess.run(
            [str(bindir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(root, ignore_errors=True)
        os.environ.update(saved)
