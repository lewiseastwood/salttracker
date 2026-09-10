"""Validation tests anchored to figures the source documents state themselves.

Each expectation below is a number printed in a state contract document, not a
value produced by this code, so these tests detect extraction drift rather than
merely re-asserting current behaviour.

Run:  PYTHONPATH=src .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from salttracker.parsers import pa_estimates  # noqa: E402

VENDOR_CSV = os.path.join(ROOT, "data", "output", "salt_contracts_by_vendor.csv")
STATE_CSV = os.path.join(ROOT, "data", "output", "salt_contracts_by_state.csv")
RAW_CSV = os.path.join(ROOT, "data", "output", "salt_contracts_raw.csv")
RAW_ROOT = os.path.join(ROOT, "data", "raw")

pytestmark = pytest.mark.skipif(
    not os.path.exists(VENDOR_CSV),
    reason="dataset not built; run scripts/refresh.py --no-download",
)


@pytest.fixture(scope="module")
def vendor() -> pd.DataFrame:
    return pd.read_csv(VENDOR_CSV)


@pytest.fixture(scope="module")
def state() -> pd.DataFrame:
    return pd.read_csv(STATE_CSV)


@pytest.fixture(scope="module")
def raw() -> pd.DataFrame:
    return pd.read_csv(RAW_CSV)


def _one(df: pd.DataFrame, **kw) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for k, v in kw.items():
        m &= df[k] == v
    rows = df[m]
    assert len(rows) == 1, f"expected exactly 1 row for {kw}, got {len(rows)}"
    return rows.iloc[0]


# ---------------------------------------------------------------- Michigan
def test_compass_fy2026_matches_stated_schedule_totals(vendor):
    """Compass FY2026: the contract states 260,595 tons / $19,731,399.85.

    Those totals are printed on pages 5, 8, 11 and 13 of contract MA180000000787
    (62,100 + 40,800 + 58,200 + 99,495 tons and the matching extended prices).
    """
    row = _one(vendor, state="MI", fiscal_year=2026, vendor="Compass Minerals")
    assert row["contracted_tons"] == pytest.approx(260_595, abs=1)
    assert row["contract_value"] == pytest.approx(19_731_399.85, abs=1.0)
    assert row["weighted_avg_price"] == pytest.approx(75.72, abs=0.01)


def test_detroit_fy2027_matches_stated_schedule_totals(vendor):
    """Detroit FY2027 states 106,260 + 89,450 + 212,700 + 529,600 = 938,010 tons."""
    row = _one(vendor, state="MI", fiscal_year=2027, vendor="Detroit Salt")
    assert row["contracted_tons"] == pytest.approx(938_010, abs=1)


def test_compass_fy2027_matches_stated_schedule_totals(vendor):
    """Compass FY2027 states 103,975 + 53,830 + 94,150 + 155,350 = 407,305 tons."""
    row = _one(vendor, state="MI", fiscal_year=2027, vendor="Compass Minerals")
    assert row["contracted_tons"] == pytest.approx(407_305, abs=1)


def test_michigan_series_is_complete(state):
    """Michigan should cover every fiscal year from FY2022 through FY2027."""
    fys = sorted(state[state["state"] == "MI"]["fiscal_year"])
    assert fys == [2022, 2023, 2024, 2025, 2026, 2027]


def test_michigan_volumes_are_plausible(state):
    """Statewide contracted volume sits in the 0.8-1.7 Mt range in every year."""
    mi = state[state["state"] == "MI"]
    assert mi["contracted_tons"].between(700_000, 1_800_000).all()


# ------------------------------------------------------------ Pennsylvania
def test_pa_fy2027_statewide_simple_average(state):
    """PA's FY2027 packet advertises a statewide average of $92.47/ton.

    That headline is the unweighted mean of the 67 county prices, so it validates
    the price extraction independently of the volume weighting.
    """
    row = _one(state, state="PA", fiscal_year=2027)
    assert row["simple_avg_price"] == pytest.approx(92.47, abs=0.02)


def test_pa_fy2026_statewide_simple_average(state):
    """The FY2027 packet quotes the prior season's average as $88.21/ton."""
    row = _one(state, state="PA", fiscal_year=2026)
    assert row["simple_avg_price"] == pytest.approx(88.21, abs=0.02)


