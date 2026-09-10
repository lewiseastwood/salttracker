"""Contract downloads fetch bytes and report failed URLs instead of dropping them."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

import _downloads as downloads  # noqa: E402


class _Resp:
    def __init__(self, content: bytes):
        self.content = content


def test_load_prefers_local_file(tmp_path):
    pa = tmp_path / "PA"
    pa.mkdir()
    (pa / "PA_FY2027.pdf").write_bytes(b"%PDF-local")
    blob, err = downloads.load_source_bytes(
        "PA_FY2027.pdf", "PA", "https://example.com/missing.pdf", raw_dir=str(tmp_path),
    )
    assert err is None
    assert blob == b"%PDF-local"


def test_load_reports_failed_url(monkeypatch, tmp_path):
    monkeypatch.setattr(downloads, "http_get", lambda url: (None, 404))
    blob, err = downloads.load_source_bytes(
        "gone.pdf", "MI", "https://example.com/gone.pdf", raw_dir=str(tmp_path),
    )
    assert blob is None
    assert "https://example.com/gone.pdf" in err
    assert "404" in err


def test_load_reports_html_body_as_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        downloads, "http_get",
        lambda url: (_Resp(b"<!DOCTYPE html><html>nope</html>"), 200),
    )
    blob, err = downloads.load_source_bytes(
        "trap.pdf", "PA", "https://example.com/trap.pdf", raw_dir=str(tmp_path),
    )
    assert blob is None
    assert "https://example.com/trap.pdf" in err


def test_zip_keeps_failures_and_successful_files(monkeypatch, tmp_path):
    pa = tmp_path / "PA"
    pa.mkdir()
    (pa / "ok.pdf").write_bytes(b"%PDF-ok")

    def fake_get(url):
        return None, 503

    monkeypatch.setattr(downloads, "http_get", fake_get)
    blob, failures = downloads.zip_sources(
        [
            {"source_doc": "ok.pdf", "state": "PA", "source_url": "https://example.com/ok.pdf"},
            {"source_doc": "miss.pdf", "state": "MI", "source_url": "https://example.com/miss.pdf"},
            {"source_doc": "none.pdf", "state": "PA", "source_url": ""},
        ],
        raw_dir=str(tmp_path),
    )
    assert blob is not None
    assert any("https://example.com/miss.pdf" in f and "503" in f for f in failures)
    assert any("none.pdf: no published URL" in f for f in failures)
