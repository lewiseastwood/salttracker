"""Column signatures: type name is not enough if the headers drifted."""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from salttracker import classify, columns, patterns  # noqa: E402

EST27 = os.path.join(ROOT, "data", "raw", "PA", "PA_estimates_FY2027_6100065611.pdf")
EST26 = os.path.join(ROOT, "data", "raw", "PA", "PA_estimates_FY2026_6100063746.pdf")

FY27_HEADERS = [
    "District", "Lot #", "County", "Cumulative",
    "PENNDOT Initial Fill", "PENNDOT Balance of Season Winter Fill",
    "PENNDOT Total Quantity", "COSTARS Total Quantity",
    "Non-PENNDOT State Agencies Total Quantity",
]
FY26_HEADERS = [
    "District", "Lot #", "County", "Cumulative",
    "PENNDOT Initial Fill", "PENNDOT Balance of Season Winter Fill",
    "PENNDOT Total Quantity",
    "COSTARS Initial Fill", "COSTARS Balance of Season Winter Fill",
    "COSTARS Total Quantity",
    "Non-PENNDOT State Agencies Total Quantity",
]
ROSTER_HEADERS = [
    "Organization Name", "Member Category", "State County",
    "Delivery Address", "Stockpile Capacity", "Total Tons Required",
]


def _estimates_entry():
    return next(
        t for t in patterns.types(patterns.load_registry())
        if t["id"] == "pa_estimates_attachment"
    )


def test_fy2027_nine_col_matches_and_fy2026_does_not():
    entry = _estimates_entry()
    fy27 = columns.header_set(FY27_HEADERS)
    fy26 = columns.header_set(FY26_HEADERS)
    roster = columns.header_set(ROSTER_HEADERS)
    assert columns.match_columns([fy27], entry)["columns_match"] is True
    assert columns.match_columns([fy27, roster], entry)["columns_match"] is True
    assert columns.match_columns([fy26], entry)["columns_match"] is False
    assert columns.match_columns([fy26, roster], entry)["columns_match"] is False


@pytest.mark.skipif(not os.path.isfile(EST27), reason="FY2027 estimates PDF not on disk")
def test_real_fy2027_estimates_pdf_matches_signature():
    result = classify.classify_document(
        EST27, name=os.path.basename(EST27), state="PA",
    )
    assert result["familiar"] is True
    assert result["document_type"] == "estimates_attachment"
    assert result["columns_match"] is True
    assert result["undetermined"] == []


@pytest.mark.skipif(not os.path.isfile(EST26), reason="FY2026 estimates PDF not on disk")
def test_real_fy2026_estimates_pdf_is_column_drift():
    result = classify.classify_document(
        EST26, name=os.path.basename(EST26), state="PA",
    )
    assert result["familiar"] is True
    assert result["document_type"] == "estimates_attachment"
    assert result["columns_match"] is False
    assert "column signature" in result["undetermined"]
