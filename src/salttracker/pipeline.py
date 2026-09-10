"""Build the road-salt contract dataset: parse -> normalize -> deduplicate -> export.

Definitions used throughout (per road-salt industry convention):

* Fiscal year runs Oct 1 - Sep 30 and is named for the calendar year it ends in,
  so the 2025/2026 winter season is FY2026.
* Michigan contracted volume is drop-point award tons (MDOT garages and named
  MiDEAL members). Pennsylvania volume is a county-lot *estimate of requirements*
  committed before the season (PennDOT + COSTARS members + non-PennDOT agencies),
  not tons purchased or delivered.
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
from dataclasses import dataclass, field

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
    "penndot_tons", "costars_tons", "agency_tons", "purchasing_entity",
    # Provenance: enough to re-find and re-verify any single row at its source.
    "source_doc", "source_page", "source_url", "source_sha256", "retrieved_at",
]

LOT_RECORD_TYPES = ("award", "price_only", "volume_only")
MEMBER_RECORD_TYPE = "costars_member"
CHANNEL_COSTARS = "COSTARS"

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
    parse_yields: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
CACHE_DIR = os.path.join("data", "interim", "parse_cache")


def _cache_key(path: str, tag: str) -> str:
    st = os.stat(path)
    sig = f"{os.path.basename(path)}-{st.st_size}-{int(st.st_mtime)}-{tag}"
    return os.path.join(CACHE_DIR, hashlib.sha1(sig.encode()).hexdigest() + ".pkl")


def _yield_record(path: str, state: str, kind: str, n_rows: int,
                  error: str | None = None) -> dict:
    return {
        "path": path,
        "name": os.path.basename(path),
        "state": state,
        "kind": kind,
        "n_rows": n_rows,
        "error": error,
    }


def pa_doc_kind(path: str) -> str:
    name = os.path.basename(path).lower()
    if "estimates" in name:
        return "estimates"
    if "bidsheet" in name or "bid_sheet" in name or "bid-sheet" in name:
        return "bidsheet"
    return "award"


def parse_michigan_docs(paths: dict[str, str | None]) -> tuple[pd.DataFrame, list[dict]]:
    """Parse Michigan PDFs. ``paths`` maps file path -> default vendor (or None).

    These contracts run to hundreds of pages, so parsed output is cached against
    the file's size and mtime. A file that exists but yields no award rows is
    recorded so the refresh can fail loud instead of looking like a quiet week.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    rows = []
    yields: list[dict] = []
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
        try:
            if os.path.exists(cache):
                with open(cache, "rb") as fh:
                    parsed = pickle.load(fh)
            else:
                parsed = mi_parser.parse(path)
                with open(cache, "wb") as fh:
                    pickle.dump(parsed, fh)
        except Exception as exc:
            yields.append(_yield_record(path, "MI", "award", 0, type(exc).__name__))
            continue

        if vendor is None:
            vendor = mi_parser.detect_vendor(path)

        parsed = mi_parser.select_current(parsed)
        yields.append(_yield_record(path, "MI", "award", len(parsed)))
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
    return pd.DataFrame(rows), yields


def parse_pennsylvania_docs(paths: list[str]) -> tuple[pd.DataFrame, list[dict]]:
    rows = []
    yields: list[dict] = []
    for path in paths:
        if not os.path.exists(path):
            continue
        kind = pa_doc_kind(path)
        try:
            parsed = pa_parser.parse(path)
        except Exception as exc:
            yields.append(_yield_record(path, "PA", kind, 0, type(exc).__name__))
            continue
        yields.append(_yield_record(path, "PA", kind, len(parsed)))
        for r in parsed:
            rows.append(dict(
                state="PA", fiscal_year=r.fy, vendor=r.vendor, county=r.county,
                program="Statewide Contract", channel=None,
                contracted_tons=r.tons, price_per_ton=r.price,
                extended_value=(r.tons * r.price) if (r.tons and r.price) else None,
                price_costars=r.price_costars, contract_no=r.contract_no,
                record_type=r.record_type, source_doc=r.source_doc,
                source_page=r.page, tons_basis="printed", change_notice=None,
                entity=None, purchasing_entity=None,
                penndot_tons=None, costars_tons=None, agency_tons=None,
            ))
    return pd.DataFrame(rows), yields


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
    members = pa[pa["record_type"].eq(MEMBER_RECORD_TYPE)]
    lots = pa[pa["record_type"].ne(MEMBER_RECORD_TYPE)]

    doc = lots["source_doc"].fillna("").str.lower()
    # Prefer a season packet over a change notice, and a priced row over a bare one.
    lots["_rank"] = (doc.str.contains("costars").astype(int) * 4
                     + lots["price_per_ton"].notna().astype(int) * 2
                     + lots["contracted_tons"].notna().astype(int))
    lots = lots.sort_values("_rank", ascending=False)
    lots = lots.drop_duplicates(subset=["fiscal_year", "county"]).drop(columns="_rank")
    return pd.concat([lots, members, rest], ignore_index=True)


