#!/usr/bin/env python
"""Heroku release phase: warn admins before dynos restart (cooldown via Postgres).

Always exits 0 so a failed DM does not block deploy.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from bot.services.deploy_notify import (
    DEPLOY_NOTIFY_MESSAGE,
    is_deploy_notify_enabled,
    notify_all_admin_user_ids,
    record_deploy_notify,
    should_notify_deploy,
)
from db.connection import get_db, init_engine
from migrate_deploy_notify_state import ensure_deploy_notify_state

logger = logging.getLogger("notify_deploy_maintenance")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _run() -> None:
    if not is_deploy_notify_enabled():
        logger.info("deploy_notify: disabled (%s)", "DEPLOY_NOTIFY_ENABLED")
        return

    init_engine()
    ensure_deploy_notify_state()
    with get_db() as session:
        if not should_notify_deploy(session):
            logger.info("deploy_notify: skipped (cooldown)")
            return

        sent = await notify_all_admin_user_ids(DEPLOY_NOTIFY_MESSAGE)
        if sent == 0:
            logger.warning("deploy_notify: no admin DMs delivered")
            return

        record_deploy_notify(session)
        logger.info("deploy_notify: sent to %s admin(s)", sent)


def main() -> None:
    _configure_logging()
    try:
        asyncio.run(_run())
    except Exception:
        logger.exception("deploy_notify: unexpected error (deploy continues)")
    sys.exit(0)


if __name__ == "__main__":
    main()
