"""Watch strip and coverage grid: latest run only, discrete fills, no invented copy."""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
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


def test_peak_season_four_days_is_not_stale_on_weekly_cadence():
    now = datetime(2026, 7, 10, 8, 0, 0)
    watch = {"last_run": "2026-07-06T07:15:00", "last_status": "ok"}
    strip = briefing.watch_strip(watch, [], now=now)
    assert strip["tone"] == "quiet"
    assert "has not run" not in strip["checked"]


def test_eleven_days_is_stale():
    now = datetime(2026, 7, 20, 8, 0, 0)
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


def test_strip_flags_auto_updated_unreviewed():
    watch = {
        "last_run": "2026-09-09T07:15:00",
        "last_status": "ok",
        "auto_updated_unreviewed": True,
    }
    strip = briefing.watch_strip(watch, [], now=NOW)
    assert strip["tone"] == "unreviewed"
    assert strip["headline"].startswith("Auto-updated, not yet reviewed.")
    assert "No new seasons" in strip["headline"]


def test_strip_failed_wins_over_unreviewed():
    watch = {
        "last_run": "2026-09-09T07:15:00",
        "last_status": "empty",
        "auto_updated_unreviewed": True,
    }
    strip = briefing.watch_strip(watch, [], now=NOW)
    assert strip["tone"] == "failed"
    assert "Auto-updated" not in strip["headline"]


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


RAW_CSV = os.path.join(ROOT, "data", "output", "salt_contracts_raw.csv")


@pytest.mark.skipif(not os.path.exists(RAW_CSV), reason="dataset not built")
def test_source_documents_catalog_has_urls():
    raw = pd.read_csv(RAW_CSV)
    catalog = briefing.source_documents(raw)
    assert not catalog.empty
    assert "PA_FY2027_COSTARS_6100065611.pdf" in set(catalog["source_doc"])
    fy27 = catalog[catalog["source_doc"] == "PA_FY2027_COSTARS_6100065611.pdf"].iloc[0]
    assert fy27["fiscal_year_from"] == 2027
    assert isinstance(fy27["source_url"], str) and fy27["source_url"].startswith("http")
    estimates = catalog[catalog["source_doc"] == "PA_estimates_FY2027_6100065611.pdf"]
    assert not estimates.empty
    est = estimates.iloc[0]
    assert isinstance(est["source_url"], str) and est["source_url"].startswith("http")
    assert "6100065611" in str(est["source_url"])
    assert "Solicitations.aspx?SID=6100065611" in str(est["source_page_url"])
    fy24 = catalog[catalog["source_doc"] == "PA_FY2024_COSTARS.pdf"].iloc[0]
    assert isinstance(fy24["source_url"], str) and fy24["source_url"].startswith("http")
    assert "2023-2024" in fy24["source_url"]
    cargill = catalog[catalog["source_doc"] == "791_snap2023-01.pdf"]
    assert not cargill.empty
    assert "CN3" in str(cargill.iloc[0]["source_label"])
    assert "2021/2022" in str(cargill.iloc[0]["source_label"])
    snaps = [
        "768_snap2023-01.pdf", "768_snap2025-04.pdf", "768_snap2026-05.pdf",
        "787_snap2023-01.pdf", "787_snap2025-04.pdf", "787_snap2026-05.pdf",
        "791_snap2023-01.pdf",
    ]
    for name in snaps:
        row = catalog[catalog["source_doc"] == name].iloc[0]
        assert str(row["source_url"]).startswith("https://web.archive.org/"), name
        assert "michigan.gov" in str(row["source_url"])
        assert str(row["source_page_url"]).startswith("https://web.archive.org/"), name
        assert briefing.file_markdown_link("Open PDF", row["source_url"]).startswith("[Open PDF](http")


def test_snap_urls_fill_from_wayback_when_csv_blank():
    raw = pd.read_csv(RAW_CSV)
    snaps = raw[raw["source_doc"] == "791_snap2023-01.pdf"].copy()
    snaps["source_url"] = None
    snaps["source_page_url"] = None
    cat = briefing.source_documents(snaps)
    row = cat.iloc[0]
    assert "180000000791" in str(row["source_url"])
    assert "web.archive.org" in str(row["source_page_url"])


def test_executive_source_table_hides_aliases_and_raw_urls():
    raw = pd.read_csv(RAW_CSV)
    cat = briefing.source_documents(raw)
    assert briefing.document_label("PA_FY2025_COSTARS.pdf") == "COSTARS FY2025 season contract"
    markup = briefing.executive_source_html(cat)
    assert ">PA_FY2025_COSTARS.pdf<" not in markup
    assert "COSTARS FY2025 season contract" in markup
    assert 'title="COSTARS FY2025 season contract"' in markup
    assert "791_snap" not in markup
    assert "MA180000000791" in markup
    fy25 = cat[cat["source_doc"] == "PA_FY2025_COSTARS.pdf"].iloc[0]
    assert fy25["source_url"] in markup
    export = briefing.source_table_for_export(cat)
    assert "PDF URL" in export.columns
    assert export[export["Filename"] == "791_snap2023-01.pdf"]["PDF URL"].iloc[0].startswith("http")


def test_volume_label_depends_on_which_states_are_in_view():
    assert briefing.volume_label(pd.DataFrame({"state": ["PA"]})) == "Estimated requirements"
    assert briefing.volume_label(pd.DataFrame({"state": ["MI"]})) == "Contracted tons"
    assert briefing.volume_label(pd.DataFrame({"state": ["MI", "PA"]})) == "Published tons"
    assert briefing.PA_VOLUME_NOTE.startswith("Pennsylvania tons are estimated")
    assert "unlike" in briefing.UNLIKE_SHARE_NOTE
    assert "FY2024" in briefing.PA_TONS_BASIS_NOTE
    assert "COSTARS packet" in briefing.PA_TONS_BASIS_NOTE
    assert "FY2025" in briefing.MI_CAPTURE_NOTE
    assert "mid-season amendment" in briefing.MI_CAPTURE_NOTE
    assert "option-year" in briefing.MI_CAPTURE_NOTE
    assert "post-amendment" in briefing.MI_CAPTURE_NOTE_SHORT
    assert "award-time" in briefing.MI_CAPTURE_NOTE_SHORT


def test_file_markdown_link():
    assert briefing.file_markdown_link(
        "791_snap2023-01.pdf",
        "https://web.archive.org/web/x/file.pdf",
    ) == "[791_snap2023-01.pdf](https://web.archive.org/web/x/file.pdf)"
    assert briefing.file_markdown_link("791_snap2023-01.pdf", None) == "791_snap2023-01.pdf"
    assert briefing.file_markdown_link("791_snap2023-01.pdf", "") == "791_snap2023-01.pdf"


def test_revision_caption_does_not_call_git():
    sha, href = briefing.revision_caption({
        "data_commit": "abc1234deadbeef",
        "change_report_path": "data/output/CHANGE_REPORT.txt",
    })
    assert sha == "abc1234deadbeef"
    assert href == (
        "https://github.com/lewiseastwood/salttracker/blob/main/"
        "data/output/CHANGE_REPORT.txt"
    )
    empty_sha, href2 = briefing.revision_caption({})
    assert empty_sha is None
    assert href2.endswith("data/output/CHANGE_REPORT.txt")
