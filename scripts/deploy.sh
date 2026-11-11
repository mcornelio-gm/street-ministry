#!/usr/bin/env bash
# One-command release: sync live content -> test -> commit & push to GitHub -> deploy.
#
#   scripts/deploy.sh                 # full release
#   scripts/deploy.sh --no-deploy     # just archive live content to GitHub
#
# Live Announcements/Stories (edited in /admin) are pulled from the bucket first, so
# GitHub always gets a current copy whenever we release.
set -euo pipefail

PROJECT="${GCP_PROJECT:-resonate-street-ministry}"
REGION="${GCP_REGION:-us-west1}"
SERVICE="${SERVICE:-street-ministry}"
BUCKET="${CONTENT_BUCKET:-gs://resonate-street-ministry-content}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTENT_DIRS=(Documents/Announcements Documents/Stories)
DO_DEPLOY=1
[[ "${1:-}" == "--no-deploy" ]] && DO_DEPLOY=0
export CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity"

cd "$ROOT"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# 1. Pull live content (protect unsaved local content edits)
step "Pulling live content from $BUCKET"
if [[ -n "$(git status --porcelain -- "${CONTENT_DIRS[@]}")" ]]; then
  echo "You have uncommitted local changes in Announcements/Stories:"
  git status --short -- "${CONTENT_DIRS[@]}"
  echo
  echo "Pulling would overwrite them with the live versions. Either:"
  echo "  - send them to the live site first:  scripts/sync_content.sh push"
  echo "  - or discard them:                   git checkout -- ${CONTENT_DIRS[*]}"
  exit 1
fi
if gcloud storage ls "$BUCKET" --project="$PROJECT" >/dev/null 2>&1; then
  "$ROOT/scripts/sync_content.sh" pull >/dev/null
  .venv/bin/mkdocs build -q 2>/dev/null || true   # refresh generated index pages
  echo "Live content pulled."
else
  echo "Bucket not found; skipping pull (site not yet using bucket storage)."
fi

# 2. Tests
step "Running tests"
.venv/bin/pytest -q

# 3. Commit & push to GitHub
step "Committing to git"
git add "${CONTENT_DIRS[@]}" Documents/Routes/index.md 2>/dev/null || true
if ! git diff --cached --quiet -- "${CONTENT_DIRS[@]}"; then
  git commit -q -m "Sync live content from site ($(date +%Y-%m-%d))" -- "${CONTENT_DIRS[@]}" Documents/Routes/index.md
  echo "Committed live content."
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo
  echo "Other uncommitted changes:"
  git status --short
  read -r -p "Commit these too? Enter a message (or leave blank to skip): " msg
  if [[ -n "$msg" ]]; then
    git add -A
    git commit -q -m "$msg"
  fi
fi
git push -q origin HEAD
echo "Pushed to GitHub."

# 4. Deploy
if [[ $DO_DEPLOY -eq 1 ]]; then
  step "Deploying to Cloud Run ($SERVICE, $REGION)"
  gcloud run deploy "$SERVICE" --source . --project="$PROJECT" --region="$REGION" --quiet
fi

step "Done"

