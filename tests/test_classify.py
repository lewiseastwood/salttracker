"""Registry matching and classify JSON shape."""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from salttracker import classify, patterns  # noqa: E402


def test_registry_lists_known_shapes_and_the_unlike_interpretations():
    reg = patterns.load_registry()
    ids = {t["id"] for t in patterns.types(reg)}
    assert ids == {
        "mi_change_notice", "pa_estimates_attachment",
        "pa_costars_packet", "pa_change_notice",
    }
    blob = json.dumps(reg)
    assert "geography" in blob.lower() or "not buyers" in json.dumps(reg["interpretations"]).lower()
    assert "listing timestamps" in json.dumps(reg["interpretations"]) or "not seasons" in blob
    assert "FY2024" in blob and "committed_estimate" in blob


def test_known_pa_estimates_is_familiar():
    hit = patterns.match_type(
        "PA_estimates_FY2027_6100065611.pdf",
        "https://www.emarketplace.state.pa.us/FileDownload.aspx?file=6100065611/Solicitation_21.pdf",
        "Estimated Requirements by County PennDOT COSTARS",
        state="PA",
    )
    assert hit and hit["id"] == "pa_estimates_attachment"
    assert hit["tons_basis"] == "committed_estimate"


def test_unknown_shape_is_unfamiliar():
    hit = patterns.match_type("ohio_bid.xlsx", "https://example.com/salt.xlsx", "Ohio DOT salt bid", state="OH")
    assert hit is None
    result = classify.classify_document(
        "", name="ohio_bid.xlsx", url="https://example.com/salt.xlsx", state="OH",
    )
    assert result["familiar"] is False
    assert result["document_type"] == "unfamiliar"
    assert "document type" in result["undetermined"]
    assert result["confidence"] <= 0.4


def test_classify_extracts_printed_dates_from_cover_text(tmp_path, monkeypatch):
    monkeypatch.setattr(
        classify, "extract_cover_text",
        lambda path, pages=3: "CONTRACT CHANGE NOTICE 17\nEFFECTIVE DATE: July 29, 2025\n"
        "Expiration Date: August 31, 2026\n2025/2026 Road Salt",
    )
    result = classify.classify_document(
        "", name="768_snap2026-05.pdf",
        url="https://www.michigan.gov/dtmb/-/media/Project/Websites/dtmb/MiDEAL-Media/006/180000000768.pdf",
        state="MI",
    )
    assert result["familiar"] is True
    assert result["document_type"] == "change_notice"
    assert result["effective_date"] == "2025-07-29"
    assert result["expiration_date"] == "2026-08-31"
    assert result["fiscal_year"] == 2026
    assert result["columns_match"] is False
    assert "column signature" in result["undetermined"]


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


def test_api_cannot_promote_a_miss_to_a_made_up_type(monkeypatch):
    monkeypatch.setenv("SALTTRACKER_CLASSIFY_API_KEY", "sk-test")
    monkeypatch.setattr(
        classify, "extract_cover_text", lambda *a, **k: "Ohio salt schedule FY2028",
    )

    def fake_post(url, **kw):
        return _Resp({
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "registry_id": "made_up",
                        "document_type": "ohio_packet",
                        "familiar": True,
                        "confidence": 0.99,
                        "fiscal_year": 2028,
                    })
                }
            }]
        })

    result = classify.classify_document(
        "", name="ohio.pdf", url="https://example.com/ohio.pdf", state="OH",
        http_post=fake_post,
    )
    assert result["familiar"] is False
    assert result["document_type"] == "unfamiliar"
    assert result["fiscal_year"] == 2028
