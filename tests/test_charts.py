"""Chart helpers: quarterly view must not invent intra-year awards."""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

import charts  # noqa: E402

VENDOR_CSV = os.path.join(ROOT, "data", "output", "salt_contracts_by_vendor.csv")

pytestmark = pytest.mark.skipif(
    not os.path.exists(VENDOR_CSV),
    reason="dataset not built",
)


def test_quarter_view_keeps_annual_totals():
    vendor = pd.read_csv(VENDOR_CSV)
    named = vendor[vendor["is_attributed"]].copy()
    annual = charts._vendor_year(named, "weighted_avg_price")
    quarterly = charts._with_period(annual, "quarter")
    q1 = quarterly[quarterly["period"].str.endswith("Q1")]
    merged = annual.merge(
        q1[["vendor", "fiscal_year", "contracted_tons", "price"]],
        on=["vendor", "fiscal_year"],
        suffixes=("_a", "_q"),
    )
    pd.testing.assert_series_equal(
        merged["contracted_tons_a"].astype(float).reset_index(drop=True),
        pd.to_numeric(merged["contracted_tons_q"], errors="coerce").reset_index(drop=True),
        check_names=False,
    )
    later = quarterly[~quarterly["period"].str.endswith("Q1")]
    assert later["contracted_tons"].isna().all()
    assert later["price"].isna().all()


def test_comparison_charts_build():
    vendor = pd.read_csv(VENDOR_CSV)
    named = vendor[vendor["is_attributed"]]
    for grain in ("annual", "quarter"):
        fig = charts.volume_price_comparison(named, "weighted_avg_price", grain)
        assert len(fig.data) > 0
    fig = charts.vendor_bubbles(named)
    assert fig.data
    fig = charts.vendor_price_bars(named)
    assert fig.data
