#!/usr/bin/env bash
# Sync editable content (Announcements, Stories) between the live bucket and Documents/.
#
#   scripts/sync_content.sh pull   # bucket -> Documents/   (then commit & push to GitHub)
#   scripts/sync_content.sh push   # Documents/ -> bucket   (overwrites LIVE content; asks first)
#
# The bucket is the master copy once the site is live; /admin edits only exist there
# until you pull. Bucket versioning keeps prior versions of every file.
set -euo pipefail

PROJECT="${GCP_PROJECT:-resonate-street-ministry}"
BUCKET="${CONTENT_BUCKET:-gs://resonate-street-ministry-content}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FOLDERS=(Announcements Stories)
export CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity"

case "${1:-}" in
  pull)
    for f in "${FOLDERS[@]}"; do
      echo "Pulling $BUCKET/$f -> Documents/$f"
      gcloud storage rsync "$BUCKET/$f" "$ROOT/Documents/$f" \
        --recursive --delete-unmatched-destination-objects \
        --exclude='.*\.tmp$' --project="$PROJECT"
    done
    echo
    echo "Done. Review and archive to GitHub:"
    echo "  git status Documents && git add Documents && git commit -m 'Sync live content' && git push"
    ;;
  push)
    echo "This OVERWRITES live Announcements/Stories in $BUCKET with your local copies"
    echo "(files missing locally will be deleted from the live site)."
    read -r -p "Type 'push' to continue: " answer
    [[ "$answer" == "push" ]] || { echo "Cancelled."; exit 1; }
    for f in "${FOLDERS[@]}"; do
      echo "Pushing Documents/$f -> $BUCKET/$f"
      gcloud storage rsync "$ROOT/Documents/$f" "$BUCKET/$f" \
        --recursive --delete-unmatched-destination-objects --project="$PROJECT"
    done
    echo "Done. The live wiki refreshes on the next save in /admin or the next restart/deploy."
    ;;
  *)
    sed -n '2,8p' "$0"
    exit 1
    ;;
esac
