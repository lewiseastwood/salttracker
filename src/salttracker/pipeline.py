"""Build the road-salt contract dataset: parse -> normalize -> deduplicate -> export.

Definitions used throughout (per road-salt industry convention):

* Fiscal year runs Oct 1 - Sep 30 and is named for the calendar year it ends in,
  so the 2025/2026 winter season is FY2026.
* Contracted volume is the tonnage a supplier is awarded/committed to under the
  contract, summed across all programs (Michigan early fill + seasonal back-up).
* Contracted price is the weighted-average price per ton, computed as total
  contract revenue divided by total contracted volume. This is verified against
  Michigan's own stated schedule totals; note it differs from the simple
  average of posted prices that states publish as a headline (e.g. PA's
  "statewide average"), which ignores volume concentration.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import os
import pickle
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .parsers import michigan as mi_parser
from .parsers import pa_estimates as pa_est_parser
from .parsers import pennsylvania as pa_parser
from .sources import MI_CONTRACT_VENDOR

# FY2027 is the live contracting cycle at time of build; earlier years are settled.
HISTORICAL_FY_RANGE = (2022, 2026)
CURRENT_FY = 2027

# Volume published without a nameable supplier. Held as a sentinel vendor rather
# than a null so the tonnage survives grouping, and excluded from market share.
UNATTRIBUTED = "Unattributed"

RAW_COLUMNS = [
    "state", "fiscal_year", "season", "is_current_cycle", "vendor", "county",
    "program", "channel", "fill_type", "delivery_terms", "price_unit",
    "contracted_tons", "price_per_ton", "extended_value",
    "price_costars", "contract_no", "record_type", "measure_basis", "tons_basis",
    # Provenance: enough to re-find and re-verify any single row at its source.
    "source_doc", "source_page", "source_url", "source_sha256", "retrieved_at",
]

# What a row actually asserts. Kept separate from record_type so that a consumer
# can filter on "rows with both measures" without knowing parser vocabulary.
MEASURE_PRICE_AND_VOLUME = "price+volume"
MEASURE_PRICE_ONLY = "price_only"
MEASURE_VOLUME_ONLY = "volume_only"


@dataclass
class BuildResult:
    raw: pd.DataFrame
    state_fy_vendor: pd.DataFrame
    state_fy: pd.DataFrame
    diagnostics: pd.DataFrame


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
CACHE_DIR = os.path.join("data", "interim", "parse_cache")


def _cache_key(path: str, tag: str) -> str:
    st = os.stat(path)
    sig = f"{os.path.basename(path)}-{st.st_size}-{int(st.st_mtime)}-{tag}"
    return os.path.join(CACHE_DIR, hashlib.sha1(sig.encode()).hexdigest() + ".pkl")


def parse_michigan_docs(paths: dict[str, str | None]) -> pd.DataFrame:
    """Parse Michigan PDFs. ``paths`` maps file path -> default vendor (or None).

    These contracts run to hundreds of pages, so parsed output is cached against
    the file's size and mtime.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    rows = []
    for path, vendor in paths.items():
        if not os.path.exists(path):
            continue
        if vendor is None:
            for cno, v in MI_CONTRACT_VENDOR.items():
                if cno in os.path.basename(path):
                    vendor = v
                    break

        # The cache is keyed on the file only; the vendor default is applied
        # afterwards so vendor-mapping changes don't force a re-parse.
        cache = _cache_key(path, "mi-v4")
        if os.path.exists(cache):
            with open(cache, "rb") as fh:
                parsed = pickle.load(fh)
        else:
            parsed = mi_parser.parse(path)
            with open(cache, "wb") as fh:
                pickle.dump(parsed, fh)

        if vendor is None:
            vendor = mi_parser.detect_vendor(path)

        parsed = mi_parser.select_current(parsed)
        for r in parsed:
            if r.vendor is None:
                r.vendor = vendor
            rows.append(dict(
                state="MI", fiscal_year=r.fy, vendor=r.vendor, county=r.county,
                program=r.program, channel=r.channel, contracted_tons=r.tons,
                price_per_ton=r.price, extended_value=r.extended,
                price_costars=None, contract_no=r.contract_no,
                record_type="award", source_doc=r.source_doc, source_page=r.page,
                tons_basis=r.tons_source, change_notice=r.change_notice,
                entity=r.entity,
            ))
    return pd.DataFrame(rows)


