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


def test_riverside_bar_is_fy2027_not_fy2024():
    vendor = pd.read_csv(VENDOR_CSV)
    pa = vendor[vendor["state"] == "PA"]
    fig = charts.volume_price_comparison(pa, "weighted_avg_price", "annual")
    placed = []
    for tr in fig.data:
        if getattr(tr, "type", None) != "bar":
            continue
        for x, y in zip(tr.x, tr.y):
            if y is not None and abs(float(y) - 332036) < 1:
                placed.append(str(x))
    assert placed == ["FY 2027"], placed


def test_facets_share_fiscal_year_slots():
    vendor = pd.read_csv(VENDOR_CSV)
    pa = vendor[vendor["state"] == "PA"]
    fig = charts.volume_price_comparison(pa, "weighted_avg_price", "annual")
    bar_xs = []
    for tr in fig.data:
        if getattr(tr, "type", None) == "bar":
            bar_xs.append([str(x) for x in tr.x])
    assert bar_xs, "expected bar traces"
    first = bar_xs[0]
    assert all(xs == first for xs in bar_xs)
    assert "FY 2025" in first
    assert "FY 2027" in first


def test_pa_facets_omit_detroit_and_lead_with_ars():
    vendor = pd.read_csv(VENDOR_CSV)
    pa = vendor[vendor["state"] == "PA"]
    fig = charts.volume_price_comparison(pa, "weighted_avg_price", "annual")
    titles = [a.text for a in fig.layout.annotations if a.text and not a.text.startswith("Bars")]
    assert "Detroit Salt" not in titles
    assert titles[0] == "American Rock Salt"
    assert "Riverside" in titles[1]


def test_comparison_volume_axis_fits_detroit():
    vendor = pd.read_csv(VENDOR_CSV)
    mi = vendor[(vendor["state"] == "MI") & vendor["is_attributed"]]
    fig = charts.volume_price_comparison(mi, "weighted_avg_price", "annual")
    bar_y = []
    for tr in fig.data:
        if tr.type == "bar":
            bar_y.extend([y for y in tr.y if y is not None])
    assert max(bar_y) > 900_000
    # Primary y-axis range must clear Detroit FY2027 tons (~938k).
    layout = fig.layout
    yaxis = layout.yaxis
    y_top = yaxis.range[1] if yaxis.range else max(bar_y)
    assert y_top >= max(bar_y)


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