def attach_pa_volume(df: pd.DataFrame, estimates: pd.DataFrame) -> pd.DataFrame:
    """Give Pennsylvania lot rows tonnage from the estimates attachment.

    Pennsylvania awards a price per county lot but publishes tonnage separately
    as estimated requirements (PennDOT + COSTARS + non-PennDOT agencies), not
    purchased or delivered tons. The cumulative is assigned to whichever supplier
    holds that county for the season. Split columns stay on the lot so a county
    can be broken out by channel without inventing a buyer.

    Seasons whose counties were never priced in a document we hold still carry a
    statewide estimate, so those are kept as unattributed volume rather than
    being dropped.
    """
    if estimates.empty:
        return df

    tons = (estimates.set_index(["fiscal_year", "county"])["cumulative_tons"]
                     .rename("_est_tons"))
    out = df.copy()
    is_lot = out["state"].eq("PA") & out["record_type"].isin(LOT_RECORD_TYPES)
    if is_lot.any():
        keys = pd.MultiIndex.from_arrays([out.loc[is_lot, "fiscal_year"],
                                          out.loc[is_lot, "county"]])
        matched = tons.reindex(keys).to_numpy()
        current = out.loc[is_lot, "contracted_tons"].to_numpy(dtype=float)
        use_est = ~pd.isna(matched)
        out.loc[is_lot, "contracted_tons"] = np.where(use_est, matched, current)
        priced = out.loc[is_lot, "price_per_ton"].notna().to_numpy()
        rt = out.loc[is_lot, "record_type"].to_numpy(dtype=object).copy()
        basis = out.loc[is_lot, "tons_basis"].to_numpy(dtype=object).copy()
        rt[use_est & priced] = "award"
        basis[use_est] = "committed_estimate"
        out.loc[is_lot, "record_type"] = rt
        out.loc[is_lot, "tons_basis"] = basis
        out.loc[is_lot, "extended_value"] = (out.loc[is_lot, "contracted_tons"]
                                             * out.loc[is_lot, "price_per_ton"])
        est_idx = estimates.set_index(["fiscal_year", "county"])
        for col in ("penndot_tons", "costars_tons", "agency_tons"):
            if col not in est_idx.columns:
                continue
            if col not in out.columns:
                out[col] = None
            out.loc[is_lot, col] = est_idx[col].reindex(keys).to_numpy()

    covered = set(out.loc[out["state"].eq("PA")
                           & out["record_type"].isin(LOT_RECORD_TYPES)
                           & out["contracted_tons"].notna(), "fiscal_year"])
    extra = estimates[~estimates["fiscal_year"].isin(covered)]
    if extra.empty:
        return out
    unattributed = pd.DataFrame({
        "state": "PA",
        "fiscal_year": extra["fiscal_year"].to_numpy(),
        "vendor": UNATTRIBUTED,
        "county": extra["county"].to_numpy(),
        "program": "Statewide Contract",
        "channel": None,
        "contracted_tons": extra["cumulative_tons"].to_numpy(),
        "price_per_ton": None,
        "extended_value": None,
        "record_type": "volume_only",
        "source_doc": extra["source_doc"].to_numpy(),
        "source_page": extra["source_page"].to_numpy(),
        "tons_basis": "committed_estimate",
        "penndot_tons": extra["penndot_tons"].to_numpy() if "penndot_tons" in extra else None,
        "costars_tons": extra["costars_tons"].to_numpy() if "costars_tons" in extra else None,
        "agency_tons": extra["agency_tons"].to_numpy() if "agency_tons" in extra else None,
        "purchasing_entity": None,
        "entity": None,
    })
    return pd.concat([out, unattributed], ignore_index=True)


