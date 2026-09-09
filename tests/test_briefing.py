"""Watch strip and coverage grid: latest run only, discrete fills, no invented copy."""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

import _briefing as briefing  # noqa: E402

VENDOR_CSV = os.path.join(ROOT, "data", "output", "salt_contracts_by_vendor.csv")

NOW = datetime(2026, 9, 9, 8, 16, 0)


def test_strip_is_quiet_when_latest_run_has_no_alerts():
    watch = {"last_run": "2026-09-09T07:15:00", "last_status": "ok"}
    alerts = [
        {"ts": "2026-09-08T19:06:20", "kind": "new-season", "state": "PA",
         "fiscal_year": 2027, "detail": "PA FY2027 appeared in the data for the first time"},
    ]
    strip = briefing.watch_strip(watch, alerts, now=NOW)
    assert strip["tone"] == "quiet"
    assert strip["headline"] == "No new seasons, suppliers, or documents."
    assert "9 Sep 2026, 07:15" in strip["checked"]
    assert "FY2027" not in strip["headline"]


def test_strip_lists_only_latest_run_and_detector_kinds():
    stamp = "2026-09-09T07:15:00"
    watch = {"last_run": stamp, "last_status": "ok"}
    alerts = [
        {"ts": "2026-09-01T00:00:00", "kind": "new-season", "state": "MI",
         "fiscal_year": 2026, "detail": "MI FY2026 appeared in the data for the first time"},
        {"ts": stamp, "kind": "new-supplier", "state": "PA",
         "vendor": "Riverside Construction Materials",
         "detail": "PA: Riverside Construction Materials not seen in earlier runs"},
        {"ts": stamp, "kind": "new-document",
         "detail": "newly published document: PA_Award.pdf"},
    ]
    strip = briefing.watch_strip(watch, alerts, now=NOW)
    assert strip["tone"] == "news"
    assert strip["headline"].startswith("2 changes since last refresh:")
    assert "PA: Riverside Construction Materials not seen in earlier runs" in strip["headline"]
    assert "newly published document: PA_Award.pdf" in strip["headline"]
    assert "FY2026" not in strip["headline"]
    assert "CN2" not in strip["headline"]


def test_strip_failed_is_not_nothing_new():
    watch = {"last_run": "2026-09-09T07:15:00", "last_status": "empty"}
    strip = briefing.watch_strip(watch, [], now=NOW)
    assert strip["tone"] == "failed"
    assert "parsed no rows" in strip["headline"]
    assert "No new seasons" not in strip["headline"]


def test_strip_parse_failed_is_not_quiet_week():
    stamp = "2026-08-12T07:15:00"
    watch = {"last_run": stamp, "last_status": "parse-failed"}
    alerts = [{
        "ts": stamp, "kind": "parse-failed", "state": "MI",
        "detail": "MI: MI_FY2028_x.pdf produced no contract rows — parser may not match this layout",
    }]
    strip = briefing.watch_strip(watch, alerts, now=datetime(2026, 8, 12, 8, 16, 0))
    assert strip["tone"] == "failed"
    assert "No new seasons" not in strip["headline"]
    assert "produced no contract rows" in strip["headline"]


def test_strip_discovery_failed_is_not_quiet_week():
    stamp = "2026-08-12T07:15:00"
    watch = {"last_run": stamp, "last_status": "discovery-failed"}
    alerts = [{
        "ts": stamp, "kind": "listing-empty", "state": "MI",
        "detail": "Michigan DTMB listing had no contract PDFs",
    }]
    strip = briefing.watch_strip(watch, alerts, now=datetime(2026, 8, 12, 8, 16, 0))
    assert strip["tone"] == "failed"
    assert "No new seasons" not in strip["headline"]
    assert "DTMB listing" in strip["headline"]


def test_strip_stale_keeps_quiet_headline_but_flags_age():
    watch = {"last_run": "2026-08-20T07:15:00", "last_status": "ok"}
    strip = briefing.watch_strip(watch, [], now=NOW)
    assert strip["tone"] == "stale"
    assert strip["headline"] == "No new seasons, suppliers, or documents."
    assert "has not run in" in strip["checked"]


def test_peak_season_stale_after_three_days():
    now = datetime(2026, 7, 10, 8, 0, 0)
    watch = {"last_run": "2026-07-06T07:15:00", "last_status": "ok"}
    strip = briefing.watch_strip(watch, [], now=now)
    assert strip["tone"] == "stale"
    assert "has not run in" in strip["checked"]


def test_offseason_four_days_is_not_stale():
    now = datetime(2026, 9, 9, 8, 0, 0)
    watch = {"last_run": "2026-09-05T07:15:00", "last_status": "ok"}
    strip = briefing.watch_strip(watch, [], now=now)
    assert strip["tone"] == "quiet"
    assert "has not run" not in strip["checked"]


def test_strip_missing_run_is_not_confirmation():
    strip = briefing.watch_strip({}, [], now=NOW)
    assert strip["tone"] == "missing"
    assert "No refresh recorded" in strip["headline"]


@pytest.mark.skipif(not os.path.exists(VENDOR_CSV), reason="dataset not built")
def test_coverage_discrete_fills_not_tonnage():
    vendor = pd.read_csv(VENDOR_CSV)
    pa = briefing.coverage_grid(vendor, "PA")
    labels = [r["label"] for r in pa["rows"]]
    assert "Volume, no award" in labels
    assert "Unattributed" not in labels
    assert "Detroit Salt" not in labels
    assert "Riverside Construction Materials" in labels

    def fill(vendor_name: str, fy: int) -> str:
        row = next(r for r in pa["rows"] if r["vendor"] == vendor_name)
        return next(c["fill"] for c in row["cells"] if c["fy"] == fy)

    assert fill("Unattributed", 2022) == "partial"
    assert fill("Unattributed", 2027) == "empty"
    assert fill("American Rock Salt", 2025) == "partial"
    assert fill("Riverside Construction Materials", 2027) == "both"
    assert fill("Riverside Construction Materials", 2026) == "empty"

    mi = briefing.coverage_grid(vendor, "MI")
    assert all(r["vendor"] != "Unattributed" for r in mi["rows"])
    detroit = next(r for r in mi["rows"] if r["vendor"] == "Detroit Salt")
    assert all(c["fill"] == "both" for c in detroit["cells"])
