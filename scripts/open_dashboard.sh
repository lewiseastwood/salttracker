#!/usr/bin/env bash
# Open the executive dashboard in a browser.
# Prefers the live Streamlit app; falls back to the static HTML briefing.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/streamlit" ]]; then
  exec "$ROOT/.venv/bin/streamlit" run dashboard/app.py --server.headless true
fi
if [[ -f "$ROOT/data/output/Road_Salt_Contract_Tracker.html" ]]; then
  open "$ROOT/data/output/Road_Salt_Contract_Tracker.html"
  exit 0
fi
echo "Install the environment first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
exit 1
