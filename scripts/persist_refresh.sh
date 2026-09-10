#!/usr/bin/env bash
# Commit a refresh proposal on the current branch. Never pushes.
#
# Publishing is a PR (scripts/open_refresh_pr.sh). Routine PRs auto-merge
# so the dashboard on main updates. This script must not `git push` origin
# main — that was the old auto-publish path.
set -euo pipefail

WATCH=data/watch_state.json
if [[ ! -f "$WATCH" ]]; then
  echo "ERROR: $WATCH missing after propose; nothing to commit" >&2
  exit 1
fi

stage() {
  local path=$1
  if [[ -e "$path" ]]; then
    git add -- "$path"
  fi
}

stage "$WATCH"
stage data/alerts.jsonl
stage data/refresh_log.jsonl
stage data/manifest.json
stage data/interim/pa_sid_scan.json
stage data/staging/CHANGE_REPORT.txt
stage data/staging/proposal.json
stage data/staging/classifications.json
if [[ -d data/archive ]]; then
  git add -f -- data/archive
fi
stage data/patterns/document_types.json
if [[ -d data/output ]]; then
  git add -- data/output
fi

if git diff --cached --quiet; then
  echo "ERROR: propose produced no commit (watch_state unchanged)." >&2
  exit 1
fi

status=$(python3 -c "import json; print(json.load(open('data/watch_state.json')).get('last_status', 'unknown'))")
label=$(python3 -c "import json,os; p='data/staging/proposal.json';
print(json.load(open(p)).get('label','routine') if os.path.exists(p) else 'routine')")
git commit -m "$(cat <<EOF
Refresh ${label} ${status} $(date -u +%Y-%m-%d)

EOF
)"
