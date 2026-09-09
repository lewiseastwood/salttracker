#!/usr/bin/env bash
# Commit scrape outputs, including failures.
#
# GitHub disables scheduled workflows after 60 days with no repository
# activity. A red run that does not commit is the same as the scraper
# never running: the cron is disarmed by December and June has no alert.
# watch_state.json is the heartbeat: last_run changes every scrape, so
# this always has something to commit after a refresh attempt.
set -euo pipefail

WATCH=data/watch_state.json
if [[ ! -f "$WATCH" ]]; then
  echo "ERROR: $WATCH missing after scrape; persist would not keep the schedule alive" >&2
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
if [[ -d data/output ]]; then
  git add -- data/output
fi

if git diff --cached --quiet; then
  echo "ERROR: scrape produced no commit (watch_state unchanged). A stretch of red runs with nothing to push disables cron in 60 days." >&2
  exit 1
fi

status=$(python3 -c "import json; print(json.load(open('data/watch_state.json')).get('last_status', 'unknown'))")
git commit -m "Refresh ${status} $(date -u +%Y-%m-%d)"

if [[ "${PERSIST_PUSH:-1}" == "1" ]]; then
  git pull --rebase origin HEAD
  git push
fi
