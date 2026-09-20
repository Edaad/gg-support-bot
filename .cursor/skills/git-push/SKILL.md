---
name: git-push
description: >-
  Push commits for gg-support-bot with known network and pre-push pitfalls.
  Use when the user asks to push, git push, publish the branch, or push to
  origin/main/Heroku after a commit.
---

# Git push (gg-support-bot)

Only push when the user explicitly asks. Never push unprompted.

## Default command

Prefer HTTPS (SSH to `github.com:22` often times out in this environment):

```bash
gh auth setup-git   # once if needed; uses HTTPS credential helper
env -u DATABASE_URL -u DATABASE_URL_TEST \
  git push https://github.com/Edaad/gg-support-bot.git HEAD:<branch>
```

Replace `<branch>` with the current branch name (`main`, feature branch, etc.).

Then refresh tracking so `git status` is accurate:

```bash
git fetch https://github.com/Edaad/gg-support-bot.git \
  <branch>:refs/remotes/origin/<branch>
git status -sb
```

Do **not** change `git remote` permanently unless the user asks.

## Why these flags

| Issue | Fix |
|-------|-----|
| `ssh: connect to host github.com port 22: Operation timed out` | Push via HTTPS URL + `gh` auth (above) |
| Pre-push tests error connecting to RDS (`DATABASE_URL` from `.env`) | `env -u DATABASE_URL -u DATABASE_URL_TEST` for the push process only |
| Status still `ahead 1` after HTTPS push | `git fetch … <branch>:refs/remotes/origin/<branch>` |

## Pre-push hook (expect ~2–3 minutes)

`.git/hooks/pre-push` runs, in order:

1. `ruff check .` and `ruff format --check .` (`pip install -r requirements-dev.txt`)
2. Python import smoke (`scripts/pre_push_import_smoke.py`)
3. Full unittest discover under `tests/`
4. `npm run build --prefix dashboard`
5. Graphify sync (unless `GRAPHIFY_SKIP_HOOK=1`)

Do **not** use `--no-verify`, `BUILD_SKIP_HOOK=1`, or `GRAPHIFY_SKIP_HOOK=1` unless the user explicitly allows skipping hooks.

If tests fail for a real code reason, fix and push again. If they fail only because `DATABASE_URL` points at unreachable RDS, retry with `env -u DATABASE_URL -u DATABASE_URL_TEST` (not with hook skip).

## Permissions / approval

- Request full network / `all` for push + fetch.
- Pushing to **main** may require smart-mode user approval — retry with approval when blocked; do not switch to a pastebin or other intermediary.

## Heroku

Do not `git push heroku` unless the user explicitly asks for a Heroku deploy push.