def parse_pennsylvania_docs(paths: list[str]) -> pd.DataFrame:
    rows = []
    for path in paths:
        if not os.path.exists(path):
            continue
        for r in pa_parser.parse(path):
            rows.append(dict(
                state="PA", fiscal_year=r.fy, vendor=r.vendor, county=r.county,
                program="Statewide Contract", channel="PennDOT / COSTARS",
                contracted_tons=r.tons, price_per_ton=r.price,
                extended_value=(r.tons * r.price) if (r.tons and r.price) else None,
                price_costars=r.price_costars, contract_no=r.contract_no,
                record_type=r.record_type, source_doc=r.source_doc,
                source_page=r.page, tons_basis="printed", change_notice=None,
                entity=None,
            ))
    return pd.DataFrame(rows)


def parse_pa_estimates(paths: list[str]) -> pd.DataFrame:
    """Collect PennDOT's published county tonnage, newest document per season."""
    frames = []
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            frame = pa_est_parser.parse(path)
        except Exception:
            continue
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    # A season can be restated by a later supplemental solicitation; the
    # document covering the most counties is the statewide one.
    order = combined.groupby(["fiscal_year", "source_doc"])["county"].transform("size")
    combined = combined.assign(_n=order).sort_values("_n", ascending=False)
    return (combined.drop_duplicates(subset=["fiscal_year", "county"])
                    .drop(columns="_n").reset_index(drop=True))


def resolve_pa_awards(df: pd.DataFrame) -> pd.DataFrame:
    """Reduce Pennsylvania to one awarded supplier per county per season.

    DGS awards each county to a single supplier for the season, but a county's
    price is restated in several places: the COSTARS packet for that season and
    again in every later change notice that renews the contract. Keeping all of
    them would both double-count tonnage and, where a county changes hands,
    credit it to two suppliers at once.

    The COSTARS season packet is preferred because it is the document that
    states who actually holds each county; change notices are used only to fill
    seasons for which no packet was published.
    """
    if df.empty or "state" not in df:
        return df
    pa = df[df["state"].eq("PA")].copy()
    if pa.empty:
        return df
    rest = df[~df["state"].eq("PA")]

    doc = pa["source_doc"].fillna("").str.lower()
    # Prefer a season packet over a change notice, and a priced row over a bare one.
    pa["_rank"] = (doc.str.contains("costars").astype(int) * 4
                   + pa["price_per_ton"].notna().astype(int) * 2
                   + pa["contracted_tons"].notna().astype(int))
    pa = pa.sort_values("_rank", ascending=False)
    pa = pa.drop_duplicates(subset=["fiscal_year", "county"]).drop(columns="_rank")
    return pd.concat([pa, rest], ignore_index=True)


def attach_pa_volume(df: pd.DataFrame, estimates: pd.DataFrame) -> pd.DataFrame:
    """Give Pennsylvania rows a tonnage using PennDOT's county estimates.

    Pennsylvania awards a price per county but publishes tonnage separately, so
    a county's estimated demand is assigned to whichever supplier holds that
    county for the season. That makes PA volume and weighted price comparable to
    Michigan, where both are printed on one schedule.

    Seasons whose counties were never priced in a document we hold still carry a
    statewide tonnage, so those are kept as unattributed volume rather than
    being dropped.
    """
    if estimates.empty:
        return df

    tons = (estimates.set_index(["fiscal_year", "county"])["cumulative_tons"]
                     .rename("_est_tons"))
    out = df.copy()
    is_pa = out["state"].eq("PA")
    if is_pa.any():
        keys = pd.MultiIndex.from_arrays([out.loc[is_pa, "fiscal_year"],
                                          out.loc[is_pa, "county"]])
        matched = tons.reindex(keys).to_numpy()
        # The estimate is preferred over any tonnage printed in the season
        # packet. Packet tonnage counts only the COSTARS members who registered,
        # while the estimate is the county's whole contracted requirement, so
        # mixing the two would make the year-on-year series inconsistent.
        current = out.loc[is_pa, "contracted_tons"].to_numpy(dtype=float)
        use_est = ~pd.isna(matched)
        out.loc[is_pa, "contracted_tons"] = np.where(use_est, matched, current)
        priced = out.loc[is_pa, "price_per_ton"].notna().to_numpy()
        rt = out.loc[is_pa, "record_type"].to_numpy(dtype=object).copy()
        basis = out.loc[is_pa, "tons_basis"].to_numpy(dtype=object).copy()
        rt[use_est & priced] = "award"
        basis[use_est] = "penndot_estimate"
        out.loc[is_pa, "record_type"] = rt
        out.loc[is_pa, "tons_basis"] = basis
        out.loc[is_pa, "extended_value"] = (out.loc[is_pa, "contracted_tons"]
                                            * out.loc[is_pa, "price_per_ton"])

    covered = set(out.loc[out["state"].eq("PA") & out["contracted_tons"].notna(), "fiscal_year"])
    extra = estimates[~estimates["fiscal_year"].isin(covered)]
    if extra.empty:
        return out
    unattributed = pd.DataFrame({
        "state": "PA",
        "fiscal_year": extra["fiscal_year"].to_numpy(),
        "vendor": UNATTRIBUTED,
        "county": extra["county"].to_numpy(),
        "program": "Statewide Contract",
        "channel": "PennDOT / COSTARS",
        "contracted_tons": extra["cumulative_tons"].to_numpy(),
        "price_per_ton": None,
        "extended_value": None,
        "record_type": "volume_only",
        "source_doc": extra["source_doc"].to_numpy(),
        "source_page": extra["source_page"].to_numpy(),
        "tons_basis": "penndot_estimate",
    })
    return pd.concat([out, unattributed], ignore_index=True)