def test_pa_fy2027_covers_all_67_counties(state):
    row = _one(state, state="PA", fiscal_year=2027)
    assert row["n_counties"] == 67


def test_pa_fy2027_price_extremes_match_quick_facts(raw):
    """PA quick facts: high of $113.10 (Mifflin), low of $75.00 (Bucks/Montgomery/Philadelphia)."""
    pa = raw[(raw["state"] == "PA") & (raw["fiscal_year"] == 2027) & (raw["record_type"] == "award")]
    assert pa["price_per_ton"].max() == pytest.approx(113.10, abs=0.01)
    assert pa["price_per_ton"].min() == pytest.approx(75.00, abs=0.01)


# ------------------------------------------------------------------ shared
def test_weighted_price_is_value_over_volume(vendor):
    """The headline price must be total contract value divided by total volume."""
    v = vendor[(vendor["priced_tons"] > 0) & vendor["weighted_avg_price"].notna()]
    implied = v["contract_value"] / v["priced_tons"]
    assert (implied - v["weighted_avg_price"]).abs().max() < 0.01


def test_volume_shares_sum_to_one_per_state_year(vendor):
    # Shares are defined only over volume that names a supplier. Unattributed
    # tonnage (PA FY2022-FY2023) and price-only seasons are excluded.
    named = vendor[vendor["is_attributed"] & vendor["volume_share"].notna()]
    sums = named.groupby(["state", "fiscal_year"])["volume_share"].sum()
    assert (sums - 1.0).abs().max() < 1e-6


def test_unattributed_is_excluded_from_vendor_share(vendor):
    """Unattributed volume must not look like a supplier's market share."""
    ua = vendor[vendor["vendor"] == "Unattributed"]
    assert not ua.empty
    assert ua["is_attributed"].eq(False).all()
    assert ua["volume_share"].isna().all()


def test_unpriced_volume_does_not_deflate_state_price(state):
    """PA FY2022–FY2023 have tons but no award prices.

    A naive revenue ÷ all-tons average would read as $0/ton. The state rollup
    must leave the price blank instead of dividing by unpriced volume.
    """
    rows = state[(state["state"] == "PA") & (state["fiscal_year"].isin([2022, 2023]))]
    assert not rows.empty
    assert (rows["priced_tons"].fillna(0) == 0).all()
    assert rows["weighted_avg_price"].isna().all()
    assert (rows["contracted_tons"].fillna(0) > 0).all()


def test_no_duplicate_vendor_rows(vendor):
    assert not vendor.duplicated(subset=["state", "fiscal_year", "vendor"]).any()


def test_prices_are_in_a_sane_band(raw):
    award = raw[raw["record_type"] == "award"]
    assert award["price_per_ton"].between(15, 400).all()


def test_fy2027_flagged_as_current_cycle(vendor):
    """FY2027 is contracted but not yet delivered and must be marked as such."""
    assert vendor[vendor["fiscal_year"] == 2027]["is_current_cycle"].all()
    assert not vendor[vendor["fiscal_year"] < 2027]["is_current_cycle"].any()


# ------------------------------------------- Pennsylvania county tonnage
# Each estimates attachment prints its own statewide total, which is an
# independent check that every county row was read and none was double counted.
PA_ESTIMATE_TOTALS = {
    "PA_estimates_FY2022_6100053321.xlsx": (2022, 1_660_915),
    "PA_estimates_FY2026_6100063746.pdf": (2026, 1_533_471),
    "PA_estimates_FY2027_6100065611.pdf": (2027, 1_650_178),
}


