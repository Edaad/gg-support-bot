#!/bin/sh
# Install graphify git hooks (post-commit, post-checkout, pre-push).
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if ! command -v graphify >/dev/null 2>&1; then
  echo "graphify not found. Install with: uv tool install graphifyy" >&2
  exit 1
fi

graphify hook install

HOOKS_DIR=$(git -C "$ROOT" rev-parse --git-path hooks)
HOOKS_DIR="$ROOT/$HOOKS_DIR"
mkdir -p "$HOOKS_DIR"

PUSH_HOOK="$HOOKS_DIR/pre-push"
BUILD_BODY="$ROOT/scripts/pre-push-build-hook.sh"
GRAPH_BODY="$ROOT/scripts/graphify-pre-push-hook.sh"

TMP=$(mktemp)
echo '#!/bin/sh' > "$TMP"
cat "$BUILD_BODY" >> "$TMP"
tail -n +2 "$GRAPH_BODY" >> "$TMP"
mv "$TMP" "$PUSH_HOOK"
chmod +x "$PUSH_HOOK"
echo "pre-push: installed build + graphify hooks at $PUSH_HOOK"

# Pin Python path in pre-push hook (same as graphify hook install).
PINNED=""
if [ -x "$HOME/.local/share/uv/tools/graphifyy/bin/python" ]; then
  PINNED="$HOME/.local/share/uv/tools/graphifyy/bin/python"
elif command -v graphify >/dev/null 2>&1; then
  GF_BIN=$(command -v graphify)
  case "$GF_BIN" in
    *.exe) ;;
    *)
      SHEBANG=$(head -1 "$GF_BIN" | sed 's/^#![[:space:]]*//')
      case "$SHEBANG" in
        */env\ *) PINNED="${SHEBANG#*/env }" ;;
        *) PINNED="$SHEBANG" ;;
      esac
      ;;
  esac
fi
if [ -n "$PINNED" ] && [ -f "$PUSH_HOOK" ]; then
  if sed --version >/dev/null 2>&1; then
    sed -i "s|__PINNED_PYTHON__|$PINNED|g" "$PUSH_HOOK"
  else
    sed -i '' "s|__PINNED_PYTHON__|$PINNED|g" "$PUSH_HOOK"
  fi
fi

graphify hook status
echo ""
echo "Hooks active:"
echo "  post-commit  — background rebuild after each commit"
echo "  post-checkout — rebuild when switching branches"
echo "  pre-push     — python compile + dashboard build, then graphify sync, before git push / gp"
echo ""
echo "Skip once:"
echo "  BUILD_SKIP_HOOK=1 git push      — skip python + dashboard builds"
echo "  GRAPHIFY_SKIP_HOOK=1 git push   — skip graphify sync"
echo "Log: ~/.cache/graphify-rebuild.log"