# --------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------
def dedupe_schedules(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one source document per (state, FY, vendor, program, channel).

    A vendor's schedule is frequently reprinted inside another vendor's contract
    packet (Michigan's FY2027 Compass PDF also contains Detroit's seasonal
    schedule), which would double-count volume. The document reporting the most
    rows for a schedule is treated as authoritative, since a partial reprint is
    never longer than the original.
    """
    if df.empty:
        return df
    award = df[df["record_type"] == "award"].copy()
    other = df[df["record_type"] != "award"].copy()

    key = ["state", "fiscal_year", "vendor", "program", "channel"]
    # Null keys must survive grouping and matching, so they are mapped to a
    # sentinel string: NaN never compares equal and would be dropped by a join.
    award = award.reset_index(drop=True)
    keyed = pd.DataFrame({"_row": award.index})
    for col in key:
        keyed[col] = award[col].fillna("~none~").astype(str).to_numpy()
    keyed["_doc"] = award["source_doc"].fillna("~none~").astype(str).to_numpy()
    keyed["_tons"] = award["contracted_tons"].fillna(0).to_numpy()

    counts = (keyed.groupby(key + ["_doc"], as_index=False)
                   .agg(n_rows=("_row", "size"), tons=("_tons", "sum"))
                   .sort_values(["n_rows", "tons"], ascending=False))
    winners = counts.drop_duplicates(subset=key)[key + ["_doc"]]

    kept_rows = keyed.merge(winners, on=key + ["_doc"], how="inner")["_row"]
    return pd.concat([award.loc[kept_rows], other], ignore_index=True)


# --------------------------------------------------------------------------
# Price semantics and provenance
# --------------------------------------------------------------------------
# Both states quote a delivered price per short ton, which is what makes a
# cross-state weighted average meaningful. Michigan prices each drop point
# ("Drop Point Address" with delivery hours on the schedule); Pennsylvania
# prices each county, delivered to the ordering member within 30 days
# ("The pricing on this contract is 'per ton'"). Neither quotes FOB mine, and
# neither uses metric tons. This is recorded per row so that adding a state
# that does quote FOB cannot silently contaminate an average.
DELIVERED = "delivered"
PRICE_UNIT = "USD/short ton"

# Michigan splits a season into an early fill and a seasonal back-up commitment
# at different prices; Pennsylvania prices initial fill and balance-of-season
# together under one county price.
FILL_TYPES = {
    "Early Fill": "early_fill",
    "Seasonal Back-Up": "seasonal_backup",
}
FILL_COMBINED = "combined"


def annotate_semantics(df: pd.DataFrame) -> pd.DataFrame:
    """Label what each price means, so unlike prices are never averaged blind."""
    if df.empty:
        return df
    out = df.copy()
    program = out.get("program", pd.Series(index=out.index, dtype=object))
    out["fill_type"] = program.map(FILL_TYPES).fillna(FILL_COMBINED)
    out["delivery_terms"] = DELIVERED
    out["price_unit"] = PRICE_UNIT

    has_price = out["price_per_ton"].notna()
    has_tons = out["contracted_tons"].notna()
    out["measure_basis"] = np.select(
        [has_price & has_tons, has_price & ~has_tons, ~has_price & has_tons],
        [MEASURE_PRICE_AND_VOLUME, MEASURE_PRICE_ONLY, MEASURE_VOLUME_ONLY],
        default="none",
    )
    return out


def attach_provenance(df: pd.DataFrame, manifest_path: str) -> pd.DataFrame:
    """Stamp each row with where its document came from and when.

    Without this a figure in the export can only be traced as far as a filename,
    which is not enough to re-verify it once the state has replaced the file at
    that URL.
    """
    if df.empty:
        return df
    out = df.copy()
    for col in ("source_url", "source_sha256", "retrieved_at"):
        if col not in out.columns:
            out[col] = None
    try:
        with open(manifest_path) as fh:
            entries = json.load(fh)
    except (OSError, ValueError):
        return out

    index = {e.get("name"): e for e in entries if e.get("name")}
    names = out["source_doc"].fillna("")
    out["source_url"] = names.map(lambda n: (index.get(n) or {}).get("url"))
    out["source_sha256"] = names.map(lambda n: (index.get(n) or {}).get("sha256"))
    out["retrieved_at"] = names.map(
        lambda n: (index.get(n) or {}).get("fetched_at") or None)

    # Documents pulled from the archive in an earlier sweep may predate the
    # manifest. Hashing them on disk keeps every row verifiable even when the
    # URL that produced it is no longer recorded.
    missing = out["source_sha256"].isna() & names.ne("")
    if missing.any():
        cache: dict[str, tuple[str | None, str | None]] = {}
        for name in names[missing].unique():
            path = next(iter(glob.glob(os.path.join("data", "raw", "*", name))), None)
            if not path:
                cache[name] = (None, None)
                continue
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            stamp = dt.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(
                timespec="seconds")
            cache[name] = (digest, stamp)
        out.loc[missing, "source_sha256"] = names[missing].map(lambda n: cache[n][0])
        out.loc[missing, "retrieved_at"] = names[missing].map(lambda n: cache[n][1])
    return out


# --------------------------------------------------------------------------
# Normalization and aggregation
# --------------------------------------------------------------------------
def normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=RAW_COLUMNS)
    out = df.copy()
    out = out[out["fiscal_year"].notna()]
    out["fiscal_year"] = out["fiscal_year"].astype(int)
    out = out[(out["fiscal_year"] >= HISTORICAL_FY_RANGE[0]) & (out["fiscal_year"] <= CURRENT_FY)]
    out["season"] = out["fiscal_year"].map(lambda fy: f"{fy - 1}/{fy}")
    out["is_current_cycle"] = out["fiscal_year"] == CURRENT_FY
    out = out[out["vendor"].notna()]
    for col in RAW_COLUMNS:
        if col not in out.columns:
            out[col] = None
    ordered = RAW_COLUMNS + [c for c in ("entity", "change_notice") if c in out.columns]
    return out[ordered].sort_values(
        ["state", "fiscal_year", "vendor", "county"], na_position="last"
    ).reset_index(drop=True)


def _weighted(g: pd.DataFrame) -> pd.Series:
    tons = g["contracted_tons"].sum() or None
    # Some tonnage is published without a matching award price. Including it in
    # the denominator would drag the average toward zero, so the weighted price
    # is taken over priced tonnage only and the gap is reported alongside it.
    priced = g[g["price_per_ton"].notna()]
    priced_tons = priced["contracted_tons"].sum()
    rev = (priced["contracted_tons"] * priced["price_per_ton"]).sum()
    return pd.Series({
        "contracted_tons": tons,
        "priced_tons": priced_tons,
        "contract_value": rev,
        "weighted_avg_price": rev / priced_tons if priced_tons else None,
        "simple_avg_price": g["price_per_ton"].mean(),
        "min_price": g["price_per_ton"].min(),
        "max_price": g["price_per_ton"].max(),
        "n_line_items": len(g),
        "n_counties": g["county"].nunique(),
    })


def aggregate(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Roll up to state x FY x vendor and state x FY, using weighted pricing."""
    # Seasons where only one of price or tonnage was ever published still belong
    # in the rollup; the missing measure is reported as blank rather than zero.
    award = raw[raw["record_type"].isin(["award", "volume_only", "price_only"])
                & (raw["contracted_tons"].notna() | raw["price_per_ton"].notna())]
    if award.empty:
        return pd.DataFrame(), pd.DataFrame()

    by_vendor = (award.groupby(["state", "fiscal_year", "season", "is_current_cycle", "vendor"])
                      .apply(_weighted, include_groups=False).reset_index())
    by_state = (award.groupby(["state", "fiscal_year", "season", "is_current_cycle"])
                     .apply(_weighted, include_groups=False).reset_index())

    # Volume that no supplier can be named for (PA FY2022-FY2023, where county
    # tonnage was published but the award pricing never was) is real state
    # volume but is not a market share. Folding it into the denominator would
    # understate every named supplier, and leaving it in the numerator would
    # invent a competitor called "Unattributed", so it is excluded from share
    # entirely and surfaced as its own column instead.
    named = by_vendor["vendor"].ne(UNATTRIBUTED)
    attributed = (by_vendor[named]
                  .groupby(["state", "fiscal_year"])["contracted_tons"].sum())
    totals = by_state.set_index(["state", "fiscal_year"])["contracted_tons"]

    def _share(r):
        if r["vendor"] == UNATTRIBUTED:
            return float("nan")
        base = attributed.get((r["state"], r["fiscal_year"]), float("nan"))
        return r["contracted_tons"] / base if base else float("nan")

    by_vendor["volume_share"] = by_vendor.apply(_share, axis=1)
    by_vendor["is_attributed"] = named

    by_state["attributed_tons"] = by_state.apply(
        lambda r: attributed.get((r["state"], r["fiscal_year"]), float("nan")), axis=1)
    by_state["unattributed_tons"] = (totals.reset_index(drop=True)
                                     - by_state["attributed_tons"].fillna(0)).where(
        by_state["attributed_tons"].notna(), by_state["contracted_tons"])
    return by_vendor, by_state


def diagnostics(raw: pd.DataFrame) -> pd.DataFrame:
    """Per-document coverage summary for QA."""
    if raw.empty:
        return pd.DataFrame()
    g = (raw.groupby(["state", "source_doc", "fiscal_year", "record_type"], dropna=False)
            .agg(rows=("price_per_ton", "size"),
                 tons=("contracted_tons", "sum"),
                 vendors=("vendor", lambda s: ", ".join(sorted(set(s.dropna())))))
            .reset_index())
    return g.sort_values(["state", "fiscal_year", "source_doc"])


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def export(result: BuildResult, out_dir: str = "data/output") -> dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "raw_csv": os.path.join(out_dir, "salt_contracts_raw.csv"),
        "vendor_csv": os.path.join(out_dir, "salt_contracts_by_vendor.csv"),
        "state_csv": os.path.join(out_dir, "salt_contracts_by_state.csv"),
        "excel": os.path.join(out_dir, "salt_contract_tracker.xlsx"),
    }
    result.raw.to_csv(paths["raw_csv"], index=False)
    result.state_fy_vendor.to_csv(paths["vendor_csv"], index=False)
    result.state_fy.to_csv(paths["state_csv"], index=False)

    with pd.ExcelWriter(paths["excel"], engine="openpyxl") as xl:
        result.state_fy_vendor.to_excel(xl, sheet_name="By Vendor", index=False)
        result.state_fy.to_excel(xl, sheet_name="By State", index=False)
        result.raw.to_excel(xl, sheet_name="Raw Line Items", index=False)
        if not result.diagnostics.empty:
            result.diagnostics.to_excel(xl, sheet_name="Source Coverage", index=False)
    return paths


def build(mi_docs: dict[str, str | None], pa_docs: list[str],
          pa_estimate_docs: list[str] | None = None,
          manifest_path: str = "data/manifest.json") -> BuildResult:
    frames = [parse_michigan_docs(mi_docs), parse_pennsylvania_docs(pa_docs)]
    combined = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    combined = dedupe_schedules(combined)
    combined = resolve_pa_awards(combined)
    combined = attach_pa_volume(combined, parse_pa_estimates(pa_estimate_docs or []))
    combined = annotate_semantics(combined)
    combined = attach_provenance(combined, manifest_path)
    raw = normalize(combined)
    by_vendor, by_state = aggregate(raw)
    return BuildResult(raw=raw, state_fy_vendor=by_vendor, state_fy=by_state,
                       diagnostics=diagnostics(raw))
