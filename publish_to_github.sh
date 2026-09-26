#!/bin/bash
# Publishes what was just deployed to GitHub, so the repo always matches what
# is live on Cloud Run no matter who ran the deploy. Called at the end of both
# ./deploy.sh (andSons) and ova_email/deploy.sh (OVA) - only after the
# `gcloud run deploy` succeeded (those scripts use `set -e`).
#
# Usage: ./publish_to_github.sh "<commit message>" <path> [<path> ...]
#   Only the given paths are staged (never `git add -A` on the whole repo), so
#   unrelated in-progress work elsewhere in the tree isn't swept into the commit.
#
# Env toggles:
#   SKIP_GIT_PUSH=1     deploy only, don't touch git (e.g. an emergency hotfix
#                       you'll commit properly later)
#   DRY_RUN=1           show what WOULD be staged/committed/pushed, change nothing
#   GIT_PUSH_REMOTE=x   remote to push to (default: origin)
set -euo pipefail

if [ "${SKIP_GIT_PUSH:-0}" = "1" ]; then
  echo "SKIP_GIT_PUSH=1 - deployed, but NOT publishing to GitHub."
  exit 0
fi

MSG="$1"; shift
cd "$(git rev-parse --show-toplevel)"
REMOTE="${GIT_PUSH_REMOTE:-origin}"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [ "$BRANCH" = "HEAD" ]; then
  echo "DEPLOY SUCCEEDED, but GitHub publish skipped: detached HEAD (check out a branch first)."
  exit 1
fi

echo ""
echo "=== Publishing to GitHub ($REMOTE/$BRANCH) ==="

# Only stage the paths for the app that was deployed.
git add -A -- "$@"

STAGED="$(git diff --cached --name-only -- "$@")"

if [ -n "$STAGED" ]; then
  # Safety net on top of .gitignore: never let a secrets file or a pasted key
  # reach GitHub, even if someone edits .gitignore or renames a file.
  BAD_FILES="$(echo "$STAGED" | grep -E '(^|/)(cloudrun-env\.yaml|\.env)$|Service Account Key|\.json\.key$' || true)"
  LEAKS="$(git diff --cached -U0 -- "$@" | grep -E '^\+' | grep -E 'sk-ant-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}|BEGIN (RSA |EC )?PRIVATE KEY|AKIA[0-9A-Z]{16}' || true)"
  if [ -n "$BAD_FILES" ] || [ -n "$LEAKS" ]; then
    git reset -q -- "$@"
    echo "DEPLOY SUCCEEDED, but GitHub publish ABORTED: something in the changes looks like a secret."
    [ -n "$BAD_FILES" ] && echo "  Secret-looking files: $BAD_FILES"
    [ -n "$LEAKS" ] && echo "  An added line looks like an API key / private key (not printed here)."
    echo "  Nothing was committed or pushed. Remove it, then run ./publish_to_github.sh again."
    exit 1
  fi

  echo "Changes to commit:"
  echo "$STAGED" | sed 's/^/  /' | head -40
  [ "$(echo "$STAGED" | wc -l)" -gt 40 ] && echo "  ...and $(( $(echo "$STAGED" | wc -l) - 40 )) more"

  if [ "${DRY_RUN:-0}" = "1" ]; then
    git reset -q -- "$@"
    echo "DRY_RUN=1 - would commit '$MSG' and push to $REMOTE/$BRANCH. Nothing changed."
    exit 0
  fi
  git commit -q -m "$MSG"
  echo "Committed: $MSG"
else
  echo "No new changes to commit."
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "DRY_RUN=1 - would push any unpushed commits on $BRANCH to $REMOTE. Nothing changed."
    exit 0
  fi
fi

if ! git push "$REMOTE" "$BRANCH"; then
  echo ""
  echo "DEPLOY SUCCEEDED, but the GitHub push FAILED (usually because a teammate pushed first)."
  echo "Fix:  git pull --rebase $REMOTE $BRANCH   then   git push $REMOTE $BRANCH"
  echo "(No need to redeploy - the service is already live with your change.)"
  exit 1
fi
echo "Published to $REMOTE/$BRANCH."
