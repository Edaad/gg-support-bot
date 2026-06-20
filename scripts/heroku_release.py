#!/usr/bin/env python
"""Heroku release phase: block deploy on import errors; notify admins on success."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_LOG_PREFIX = "[heroku release]"


def _run_import_smoke() -> None:
    from scripts.pre_push_import_smoke import main as import_smoke_main

    code = import_smoke_main(log_prefix=_LOG_PREFIX)
    if code != 0:
        raise SystemExit(code)


def _run_deploy_notify() -> None:
    from scripts.notify_deploy_maintenance import main as notify_main

    notify_main()


def main() -> None:
    try:
        _run_import_smoke()
        print(f"{_LOG_PREFIX} import smoke ok", flush=True)
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(f"{_LOG_PREFIX} import smoke failed (release aborted)", file=sys.stderr, flush=True)
            raise
        raise
    except Exception as exc:
        print(f"{_LOG_PREFIX} import smoke failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc

    _run_deploy_notify()


if __name__ == "__main__":
    main()
