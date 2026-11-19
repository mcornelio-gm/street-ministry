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
# bucket folder : local folder
PAIRS=(Announcements:Documents/Announcements Stories:Documents/Stories Guides:Documents/Guides Data:data)
export CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity"

case "${1:-}" in
  pull)
    for p in "${PAIRS[@]}"; do
      b="${p%%:*}"; l="${p##*:}"
      echo "Pulling $BUCKET/$b -> $l"
      gcloud storage rsync "$BUCKET/$b" "$ROOT/$l" \
        --recursive --delete-unmatched-destination-objects \
        --exclude='.*\.tmp$' --project="$PROJECT"
    done
    echo
    echo "Done. Review and archive to GitHub:"
    echo "  git status Documents data && git add Documents data && git commit -m 'Sync live content' && git push"
    ;;
  push)
    echo "This OVERWRITES live Announcements/Stories/Guides/routes in $BUCKET with your local copies"
    echo "(files missing locally will be deleted from the live site)."
    read -r -p "Type 'push' to continue: " answer
    [[ "$answer" == "push" ]] || { echo "Cancelled."; exit 1; }
    for p in "${PAIRS[@]}"; do
      b="${p%%:*}"; l="${p##*:}"
      echo "Pushing $l -> $BUCKET/$b"
      gcloud storage rsync "$ROOT/$l" "$BUCKET/$b" \
        --recursive --delete-unmatched-destination-objects \
        --exclude='.*\.tmp$' --project="$PROJECT"
    done
    echo "Done. The live wiki refreshes on the next save in /admin or the next restart/deploy."
    ;;
  *)
    sed -n '2,8p' "$0"
    exit 1
    ;;
esac
