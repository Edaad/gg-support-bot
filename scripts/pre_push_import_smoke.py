"""Import production entrypoints before deploy (catches ImportError / NameError)."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _import_worker_handlers(*, test_mode: bool) -> None:
    from bot.main import import_worker_handlers

    import_worker_handlers(test_mode=test_mode)


def _import_cashier_wizard() -> None:
    from cashier.handlers.wizard import get_cashier_wizard_handler

    get_cashier_wizard_handler()


def _import_release_script() -> None:
    path = _ROOT / "scripts" / "notify_deploy_maintenance.py"
    spec = importlib.util.spec_from_file_location("notify_deploy_maintenance", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load release script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def main(*, log_prefix: str = "[pre-push build]") -> int:
    steps = [
        ("api.app", lambda: importlib.import_module("api.app")),
        ("bot.main.import_worker_handlers(test_mode=False)", lambda: _import_worker_handlers(test_mode=False)),
        ("bot.main.import_worker_handlers(test_mode=True)", lambda: _import_worker_handlers(test_mode=True)),
        ("cashier.handlers.wizard.get_cashier_wizard_handler", _import_cashier_wizard),
        ("notification.main", lambda: importlib.import_module("notification.main")),
        ("scripts/notify_deploy_maintenance.py", _import_release_script),
    ]
    for label, fn in steps:
        print(f"{log_prefix} importing {label}...", flush=True)
        fn()
    print(f"{log_prefix} python imports ok", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[pre-push build] python import failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
