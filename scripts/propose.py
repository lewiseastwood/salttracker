#!/usr/bin/env python3
"""Detect-and-propose a weekly refresh. Does not publish to main.

    python scripts/propose.py
    python scripts/propose.py --no-rebuild   # fetch + classify only
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from salttracker.propose import run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-scan", action="store_true",
                    help="skip the eMarketplace SID walk")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="do not parse or write proposed CSVs")
    args = ap.parse_args()
    proposal = run(
        ROOT,
        scan_emarketplace=not args.no_scan,
        rebuild=not args.no_rebuild,
    )
    print(f"Proposal label: {proposal['label']}")
    if proposal.get("assertions"):
        print("Assertions:")
        for a in proposal["assertions"]:
            print(f"  [{a['kind']}] {a['detail']}")
    if proposal.get("discovery_ok") and not args.no_rebuild:
        html = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "build_exec_dashboard.py")],
            cwd=ROOT,
        )
        if html.returncode != 0:
            print("HTML pack rebuild failed; CSVs in the PR are still the proposal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