def parse_pa_members(packet_paths: list[str], estimate_paths: list[str]) -> pd.DataFrame:
    """Named COSTARS buyers from packet rosters and estimates LPPU tables."""
    frames = []
    for path in packet_paths:
        if not os.path.exists(path):
            continue
        try:
            rows = pa_parser.parse_members(path)
        except Exception:
            continue
        if rows:
            frames.append(pd.DataFrame(rows))
    for path in estimate_paths:
        if not os.path.exists(path):
            continue
        try:
            frame = pa_est_parser.parse_members(path)
        except Exception:
            continue
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    if "member_category" not in combined.columns:
        combined["member_category"] = None
    combined["_has_cat"] = combined["member_category"].notna()
    combined = combined.sort_values("_has_cat", ascending=False)
    return (combined.drop_duplicates(subset=["fiscal_year", "county", "purchasing_entity"])
                    .drop(columns="_has_cat").reset_index(drop=True))


def attach_pa_members(df: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    """Append COSTARS roster rows. Not rolled into state/vendor totals."""
    if members is None or members.empty:
        return df
    lots = df[df["state"].eq("PA") & df["record_type"].isin(LOT_RECORD_TYPES)]
    vendor_map = {}
    price_map = {}
    cno_map = {}
    if not lots.empty:
        for _, row in lots.iterrows():
            key = (int(row["fiscal_year"]), str(row["county"]))
            if pd.notna(row.get("vendor")):
                vendor_map[key] = row["vendor"]
            if pd.notna(row.get("price_per_ton")):
                price_map[key] = row["price_per_ton"]
            if pd.notna(row.get("contract_no")):
                cno_map[key] = row["contract_no"]

    extra = []
    for _, m in members.iterrows():
        fy = int(m["fiscal_year"])
        county = str(m["county"])
        key = (fy, county)
        tons = m.get("contracted_tons")
        price = price_map.get(key)
        extra.append({
            "state": "PA",
            "fiscal_year": fy,
            "vendor": vendor_map.get(key) or UNATTRIBUTED,
            "county": county,
            "program": "Statewide Contract",
            "channel": CHANNEL_COSTARS,
            "contracted_tons": tons,
            "price_per_ton": price,
            "extended_value": (tons * price) if (pd.notna(tons) and pd.notna(price)) else None,
            "price_costars": price,
            "contract_no": cno_map.get(key),
            "record_type": MEMBER_RECORD_TYPE,
            "source_doc": m.get("source_doc"),
            "source_page": m.get("source_page"),
            "tons_basis": "costars_member_roster",
            "purchasing_entity": m.get("purchasing_entity"),
            "entity": m.get("purchasing_entity"),
            "penndot_tons": None,
            "costars_tons": None,
            "agency_tons": None,
            "change_notice": None,
        })
    if not extra:
        return df
    return pd.concat([df, pd.DataFrame(extra)], ignore_index=True)


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
    out = out[out["fiscal_year"] >= HISTORICAL_FY_RANGE[0]]
    out["season"] = out["fiscal_year"].map(lambda fy: f"{fy - 1}/{fy}")
    latest = int(out["fiscal_year"].max()) if not out.empty else CURRENT_FY
    out["is_current_cycle"] = out["fiscal_year"] == latest
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
    award = raw[raw["record_type"].isin(LOT_RECORD_TYPES)
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
    mi_frame, mi_yields = parse_michigan_docs(mi_docs)
    pa_frame, pa_yields = parse_pennsylvania_docs(pa_docs)
    frames = [mi_frame, pa_frame]
    nonempty = [f for f in frames if not f.empty]
    combined = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()
    combined = dedupe_schedules(combined)
    combined = resolve_pa_awards(combined)
    combined = attach_pa_volume(combined, parse_pa_estimates(pa_estimate_docs or []))
    combined = attach_pa_members(
        combined, parse_pa_members(pa_docs, pa_estimate_docs or []))
    combined = annotate_semantics(combined)
    combined = attach_provenance(combined, manifest_path)
    raw = normalize(combined)
    by_vendor, by_state = aggregate(raw)
    return BuildResult(raw=raw, state_fy_vendor=by_vendor, state_fy=by_state,
                       diagnostics=diagnostics(raw),
                       parse_yields=mi_yields + pa_yields)
