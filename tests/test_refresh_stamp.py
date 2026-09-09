"""Crash path must stamp last_run so a throw is not a never-run."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A PDF the Michigan parser accepts but cannot turn into award rows — the
# FY2028-layout-changed case, without depending on a live DTMB file.
EMPTY_AWARD_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj
xref
0 4
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
trailer<</Size 4/Root 1 0 R>>
startxref
190
%%EOF
"""


def _load_refresh():
    path = os.path.join(ROOT, "scripts", "refresh.py")
    spec = importlib.util.spec_from_file_location("salttracker_refresh", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _isolate(refresh, tmp_path, monkeypatch, argv):
    monkeypatch.setattr(refresh, "STATE", str(tmp_path / "watch_state.json"))
    monkeypatch.setattr(refresh, "LOG", str(tmp_path / "refresh_log.jsonl"))
    monkeypatch.setattr(refresh, "ALERTS", str(tmp_path / "alerts.jsonl"))
    monkeypatch.setattr(refresh, "MANIFEST", str(tmp_path / "manifest.json"))
    monkeypatch.setattr(refresh, "RAW_ROOT", str(tmp_path / "raw"))
    monkeypatch.setattr(sys, "argv", argv)


def test_forced_throw_mid_scrape_writes_last_run(tmp_path, monkeypatch):
    refresh = _load_refresh()
    _isolate(refresh, tmp_path, monkeypatch, ["refresh.py", "--no-download"])

    def boom(*_a, **_k):
        raise RuntimeError("forced mid-scrape throw")

    monkeypatch.setattr(refresh, "build", boom)

    with pytest.raises(RuntimeError, match="forced mid-scrape throw"):
        refresh.main()

    written = json.loads((tmp_path / "watch_state.json").read_text())
    assert written["last_status"] == "error"
    assert written.get("last_run")
    assert os.path.exists(tmp_path / "watch_state.json")


def test_empty_dtmb_listing_is_discovery_failed_not_ok(tmp_path, monkeypatch):
    refresh = _load_refresh()
    _isolate(refresh, tmp_path, monkeypatch, ["refresh.py", "--no-archive", "--no-scan"])
    monkeypatch.setattr(refresh.sources, "fetch_michigan_listing", lambda: ([], "empty"))
    monkeypatch.setattr(refresh.sources, "fetch_pennsylvania_live", lambda **_k: ([], "ok"))
    monkeypatch.setattr(refresh.sources, "discover_michigan_archived", lambda: [])
    monkeypatch.setattr(refresh.sources, "download", lambda *_a, **_k: None)
    monkeypatch.setattr(refresh.sources, "write_manifest", lambda *_a, **_k: None)

    assert refresh.main() == 1
    written = json.loads((tmp_path / "watch_state.json").read_text())
    assert written["last_status"] == "discovery-failed"
    alerts = (tmp_path / "alerts.jsonl").read_text()
    assert "listing-empty" in alerts
    assert "No new seasons" not in alerts


def test_unfetched_dtmb_listing_is_discovery_failed(tmp_path, monkeypatch):
    refresh = _load_refresh()
    _isolate(refresh, tmp_path, monkeypatch, ["refresh.py", "--no-archive", "--no-scan"])
    monkeypatch.setattr(refresh.sources, "fetch_michigan_listing", lambda: ([], "unfetched"))
    monkeypatch.setattr(refresh.sources, "fetch_pennsylvania_live", lambda **_k: ([], "ok"))
    monkeypatch.setattr(refresh.sources, "discover_michigan_archived", lambda: [])
    monkeypatch.setattr(refresh.sources, "download", lambda *_a, **_k: None)
    monkeypatch.setattr(refresh.sources, "write_manifest", lambda *_a, **_k: None)

    assert refresh.main() == 1
    written = json.loads((tmp_path / "watch_state.json").read_text())
    assert written["last_status"] == "discovery-failed"
    assert "listing-unfetched" in (tmp_path / "alerts.jsonl").read_text()


def test_zero_row_award_pdf_is_parse_failed_not_ok(tmp_path, monkeypatch):
    refresh = _load_refresh()
    _isolate(refresh, tmp_path, monkeypatch, ["refresh.py", "--no-download"])
    (tmp_path / "watch_state.json").write_text(json.dumps({
        "coverage": {"MI": [2027], "PA": [2027]},
        "vendors": {"MI": ["Detroit Salt"], "PA": ["Morton"]},
        "documents": ["old.pdf"],
    }))

    result = SimpleNamespace(
        raw=pd.DataFrame({
            "state": ["MI", "PA"],
            "fiscal_year": [2027, 2027],
            "vendor": ["Detroit Salt", "Morton"],
        }),
        state_fy=pd.DataFrame({
            "state": ["MI", "PA"],
            "fiscal_year": [2027, 2027],
            "contracted_tons": [1.0, 1.0],
            "weighted_avg_price": [70.0, 80.0],
        }),
        parse_yields=[{
            "name": "MI_FY2028_unknown_270000000801.pdf",
            "state": "MI",
            "kind": "award",
            "n_rows": 0,
        }],
    )
    monkeypatch.setattr(refresh, "build", lambda *_a, **_k: result)
    monkeypatch.setattr(refresh, "export", lambda *_a, **_k: {"raw_csv": "x"})
    monkeypatch.setattr(refresh, "local_docs", lambda: ({}, [], []))

    assert refresh.main() == 1
    written = json.loads((tmp_path / "watch_state.json").read_text())
    assert written["last_status"] == "parse-failed"
    alerts = (tmp_path / "alerts.jsonl").read_text()
    assert "parse-failed" in alerts
    assert "produced no contract rows" in alerts


def test_listed_michigan_pdf_that_does_not_download_is_not_ok(tmp_path, monkeypatch):
    refresh = _load_refresh()
    _isolate(refresh, tmp_path, monkeypatch, ["refresh.py", "--no-archive", "--no-scan"])
    listed = [refresh.sources.Doc(
        state="MI", name="MI_FY2028_270000000801.pdf",
        url="https://www.michigan.gov/example/270000000801.pdf",
        contract_no="270000000801",
    )]
    monkeypatch.setattr(refresh.sources, "fetch_michigan_listing", lambda: (listed, "ok"))
    monkeypatch.setattr(refresh.sources, "fetch_pennsylvania_live", lambda **_k: ([], "ok"))
    monkeypatch.setattr(refresh.sources, "download", lambda *_a, **_k: None)
    monkeypatch.setattr(refresh.sources, "write_manifest", lambda *_a, **_k: None)

    assert refresh.main() == 1
    written = json.loads((tmp_path / "watch_state.json").read_text())
    assert written["last_status"] == "discovery-failed"
    assert "download-failed" in (tmp_path / "alerts.jsonl").read_text()


def test_empty_pa_listing_is_discovery_failed_even_when_michigan_ok(tmp_path, monkeypatch):
    refresh = _load_refresh()
    _isolate(refresh, tmp_path, monkeypatch, ["refresh.py", "--no-archive", "--no-scan"])
    listed = [refresh.sources.Doc(
        state="MI", name="MI_FY2028_270000000801.pdf",
        url="https://www.michigan.gov/example/270000000801.pdf",
        contract_no="270000000801",
    )]

    def fake_download(doc, *_a, **_k):
        doc.path = str(tmp_path / doc.name)
        return doc

    monkeypatch.setattr(refresh.sources, "fetch_michigan_listing", lambda: (listed, "ok"))
    monkeypatch.setattr(refresh.sources, "fetch_pennsylvania_live", lambda **_k: ([], "empty"))
    monkeypatch.setattr(refresh.sources, "download", fake_download)
    monkeypatch.setattr(refresh.sources, "write_manifest", lambda *_a, **_k: None)

    assert refresh.main() == 1
    written = json.loads((tmp_path / "watch_state.json").read_text())
    assert written["last_status"] == "discovery-failed"
    alerts = (tmp_path / "alerts.jsonl").read_text()
    assert "listing-empty" in alerts
    assert "Pennsylvania" in alerts
    assert "seed" in alerts.lower()


def test_parse_failure_end_to_end_strip_reads_parse_failed_not_stale(
        tmp_path, monkeypatch):
    """A zero-row award PDF stamps parse-failed; the dashboard strip reads that,
    not the previous ok timestamp (which in August would look like a quiet week
    until it aged into stale).
    """
    refresh = _load_refresh()
    _isolate(refresh, tmp_path, monkeypatch, ["refresh.py", "--no-download"])
    import salttracker.pipeline as pipeline
    monkeypatch.setattr(pipeline, "CACHE_DIR", str(tmp_path / "cache"))

    stale_ok = "2026-07-01T07:15:00"
    (tmp_path / "watch_state.json").write_text(json.dumps({
        "last_run": stale_ok,
        "last_status": "ok",
        "coverage": {"MI": [2027], "PA": [2027]},
    }))

    mi_dir = tmp_path / "raw" / "MI"
    mi_dir.mkdir(parents=True)
    (mi_dir / "MI_FY2028_new_layout.pdf").write_bytes(EMPTY_AWARD_PDF)

    assert refresh.main() == 1

    watch_path = tmp_path / "watch_state.json"
    alerts_path = tmp_path / "alerts.jsonl"
    written = json.loads(watch_path.read_text())
    assert written["last_status"] == "parse-failed"
    assert written["last_run"] != stale_ok

    sys.path.insert(0, os.path.join(ROOT, "dashboard"))
    import briefing  # noqa: E402

    # Peak season, more than three days after the old ok stamp. If persist
    # never replaced that stamp, the strip would be stale, not failed.
    strip = briefing.watch_strip(
        briefing.load_watch(watch_path),
        briefing.load_jsonl(alerts_path),
        now=datetime(2026, 8, 12, 8, 0, 0),
    )
    assert strip["tone"] == "failed"
    assert strip["tone"] != "stale"
    assert "No new seasons" not in strip["headline"]
    assert "has not run" not in strip["checked"]
    assert "produced no contract rows" in strip["headline"] or "did not parse" in strip["headline"]


def test_parse_failure_alerts_ignore_bidsheets():
    refresh = _load_refresh()
    alerts = refresh.parse_failure_alerts([
        {"name": "PA_bidsheet.pdf", "state": "PA", "kind": "bidsheet", "n_rows": 0},
        {"name": "MI_FY2028.pdf", "state": "MI", "kind": "award", "n_rows": 0},
        {"name": "MI_FY2027.pdf", "state": "MI", "kind": "award", "n_rows": 40},
    ])
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "parse-failed"
    assert "MI_FY2028.pdf" in alerts[0]["detail"]
