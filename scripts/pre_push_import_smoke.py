"""Import production entrypoints before git push (catches ImportError / NameError)."""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _import_bot_handlers() -> None:
    import bot.handlers as handlers_pkg

    for mod in pkgutil.walk_packages(handlers_pkg.__path__, handlers_pkg.__name__ + "."):
        importlib.import_module(mod.name)


def _import_release_script() -> None:
    path = _ROOT / "scripts" / "notify_deploy_maintenance.py"
    spec = importlib.util.spec_from_file_location("notify_deploy_maintenance", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load release script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def main() -> int:
    steps = [
        ("api.app", lambda: importlib.import_module("api.app")),
        ("bot.handlers", _import_bot_handlers),
        ("cashier.main", lambda: importlib.import_module("cashier.main")),
        ("notification.main", lambda: importlib.import_module("notification.main")),
        ("scripts/notify_deploy_maintenance.py", _import_release_script),
    ]
    for label, fn in steps:
        print(f"[pre-push build] importing {label}...", flush=True)
        fn()
    print("[pre-push build] python imports ok", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[pre-push build] python import failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
