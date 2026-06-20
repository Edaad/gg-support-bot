# pre-push-build-hook-start
# Run Python import smoke + tests + dashboard production build before git push.
# Graphify sync runs only after both succeed (see graphify-pre-push-hook below).
# Installed by: scripts/install-graphify-hooks.sh

[ "${BUILD_SKIP_HOOK:-0}" = "1" ] && exit 0

PYTHON=""
if [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
fi

if [ -z "$PYTHON" ]; then
  echo "[pre-push build] python not found; push aborted." >&2
  exit 1
fi

echo "[pre-push build] checking Python imports (scripts/pre_push_import_smoke.py)..."
"$PYTHON" scripts/pre_push_import_smoke.py || {
  echo "[pre-push build] python import check failed; push aborted (graphify skipped)." >&2
  exit 1
}

echo "[pre-push build] running Python tests (python -m unittest discover -s tests)..."
"$PYTHON" -m unittest discover -s tests -p 'test_*.py' || {
  echo "[pre-push build] python tests failed; push aborted (graphify skipped)." >&2
  exit 1
}
echo "[pre-push build] python tests ok"

if ! command -v npm >/dev/null 2>&1; then
  echo "[pre-push build] npm not found; push aborted." >&2
  exit 1
fi

if [ ! -d dashboard/node_modules ]; then
  echo "[pre-push build] dashboard/node_modules missing. Run: npm ci --prefix dashboard" >&2
  exit 1
fi

echo "[pre-push build] building dashboard (npm run build --prefix dashboard)..."
npm run build --prefix dashboard || {
  echo "[pre-push build] dashboard build failed; push aborted (graphify skipped)." >&2
  exit 1
}
# pre-push-build-hook-end
