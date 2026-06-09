#!/bin/sh
# graphify-pre-push-hook-start
# Sync graph rebuild before git push (so graphify-out is current when you push).
# Installed by: scripts/install-graphify-hooks.sh

export PYTHONHASHSEED=0

[ "${GRAPHIFY_SKIP_HOOK:-0}" = "1" ] && exit 0
[ ! -d "graphify-out" ] && exit 0

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
[ -d "$GIT_DIR/rebase-merge" ] && exit 0
[ -d "$GIT_DIR/rebase-apply" ] && exit 0
[ -f "$GIT_DIR/MERGE_HEAD" ] && exit 0
[ -f "$GIT_DIR/CHERRY_PICK_HEAD" ] && exit 0

CHANGED=""
while read -r local_ref local_sha remote_ref remote_sha; do
  [ -z "$local_sha" ] && continue
  [ "$local_sha" = "0000000000000000000000000000000000000000" ] && continue
  if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
    base=$(git merge-base "$local_sha" refs/remotes/origin/main 2>/dev/null \
      || git merge-base "$local_sha" refs/remotes/origin/master 2>/dev/null \
      || git merge-base "$local_sha" main 2>/dev/null \
      || git merge-base "$local_sha" master 2>/dev/null \
      || true)
    if [ -n "$base" ]; then
      part=$(git diff --name-only "$base" "$local_sha" 2>/dev/null)
    else
      part=$(git rev-list --parents -n 1 "$local_sha" 2>/dev/null | awk '{print $2}')
      if [ -n "$part" ] && [ "$part" != "0000000000000000000000000000000000000000" ]; then
        part=$(git diff --name-only "$part" "$local_sha" 2>/dev/null)
      else
        part=$(git show --pretty=format: --name-only "$local_sha" 2>/dev/null)
      fi
    fi
  else
    part=$(git diff --name-only "$remote_sha" "$local_sha" 2>/dev/null)
  fi
  CHANGED="$CHANGED
$part"
done

if [ -z "$(printf '%s' "$CHANGED" | tr -d '[:space:]')" ]; then
  CHANGED=$(git diff --name-only "@{upstream}"..HEAD 2>/dev/null || true)
fi

_NON_GRAPH=$(printf '%s\n' "$CHANGED" | grep -v '^graphify-out/' | grep -v '^$' || true)
if [ -z "$_NON_GRAPH" ]; then
  exit 0
fi

GRAPHIFY_PYTHON=""
_PINNED='__PINNED_PYTHON__'
if [ -n "$_PINNED" ] && [ -x "$_PINNED" ] && "$_PINNED" -c "import graphify" 2>/dev/null; then
  GRAPHIFY_PYTHON="$_PINNED"
fi
if [ -z "$GRAPHIFY_PYTHON" ] && [ -f "graphify-out/.graphify_python" ]; then
  _FROM_FILE=$(cat "graphify-out/.graphify_python" 2>/dev/null | tr -d '[:space:]')
  case "$_FROM_FILE" in
    *[!a-zA-Z0-9/_.@:\-]*) _FROM_FILE="" ;;
  esac
  if [ -n "$_FROM_FILE" ] && [ -x "$_FROM_FILE" ] && "$_FROM_FILE" -c "import graphify" 2>/dev/null; then
    GRAPHIFY_PYTHON="$_FROM_FILE"
  fi
fi
if [ -z "$GRAPHIFY_PYTHON" ]; then
  GRAPHIFY_BIN=$(command -v graphify 2>/dev/null)
  if [ -n "$GRAPHIFY_BIN" ]; then
    case "$GRAPHIFY_BIN" in
      *.exe) _SHEBANG="" ;;
      *)     _SHEBANG=$(head -1 "$GRAPHIFY_BIN" | sed 's/^#![[:space:]]*//') ;;
    esac
    case "$_SHEBANG" in
      */env\ *) GRAPHIFY_PYTHON="${_SHEBANG#*/env }" ;;
      *)         GRAPHIFY_PYTHON="$_SHEBANG" ;;
    esac
    case "$GRAPHIFY_PYTHON" in
      *[!a-zA-Z0-9/_.@-]*) GRAPHIFY_PYTHON="" ;;
    esac
    if [ -n "$GRAPHIFY_PYTHON" ] && ! "$GRAPHIFY_PYTHON" -c "import graphify" 2>/dev/null; then
      GRAPHIFY_PYTHON=""
    fi
  fi
fi
if [ -z "$GRAPHIFY_PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1 && python3 -c "import graphify" 2>/dev/null; then
    GRAPHIFY_PYTHON="python3"
  elif command -v python >/dev/null 2>&1 && python -c "import graphify" 2>/dev/null; then
    GRAPHIFY_PYTHON="python"
  elif command -v graphify >/dev/null 2>&1; then
    echo "[graphify pre-push] updating graph (via graphify CLI)..."
    graphify update . || exit 1
    exit 0
  else
    echo "[graphify pre-push] graphify not found; skipping graph update." >&2
    exit 0
  fi
fi

_GRAPHIFY_LOG="${HOME}/.cache/graphify-rebuild.log"
mkdir -p "$(dirname "$_GRAPHIFY_LOG")"
echo "[graphify pre-push] syncing graph before push (log: $_GRAPHIFY_LOG)..."
export GRAPHIFY_CHANGED="$_NON_GRAPH"
"$GRAPHIFY_PYTHON" -c "
import os, signal, sys
from pathlib import Path

changed_raw = os.environ.get('GRAPHIFY_CHANGED', '')
changed = [Path(f.strip()) for f in changed_raw.strip().splitlines() if f.strip()]
if not changed:
    sys.exit(0)
print(f'[graphify pre-push] {len(changed)} file(s) in push range - rebuilding graph...')
try:
    from graphify.watch import _rebuild_code, _apply_resource_limits
    _apply_resource_limits()
    _timeout = int(os.environ.get('GRAPHIFY_REBUILD_TIMEOUT', '600'))
    if _timeout > 0 and hasattr(signal, 'SIGALRM'):
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError(f'graphify rebuild exceeded {_timeout}s')))
        signal.alarm(_timeout)
    _force = os.environ.get('GRAPHIFY_FORCE', '').lower() in ('1', 'true', 'yes')
    _root = Path('.')
    _saved = Path('graphify-out/.graphify_root')
    if _saved.exists():
        _txt = _saved.read_text(encoding='utf-8').strip()
        if _txt:
            _root = Path(_txt)
    _rebuild_code(_root, changed_paths=changed, force=_force)
except TimeoutError as exc:
    print(f'[graphify pre-push] {exc}', file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f'[graphify pre-push] rebuild failed: {exc}', file=sys.stderr)
    sys.exit(1)
" >> "$_GRAPHIFY_LOG" 2>&1 || exit 1

if ! git diff --quiet -- graphify-out/ 2>/dev/null; then
  echo "[graphify pre-push] graphify-out/ updated. Commit those changes before push if you track them in git." >&2
fi
# graphify-pre-push-hook-end
