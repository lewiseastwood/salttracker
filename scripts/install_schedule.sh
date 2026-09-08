#!/usr/bin/env bash
# Install a launchd job that refreshes the salt contract dataset on a schedule.
#
# Road salt contracts for the next winter are awarded in the July-August window,
# so the job runs daily during June-August (when new awards appear) and weekly
# for the rest of the year (to pick up change notices amending live contracts).
#
#   ./scripts/install_schedule.sh          install / reinstall
#   ./scripts/install_schedule.sh --remove uninstall
#   ./scripts/install_schedule.sh --status show state and recent log lines

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.salttracker.refresh"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
PYTHON="$ROOT/.venv/bin/python"

if [[ "${1:-}" == "--remove" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

if [[ "${1:-}" == "--status" ]]; then
  launchctl list | grep -F "$LABEL" || echo "$LABEL is not loaded"
  echo "--- recent runs ---"
  tail -n 15 "$ROOT/data/refresh_log.jsonl" 2>/dev/null || echo "(no runs recorded yet)"
  echo "--- newly published contract data ---"
  tail -n 15 "$ROOT/data/alerts.jsonl" 2>/dev/null || echo "(nothing new detected yet)"
  exit 0
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing virtualenv at $PYTHON. Create it first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data/logs"

# Daily at 07:15 in June, July and August; Mondays at 07:15 otherwise.
build_calendar() {
  for month in 6 7 8; do
    cat <<XML
    <dict>
      <key>Month</key><integer>${month}</integer>
      <key>Hour</key><integer>7</integer>
      <key>Minute</key><integer>15</integer>
    </dict>
XML
  done
  cat <<'XML'
    <dict>
      <key>Weekday</key><integer>1</integer>
      <key>Hour</key><integer>7</integer>
      <key>Minute</key><integer>15</integer>
    </dict>
XML
}

cat > "$PLIST" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>WorkingDirectory</key><string>${ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONPATH</key><string>${ROOT}/src</string></dict>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${ROOT}/scripts/refresh.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
$(build_calendar)
  </array>
  <key>StandardOutPath</key><string>${ROOT}/data/logs/refresh.out.log</string>
  <key>StandardErrorPath</key><string>${ROOT}/data/logs/refresh.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
XML

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Installed $LABEL"
echo "  daily 07:15 during June-August (new award season)"
echo "  Mondays 07:15 the rest of the year (change notices)"
echo "  logs: $ROOT/data/logs/refresh.out.log"
echo "  status: ./scripts/install_schedule.sh --status"
