"""Michigan overwrite: archive prior bytes and record a Wayback snapshot."""
from __future__ import annotations

import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from salttracker import archive  # noqa: E402


class _Resp:
    def __init__(self, text, headers, url=""):
        self.text = text
        self.headers = headers
        self.url = url


def test_archive_prior_bytes_names_file_by_sha(tmp_path):
    src = tmp_path / "180000000768.pdf"
    src.write_bytes(b"%PDF-old")
    digest = hashlib.sha256(b"%PDF-old").hexdigest()
    dest = archive.archive_prior_bytes(str(src), str(tmp_path / "archive"))
    assert dest.endswith(f"{digest}_180000000768.pdf")
    assert open(dest, "rb").read() == b"%PDF-old"


def test_save_page_now_records_identity_url():
    def fake_get(url, **kw):
        html = "Saved https://web.archive.org/web/20260910120000/https://www.michigan.gov/x.pdf"
        return _Resp(html, {"Content-Location": "/web/20260910120000/https://www.michigan.gov/x.pdf"}), 200

    out = archive.save_page_now("https://www.michigan.gov/x.pdf", http_get_fn=fake_get)
    assert out["ok"] is True
    assert out["timestamp"] == "20260910120000"
    assert "id_/" in out["wayback_url"]


def test_preserve_writes_index_and_source_url(tmp_path):
    src = tmp_path / "old.pdf"
    src.write_bytes(b"%PDF-prior")

    def fake_get(url, **kw):
        return _Resp(
            "https://web.archive.org/web/20260910120000id_/https://www.michigan.gov/live.pdf",
            {},
        ), 200

    rec = archive.preserve_michigan_overwrite(
        "https://www.michigan.gov/live.pdf",
        str(src),
        str(tmp_path / "archive"),
        str(tmp_path / "index.json"),
        http_get_fn=fake_get,
    )
    assert rec["save_ok"] is True
    assert rec["source_url"] == rec["wayback_url"]
    assert rec["prior_sha256"] == hashlib.sha256(b"%PDF-prior").hexdigest()
    assert os.path.isfile(rec["archived_path"])


def test_preserve_failed_save_has_no_wayback_source_url(tmp_path):
    src = tmp_path / "old.pdf"
    src.write_bytes(b"%PDF-prior")

    def fake_get(url, **kw):
        return _Resp("rate limited", {}), 429

    rec = archive.preserve_michigan_overwrite(
        "https://www.michigan.gov/live.pdf",
        str(src),
        str(tmp_path / "archive"),
        str(tmp_path / "index.json"),
        http_get_fn=fake_get,
    )
    assert rec["save_ok"] is False
    assert rec["source_url"] is None
    assert rec["wayback_url"] is None
    assert os.path.isfile(rec["archived_path"])
