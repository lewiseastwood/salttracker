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


def test_load_no_url_with_page_is_not_a_fetch_failure(tmp_path):
    blob, err = downloads.load_source_bytes(
        "alias.pdf", "PA", "", raw_dir=str(tmp_path),
        page_url="https://example.com/solicitation",
    )
    assert blob is None
    assert "no stable public file URL" in err
    assert "https://example.com/solicitation" in err


def test_load_neither_url_nor_page_is_provenance_unknown(tmp_path):
    blob, err = downloads.load_source_bytes(
        "mystery.pdf", "MI", "", raw_dir=str(tmp_path),
    )
    assert blob is None
    assert err == "mystery.pdf: provenance unknown"


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
            {
                "source_doc": "pageonly.pdf", "state": "PA", "source_url": "",
                "source_page_url": "https://example.com/landing",
            },
        ],
        raw_dir=str(tmp_path),
    )
    assert blob is not None
    assert any("https://example.com/miss.pdf" in f and "503" in f for f in failures)
    assert any("none.pdf: provenance unknown" in f for f in failures)
    assert any(
        "pageonly.pdf: no stable public file URL; page https://example.com/landing" in f
        for f in failures
    )
