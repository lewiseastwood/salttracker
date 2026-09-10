#!/usr/bin/env bash
# The scheduler is GitHub Actions (.github/workflows/refresh.yml), not launchd.
# A closed laptop cannot carry the June–August scrape. This script only
# removes a leftover LaunchAgent if one was installed earlier.
set -euo pipefail

LABEL="com.salttracker.refresh"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ "${1:-}" == "--remove" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed leftover $LABEL (schedule is GitHub Actions)"
  exit 0
fi

if [[ "${1:-}" == "--status" ]]; then
  echo "Scheduler: GitHub Actions (.github/workflows/refresh.yml)"
  echo "  Mondays 07:15 Eastern (11:15 UTC)"
  echo "  trigger: gh workflow run 'Weekly salt refresh'"
  if launchctl list 2>/dev/null | grep -qF "$LABEL"; then
    echo "Leftover LaunchAgent $LABEL is still loaded. Run: $0 --remove"
  fi
  exit 0
fi

echo "Do not install a local LaunchAgent. The scraper runs on GitHub Actions." >&2
echo "  workflow: .github/workflows/refresh.yml" >&2
echo "  trigger:  gh workflow run 'Weekly salt refresh'" >&2
echo "To drop a leftover LaunchAgent: $0 --remove" >&2
exit 1
