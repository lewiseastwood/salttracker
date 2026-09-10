"""Provenance lookup joins local aliases to the downloaded filename by hash."""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from salttracker.pipeline import attach_provenance  # noqa: E402
from salttracker.sources import PA_EMKT  # noqa: E402

EST_HASH = "e0a69031842bd761f0832b5a08c6cdffc9621543dd3b72f8a2fcaf14a34feb04"
EST_URL = (
    f"{PA_EMKT}/FileDownload.aspx?file=6100065611/Solicitation_21.pdf"
    "&OriginalFileName=03 Attachment A.pdf"
)


def test_attach_provenance_matches_alias_by_sha256(tmp_path):
    manifest = [
        {
            "name": "PA_estimates_FYx_6100065611.pdf",
            "url": EST_URL,
            "sha256": EST_HASH,
            "fetched_at": "2026-09-08T19:05:23",
        }
    ]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    df = pd.DataFrame({
        "source_doc": ["PA_estimates_FY2027_6100065611.pdf"],
        "source_sha256": [EST_HASH],
        "source_url": [None],
        "retrieved_at": ["2026-09-08T18:47:31"],
    })
    out = attach_provenance(df, str(path))
    assert out.loc[0, "source_url"] == EST_URL
    assert out.loc[0, "source_page_url"] == f"{PA_EMKT}/Solicitations.aspx?SID=6100065611"
    assert out.loc[0, "source_sha256"] == EST_HASH
    assert out.loc[0, "retrieved_at"] == "2026-09-08T19:05:23"


def test_attach_provenance_records_wayback_snaps_without_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("[]")
    df = pd.DataFrame({
        "source_doc": ["768_snap2026-05.pdf"],
        "source_sha256": ["abcd"],
        "source_url": [None],
        "retrieved_at": ["2026-09-08T17:54:45"],
    })
    out = attach_provenance(df, str(path))
    url = out.loc[0, "source_url"]
    assert "web.archive.org/web/20260520035130id_" in url
    assert "180000000768" in url
    assert out.loc[0, "source_page_url"].startswith(
        "https://web.archive.org/web/20260520035130/"
    )
    assert out.loc[0, "retrieved_at"] == "2026-09-08T17:54:45"


def test_attach_provenance_leaves_unknown_blank(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("[]")
    df = pd.DataFrame({
        "source_doc": ["hand_added.pdf"],
        "source_sha256": ["ffff"],
        "source_url": [None],
        "retrieved_at": ["2026-09-08T12:00:00"],
    })
    out = attach_provenance(df, str(path))
    assert pd.isna(out.loc[0, "source_url"]) or not str(out.loc[0, "source_url"]).strip()
    assert pd.isna(out.loc[0, "source_page_url"]) or not str(
        out.loc[0, "source_page_url"]
    ).strip()
    assert out.loc[0, "retrieved_at"] == "2026-09-08T12:00:00"