@pytest.mark.parametrize("filename,expected", sorted(PA_ESTIMATE_TOTALS.items()))
def test_pa_estimates_match_document_totals(filename, expected):
    fy, total = expected
    path = os.path.join(RAW_ROOT, "PA", filename)
    if not os.path.exists(path):
        pytest.skip(f"{filename} not downloaded")
    frame = pa_estimates.parse(path)
    assert int(frame["fiscal_year"].iloc[0]) == fy
    assert len(frame) == 67, "Pennsylvania has 67 counties"
    assert abs(frame["cumulative_tons"].sum() - total) < 1


def test_pa_counties_have_one_supplier_per_season(raw):
    """DGS awards a county to a single supplier for the season."""
    pa = raw[(raw["state"] == "PA") & (raw["vendor"] != "Unattributed")]
    per_county = pa.groupby(["fiscal_year", "county"])["vendor"].nunique()
    assert per_county.max() <= 1


def test_pa_fy2024_matches_published_statewide_average(state):
    """The FY2024 packet prints 'Statewide Avg. -- $80.10/ton' on its map page."""
    row = state[(state["state"] == "PA") & (state["fiscal_year"] == 2024)]
    assert not row.empty
    assert abs(float(row["simple_avg_price"].iloc[0]) - 80.10) < 0.05


def test_pa_channel_is_not_hardcoded_blend(raw):
    """Lot rows no longer pretend PennDOT and COSTARS are one buyer."""
    assert "PennDOT / COSTARS" not in set(raw["channel"].dropna().astype(str))
    lot = raw[(raw["state"] == "PA") & (raw["fiscal_year"] == 2027)
              & (raw["county"] == "Allegheny") & (raw["record_type"] == "award")]
    assert len(lot) == 1
    row = lot.iloc[0]
    assert row["penndot_tons"] == pytest.approx(38_500, abs=1)
    assert row["costars_tons"] == pytest.approx(104_170, abs=1)
    assert row["agency_tons"] == pytest.approx(150, abs=1)
    assert pd.isna(row["purchasing_entity"]) or str(row["purchasing_entity"]).strip() == ""
    assert row["tons_basis"] == "committed_estimate"
    assert pd.isna(row["channel"]) or str(row["channel"]).strip() == ""


def test_pa_costars_member_allegheny_county_from_roster(raw):
    members = raw[(raw["state"] == "PA") & (raw["fiscal_year"] == 2027)
                   & (raw["record_type"] == "costars_member")]
    assert not members.empty
    row = members[members["purchasing_entity"] == "Allegheny County"]
    assert len(row) == 1
    assert row.iloc[0]["contracted_tons"] == pytest.approx(25_500, abs=1)
    assert row.iloc[0]["channel"] == "COSTARS"


def test_pa_does_not_infer_missing_county_governments(raw):
    names = set(raw[(raw["state"] == "PA") & (raw["fiscal_year"] == 2027)
                     & (raw["record_type"] == "costars_member")]["purchasing_entity"].dropna())
    assert "Washington County" not in names
    assert "Erie County" not in names


def test_pa_members_do_not_inflate_statewide_tons(state, raw):
    row = _one(state, state="PA", fiscal_year=2027)
    assert row["contracted_tons"] == pytest.approx(1_650_178, abs=1)
    members = raw[(raw["state"] == "PA") & (raw["fiscal_year"] == 2027)
                  & (raw["record_type"] == "costars_member")]
    assert members["contracted_tons"].sum() > 0


def test_michigan_rows_name_a_drop_point_and_channel(raw):
    mi = raw[raw["state"] == "MI"]
    assert not mi.empty
    assert mi["entity"].notna().all()
    assert mi["channel"].notna().all()


def test_fy2027_estimates_split_matches_attachment():
    path = os.path.join(RAW_ROOT, "PA", "PA_estimates_FY2027_6100065611.pdf")
    if not os.path.exists(path):
        pytest.skip("FY2027 estimates PDF not downloaded")
    frame = pa_estimates.parse(path)
    row = frame[frame["county"] == "Allegheny"].iloc[0]
    assert row["penndot_tons"] == pytest.approx(38_500, abs=1)
    assert row["costars_tons"] == pytest.approx(104_170, abs=1)
    assert row["agency_tons"] == pytest.approx(150, abs=1)
