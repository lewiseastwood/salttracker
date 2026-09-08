"""End-to-end check of Michigan extraction against the brief's Compass FY2026 figure.

The brief states that Compass Minerals' Michigan FY2026 contract, taken from
pages 4-13 of the MiDEAL PDF, covers 260,595 tons at $78.96/ton, and it defines
the price as total revenue divided by total volume.

These tests run from the raw PDF through the parser so the numbers are checked
against the document rather than against another part of this codebase.

Tonnage matches the brief exactly. The weighted price on those same rows is
$75.72, which is also what the contract's own schedule totals imply
($19,731,399.85 / 260,595). The brief's $78.96 is not reproduced by any named
average of the extracted prices and is left as an open question — it is not
asserted here.
"""
from __future__ import annotations

import glob
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from salttracker.parsers import michigan as mi_parser  # noqa: E402

# Stated in the brief and matched exactly by the extraction.
EXPECTED_TONS = 260_595
# Revenue and weighted price printed on the contract's own schedule totals,
# and equal to sum(tons * price) / sum(tons) on the extracted rows.
EXPECTED_WEIGHTED = 75.72
EXPECTED_REVENUE = 19_731_399.85
SCHEDULE_PAGES = range(4, 14)  # "page 4-page 13" in the brief
# 274 line items, all with positive tons. Locked so a phantom header/subtotal
# row cannot appear without failing this file.
EXPECTED_ROWS = 274

# The brief quotes $78.96/ton. That figure is not asserted. Closest named
# miss on the extracted prices is sum(prices)/273 = $78.97; no candidate
# reproduced $78.96 exactly. See README "Known gaps".


def _compass_pdf() -> str:
    """The MiDEAL contract for Compass Minerals, contract 180000000787."""
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "raw", "MI", "*.pdf"))):
        base = os.path.basename(path)
        if "787" in base and "2026" in base:
            return path
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "raw", "MI", "*787*.pdf"))):
        return path
    pytest.skip("Compass Minerals contract PDF not downloaded")


@pytest.fixture(scope="module")
def fy2026_rows():
    rows = [r for r in mi_parser.parse(_compass_pdf()) if r.fy == 2026]
    if not rows:
        pytest.skip("no FY2026 rows parsed from the Compass contract")
    return rows


def test_tonnage_matches_the_brief(fy2026_rows):
    """260,595 tons, straight from the PDF."""
    assert len(fy2026_rows) == EXPECTED_ROWS
    assert sum(r.tons for r in fy2026_rows if r.tons) == EXPECTED_TONS
    assert all(r.tons and r.tons > 0 for r in fy2026_rows)


def test_rows_come_from_the_schedule_pages(fy2026_rows):
    """The brief's page range is the whole of the FY2026 schedule and nothing else."""
    pages = {r.page for r in fy2026_rows}
    assert pages <= set(SCHEDULE_PAGES)
    in_range = [r for r in fy2026_rows if r.page in SCHEDULE_PAGES]
    assert sum(r.tons for r in in_range if r.tons) == EXPECTED_TONS


def test_weighted_average_is_revenue_over_volume(fy2026_rows):
    """The metric the brief defines: total revenue / total volume."""
    tons = sum(r.tons for r in fy2026_rows if r.tons)
    revenue = sum(r.tons * r.price for r in fy2026_rows if r.tons and r.price)
    assert abs(revenue - EXPECTED_REVENUE) < 1.0
    assert abs(revenue / tons - EXPECTED_WEIGHTED) < 0.01


def test_pipeline_reports_the_same_figures():
    """The published vendor rollup must agree with the raw parse above."""
    import pandas as pd
    csv = os.path.join(ROOT, "data", "output", "salt_contracts_by_vendor.csv")
    if not os.path.exists(csv):
        pytest.skip("dataset not built")
    v = pd.read_csv(csv)
    row = v[(v.state == "MI") & (v.fiscal_year == 2026) & (v.vendor == "Compass Minerals")]
    assert len(row) == 1
    assert abs(float(row["contracted_tons"].iloc[0]) - EXPECTED_TONS) < 1
    assert abs(float(row["weighted_avg_price"].iloc[0]) - EXPECTED_WEIGHTED) < 0.01
