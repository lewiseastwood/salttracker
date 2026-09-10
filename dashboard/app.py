"""Executive dashboard for contracted road-salt price and volume.

    streamlit run dashboard/app.py
"""
from __future__ import annotations

import io
import os
from datetime import date
from html import escape

import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils.dataframe import dataframe_to_rows

import _charts as charts
import _briefing as briefing
import _downloads as downloads
from _theme import STATE_NAMES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "output")
VENDOR_CSV = os.path.join(OUT_DIR, "salt_contracts_by_vendor.csv")
STATE_CSV = os.path.join(OUT_DIR, "salt_contracts_by_state.csv")
RAW_CSV = os.path.join(OUT_DIR, "salt_contracts_raw.csv")
WATCH_STATE = os.path.join(ROOT, "data", "watch_state.json")
ALERTS_JSONL = os.path.join(ROOT, "data", "alerts.jsonl")

st.set_page_config(
    page_title="Road Salt Contracts | Michigan & Pennsylvania",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={"Get help": None, "Report a bug": None, "About": None},
)

st.markdown("""
<style>
@import url("https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=Libre+Baskerville:wght@700&display=swap");
html, body, [class*="css"] { font-family: "Source Sans 3", sans-serif; }
.block-container { padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1400px; }
h1, h2, h3 { font-family: "Libre Baskerville", Georgia, serif !important; color: #1B3A4B; letter-spacing: -0.02em; }
div[data-testid="stMetric"] {
    background: #fff; border: 1px solid #D7DCE0; border-radius: 8px;
    padding: 14px 16px;
}
div[data-testid="stMetric"] label { color: #5C6770; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] { font-family: "Libre Baskerville", Georgia, serif; color: #1B3A4B; }
.masthead { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #1B3A4B; padding-bottom: 10px; margin-bottom: 18px; }
.masthead .title { font-family: "Libre Baskerville", Georgia, serif; font-size: 1.55rem; color: #1B3A4B; }
.masthead .meta { color: #5C6770; font-size: 0.85rem; }
.note { color: #5C6770; font-size: 0.85rem; margin-top: 4px; }
.watch { display: flex; justify-content: space-between; gap: 16px; align-items: baseline;
         flex-wrap: wrap; background: #fff; border: 1px solid #D7DCE0; border-radius: 8px;
         padding: 10px 14px; margin-bottom: 16px; }
.watch.news { border-left: 4px solid #1B3A4B; }
.watch.quiet { border-left: 4px solid #D7DCE0; }
.watch.stale, .watch.failed, .watch.missing, .watch.unreviewed { border-left: 4px solid #C45C26; }
.watch-line { color: #1A2332; font-size: 0.95rem; }
.watch-meta { color: #5C6770; font-size: 0.82rem; white-space: nowrap; }
.cov-wrap { margin-bottom: 12px; }
.cov-title { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; color: #5C6770; margin-bottom: 6px; }
table.cov { border-collapse: collapse; width: 100%; font-size: 0.8rem; }
table.cov th, table.cov td { border: 1px solid #D7DCE0; padding: 4px 6px; text-align: center; }
table.cov th:first-child { text-align: left; font-weight: 600; white-space: nowrap; }
table.cov td { width: 2.4rem; }
table.cov td span { display: block; width: 14px; height: 14px; margin: 0 auto; border-radius: 2px; }
table.cov td.cov-both span { background: #1B3A4B; }
table.cov td.cov-partial span { background: #1B3A4B; opacity: 0.35; }
table.cov td.cov-empty span { background: transparent; border: 1px solid #D7DCE0; }
.cov-key { color: #5C6770; font-size: 0.8rem; margin-top: 8px; }
.stTabs [data-baseweb="tab-list"] { gap: 8px; }
.stTabs [data-baseweb="tab"] { font-weight: 600; }
footer { visibility: hidden; }
#MainMenu { visibility: hidden; }
header[data-testid="stHeader"] { display: none; }
div[data-testid="stToolbar"] { display: none; }
div[data-testid="stDecoration"] { display: none; }
div[data-testid="stStatusWidget"] { display: none; }
.stDeployButton { display: none; }
.stAppDeployButton { display: none; }
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load():
    if not os.path.exists(VENDOR_CSV):
        return None, None, None
    return pd.read_csv(RAW_CSV), pd.read_csv(VENDOR_CSV), pd.read_csv(STATE_CSV)


def excel_bytes(*sheets: tuple[str, pd.DataFrame]) -> bytes:
    """Workbook of the currently filtered tables, formatted for a briefing pack."""
    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="1B3A4B")
    header_font = Font(color="FFFFFF", bold=True, name="Calibri")
    thin = Border(
        left=Side(style="thin", color="D7DCE0"),
        right=Side(style="thin", color="D7DCE0"),
        top=Side(style="thin", color="D7DCE0"),
        bottom=Side(style="thin", color="D7DCE0"),
    )

    def write(name: str, df: pd.DataFrame) -> None:
        ws = wb.create_sheet(name)
        for row in dataframe_to_rows(df, index=False, header=True):
            ws.append(row)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=ws.max_column):
            for cell in row:
                cell.border = thin
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            letter = col[0].column_letter
            ws.column_dimensions[letter].width = min(32, max(14, len(str(col[0].value or "")) + 4))

    for name, df in sheets:
        write(name, df)
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def supplier_table(df: pd.DataFrame) -> pd.DataFrame:
    tons_name = briefing.volume_label(df)
    out = df.copy()
    out["state"] = out["state"].map(lambda s: STATE_NAMES.get(s, s))
    out = out.rename(columns={
        "state": "State",
        "fiscal_year": "Fiscal year",
        "season": "Season",
        "vendor": "Supplier",
        "contracted_tons": tons_name,
        "priced_tons": "Priced tons",
        "weighted_avg_price": "Weighted $/ton",
        "simple_avg_price": "Unweighted $/ton",
        "contract_value": "Contract value ($)",
        "volume_share": "Volume share",
        "n_counties": "Counties",
        "tons_basis": "Tons basis",
    })
    cols = [c for c in [
        "State", "Fiscal year", "Season", "Supplier", tons_name,
        "Priced tons", "Weighted $/ton", "Unweighted $/ton",
        "Contract value ($)", "Volume share", "Counties", "Tons basis",
    ] if c in out.columns]
    return out[cols].sort_values(["State", "Fiscal year", "Supplier"])


def state_table(df: pd.DataFrame) -> pd.DataFrame:
    tons_name = briefing.volume_label(df)
    out = df.copy()
    out["state"] = out["state"].map(lambda s: STATE_NAMES.get(s, s))
    out = out.rename(columns={
        "state": "State",
        "fiscal_year": "Fiscal year",
        "season": "Season",
        "contracted_tons": tons_name,
        "priced_tons": "Priced tons",
        "weighted_avg_price": "Weighted $/ton",
        "simple_avg_price": "Unweighted $/ton",
        "contract_value": "Contract value ($)",
        "n_counties": "Counties",
        "tons_basis": "Tons basis",
    })
    cols = [c for c in [
        "State", "Fiscal year", "Season", tons_name, "Priced tons",
        "Weighted $/ton", "Unweighted $/ton", "Contract value ($)", "Counties",
        "Tons basis",
    ] if c in out.columns]
    return out[cols].sort_values(["State", "Fiscal year"])


raw, vendor, state = load()
if vendor is None:
    st.error("No dataset found. Run:  python scripts/refresh.py --no-download")
    st.stop()

named_vendors = sorted(v for v in vendor["vendor"].unique() if v != "Unattributed")
fys = sorted(int(x) for x in vendor["fiscal_year"].unique())

n_docs = int(raw["source_doc"].nunique()) if "source_doc" in raw.columns else 0
try:
    retrieved = pd.to_datetime(raw["retrieved_at"], errors="coerce").max()
    retrieved_label = retrieved.strftime("%d %b %Y") if pd.notna(retrieved) else date.today().strftime("%d %b %Y")
except Exception:
    retrieved_label = date.today().strftime("%d %b %Y")

st.markdown(
    f"""<div class="masthead">
      <div class="title">Road salt contract tracker</div>
      <div class="meta">Michigan · Pennsylvania &nbsp;|&nbsp; {n_docs} source documents &nbsp;|&nbsp; Updated {retrieved_label}</div>
    </div>""",
    unsafe_allow_html=True,
)

watch = briefing.watch_strip(briefing.load_watch(WATCH_STATE), briefing.load_jsonl(ALERTS_JSONL))
st.markdown(
    f"""<div class="watch {watch['tone']}">
      <div class="watch-line">{escape(watch['headline'])}</div>
      <div class="watch-meta">{escape(watch['checked'])}</div>
    </div>""",
    unsafe_allow_html=True,
)
sha = briefing.load_watch(WATCH_STATE).get("data_commit") or briefing.git_head(ROOT)
href = briefing.change_report_href(watch, ROOT)
if href and str(href).startswith("http"):
    st.caption(f"Data commit `{sha or '—'}` · [Change report]({href})")
else:
    st.caption(
        f"Data commit `{sha or '—'}` · Change report: "
        f"`{href or 'data/output/CHANGE_REPORT.txt'}`"
    )

f1, f2, f3, f4, f5, f6 = st.columns([1.3, 1.5, 0.85, 0.85, 1.2, 1.6])
with f1:
    state_label = st.selectbox("State", ["Both states", "Michigan", "Pennsylvania"])
with f2:
    vendor_label = st.selectbox("Supplier", ["All suppliers"] + named_vendors)
with f3:
    fy_from = st.selectbox("From", fys, index=0)
with f4:
    fy_to = st.selectbox("To", fys, index=len(fys) - 1)
with f5:
    period_label = st.radio("Period", ["Annual", "Quarter"], horizontal=True)
with f6:
    price_label = st.selectbox(
        "Price basis",
        ["Weighted (revenue ÷ volume)", "Unweighted average of posted prices"],
    )
grain = "quarter" if period_label == "Quarter" else "annual"

if fy_from > fy_to:
    fy_from, fy_to = fy_to, fy_from

sel_states = ["MI", "PA"] if state_label == "Both states" else (
    ["MI"] if state_label == "Michigan" else ["PA"]
)
sel_vendors = named_vendors if vendor_label == "All suppliers" else [vendor_label]
metric = "weighted_avg_price" if price_label.startswith("Weighted") else "simple_avg_price"

v = vendor[
    vendor["state"].isin(sel_states)
    & vendor["fiscal_year"].between(fy_from, fy_to)
    & vendor["vendor"].isin(sel_vendors + (["Unattributed"] if vendor_label == "All suppliers" else []))
].copy()
s = state[
    state["state"].isin(sel_states) & state["fiscal_year"].between(fy_from, fy_to)
].copy()
r = raw[
    raw["state"].isin(sel_states)
    & raw["fiscal_year"].between(fy_from, fy_to)
    & raw["vendor"].isin(v["vendor"].unique())
].copy()

if v.empty:
    st.warning("No contracts match those filters.")
    st.stop()

by_supplier = supplier_table(v)
by_state = state_table(s)
line_keep = [c for c in [
    "state", "fiscal_year", "vendor", "county", "program", "channel", "purchasing_entity",
    "contracted_tons", "penndot_tons", "costars_tons", "agency_tons",
    "price_per_ton", "extended_value", "record_type",
    "source_doc", "source_page", "source_url", "source_page_url", "tons_basis",
] if c in r.columns]
line_items = r[line_keep].copy()
if "state" in line_items.columns:
    line_items["state"] = line_items["state"].map(lambda s: STATE_NAMES.get(s, s))
tons_col = briefing.volume_label(r)
line_items = line_items.rename(columns={
    "state": "State", "fiscal_year": "Fiscal year", "vendor": "Supplier",
    "county": "County / drop point", "program": "Program", "channel": "Channel",
    "purchasing_entity": "Purchasing entity",
    "contracted_tons": tons_col, "penndot_tons": "PennDOT tons",
    "costars_tons": "COSTARS tons", "agency_tons": "Non-PennDOT agency tons",
    "price_per_ton": "Price $/ton",
    "extended_value": "Extended value ($)", "record_type": "Record type",
    "source_doc": "Source document", "source_page": "Page",
    "source_url": "Source URL", "source_page_url": "Source page URL",
    "tons_basis": "Tons basis",
})
catalog = briefing.source_documents(r)
catalog_view = catalog.copy()
catalog_view["state"] = catalog_view["state"].map(
    lambda s: STATE_NAMES.get(s, s) if isinstance(s, str) and len(s) == 2 else s
)
catalog_view = catalog_view.rename(columns={
    "state": "State",
    "source_doc": "Source document",
    "source_label": "Document",
    "fiscal_year_from": "FY from",
    "fiscal_year_to": "FY to",
    "suppliers": "Suppliers",
    "source_url": "Published file URL",
    "source_page_url": "Source page URL",
})
pack = excel_bytes(
    ("By supplier", by_supplier),
    ("By state", by_state),
    ("Line items", line_items.head(10_000)),
    ("Source contracts", catalog_view),
)

latest_fy = int(s["fiscal_year"].max())
prev_fy = int(s[s["fiscal_year"] < latest_fy]["fiscal_year"].max()) if (s["fiscal_year"] < latest_fy).any() else None
cur = s[s["fiscal_year"] == latest_fy]
prev = s[s["fiscal_year"] == prev_fy] if prev_fy else None

def _delta(now, then, fmt):
    if then is None or then != then or not then:
        return None
    return fmt.format(now - then)

def _named_set(df: pd.DataFrame, fy: int) -> set[str]:
    if df.empty:
        return set()
    hit = df[(df["fiscal_year"] == fy) & df["is_attributed"]]
    return set(hit["vendor"].dropna().unique())


k1, k2, k3 = st.columns(3)
tons_now = float(cur["contracted_tons"].fillna(0).sum())
tons_prev = float(prev["contracted_tons"].fillna(0).sum()) if prev is not None and not prev.empty else None
priced_now = float(cur["priced_tons"].fillna(0).sum())
like_for_like = (
    prev_fy is not None
    and _named_set(v, latest_fy) == _named_set(v, prev_fy)
    and _named_set(v, latest_fy)
)

if priced_now:
    wavg = float(cur["contract_value"].sum() / priced_now)
    wavg_prev = None
    if prev is not None and not prev.empty and prev["priced_tons"].fillna(0).sum():
        wavg_prev = float(prev["contract_value"].sum() / prev["priced_tons"].sum())
        price_delta = f"{(wavg / wavg_prev - 1) * 100:+.1f}% vs FY{prev_fy}"
    else:
        price_delta = None
    k1.metric(f"FY{latest_fy} Avg. Price", f"${wavg:,.2f}", price_delta)
    val_now = float(cur["contract_value"].sum())
    val_prev = float(prev["contract_value"].sum()) if like_for_like and prev is not None else None
    k3.metric(
        f"FY{latest_fy} Estd. Value",
        f"${val_now / 1e6:,.0f}M",
        f"{(val_now - val_prev) / 1e6:+.0f}M vs FY{prev_fy}" if val_prev else None,
    )
else:
    k1.metric(f"FY{latest_fy} Avg. Price", "—")
    k3.metric(f"FY{latest_fy} Estd. Value", "—")
k2.metric(
    f"FY{latest_fy} {briefing.volume_label(s)}",
    f"{tons_now / 1000:,.0f}K",
    _delta(tons_now, tons_prev, "{:+,.0f} t vs prior year") if like_for_like and tons_prev else None,
)
if not like_for_like and prev_fy:
    added = _named_set(v, latest_fy) - _named_set(v, prev_fy)
    dropped = _named_set(v, prev_fy) - _named_set(v, latest_fy)
    bits = []
    if added:
        bits.append("added " + ", ".join(sorted(added)))
    if dropped:
        bits.append("dropped " + ", ".join(sorted(dropped)))
    st.caption(
        f"Volume and value vs FY{prev_fy} are omitted: the named-supplier set changed"
        + (f" ({'; '.join(bits)})" if bits else "")
        + ". That is coverage/awards, not like-for-like market growth."
    )

with st.expander("How to read this"):
    st.markdown(
        "Fiscal years run 1 Oct – 30 Sep and are named for the year they end. "
        "Headline KPIs are the **latest fiscal year in the From/To range**, not a multi-year average. "
        "Price is the volume-weighted average unless you switch the basis. "
        "FY2027 is the awarded upcoming winter, not delivered volume. "
        f"{briefing.PA_VOLUME_NOTE} "
        f"{briefing.PA_TONS_BASIS_NOTE} "
        f"{briefing.UNLIKE_SHARE_NOTE} "
        f"{briefing.MI_CAPTURE_NOTE} "
        "Pennsylvania FY2022–FY2023 have published county estimates but **no supplier award**, so those years have volume without a named price. "
        "Both states quote **delivered** $/short ton (not FOB); programs still differ, so the MI–PA price gap is real in the documents but not a like-for-like bid. "
        "Quarterly view places each annual award in Q1 (Oct–Dec). Q2–Q4 have no new published figures; the line connects Q1 awards across years."
    )

briefing_notes = []
if "PA" in sel_states:
    briefing_notes.extend([briefing.PA_VOLUME_NOTE, briefing.PA_TONS_BASIS_NOTE])
if "MI" in sel_states:
    briefing_notes.append(briefing.MI_CAPTURE_NOTE_SHORT)
    if "PA" in sel_states:
        briefing_notes.append(briefing.UNLIKE_SHARE_NOTE)
if briefing_notes:
    st.caption(" ".join(briefing_notes))

st.plotly_chart(charts.price_timeseries(s, metric, grain), width="stretch")
st.caption(
    "Pennsylvania FY2022–FY2023: volume is published without a supplier award, so there is no PA price those years. "
    "Both states post delivered $/ton (not FOB); programs still differ, so the MI–PA gap is not a like-for-like bid."
)

with st.expander("Coverage by supplier and year", expanded=False):
    st.caption(
        "Filled = price and volume. Half-tone = one of the two is published. "
        "Empty = nothing in the documents. "
        "Volume, no award is Pennsylvania county estimates with no named supplier."
    )
    cov_cols = st.columns(len(sel_states))
    for i, code in enumerate(sel_states):
        grid = briefing.coverage_grid(v, code)
        with cov_cols[i]:
            st.markdown(briefing.coverage_table_html(grid), unsafe_allow_html=True)

share_cols = st.columns(len(sel_states))
for i, code in enumerate(sel_states):
    vs = v[(v["state"] == code) & v["is_attributed"]]
    if vs.empty:
        continue
    with share_cols[i]:
        st.plotly_chart(charts.vendor_share(v, code), width="stretch")

map_cols = st.columns(len(sel_states))
for i, code in enumerate(sel_states):
    with map_cols[i]:
        st.plotly_chart(charts.state_volume_map(s, code), width="stretch")
        notes = [f"{STATE_NAMES.get(code, code)}: {briefing.STATE_VOLUME_MEASURE[code]} in the **From/To fiscal-year range** (summed, not a share)."]
        if code == "PA":
            notes.append(briefing.PA_VOLUME_NOTE)
        if code == "MI":
            notes.append(briefing.MI_CAPTURE_NOTE_SHORT)
        st.caption(" ".join(notes))
st.plotly_chart(charts.state_volume_bars(s), width="stretch")
st.caption("Bars are the latest fiscal year in the From/To range, shown as tons — not a two-state share.")

tab_compare, tab_suppliers, tab_vol, tab_table = st.tabs(
    ["Volume & price by supplier", "Suppliers in latest year", "Volume over time", "Tables & export"]
)

money_fmt = st.column_config.NumberColumn(format="$%.2f")
tons_fmt = st.column_config.NumberColumn(format="%.0f")
share_fmt = st.column_config.NumberColumn(format="%.1%")
value_fmt = st.column_config.NumberColumn(format="$%.0f")

with tab_compare:
    st.caption(
        "One chart per state. Detroit Salt is Michigan-only; it will not appear on the Pennsylvania figure. "
        "Bars are omitted where contracted tonnage was not published (Pennsylvania FY2025 renewal)."
    )
    for code in sel_states:
        st.plotly_chart(
            charts.volume_price_comparison(v[v["state"] == code], metric, grain),
            width="stretch",
        )
        if code == "PA":
            st.caption(briefing.PA_TONS_BASIS_NOTE)
    for code in sel_states:
        st.plotly_chart(charts.vendor_price(v, metric, code, grain), width="stretch")
        if code == "PA":
            st.caption(
                "No published supplier award in FY2022–FY2023. First priced year is FY2024 "
                "(American Rock Salt ~$81.79, Morton ~$80.77)."
            )

with tab_suppliers:
    for code in sel_states:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(charts.vendor_bubbles(v, code), width="stretch")
        with c2:
            st.plotly_chart(charts.vendor_price_bars(v, metric, code), width="stretch")

with tab_vol:
    st.plotly_chart(charts.volume_timeseries(s, grain), width="stretch")
    if "PA" in sel_states:
        st.caption(briefing.PA_TONS_BASIS_NOTE)
    for code in sel_states:
        st.plotly_chart(charts.vendor_volume(v, code, grain), width="stretch")

with tab_table:
    st.markdown("**These tables follow the dropdowns above** — selected state, supplier and years only.")
    t1, x2, x3, x4 = st.columns(4)
    t1.download_button(
        "Supplier table · CSV", by_supplier.to_csv(index=False).encode(),
        "salt_by_supplier.csv", "text/csv", width="stretch", key="tab_supplier_csv",
    )
    x2.download_button(
        "State table · CSV", by_state.to_csv(index=False).encode(),
        "salt_by_state.csv", "text/csv", width="stretch", key="tab_state_csv",
    )
    x3.download_button(
        "Line items · CSV", line_items.to_csv(index=False).encode(),
        "salt_line_items.csv", "text/csv", width="stretch", key="tab_line_csv",
    )
    x4.download_button(
        "All four · Excel", pack, "salt_contract_tables.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch", key="tab_excel",
    )

    st.subheader("Source contracts")
    st.caption(
        "Each Fetch returns the published file (local disk after a scrape, otherwise "
        "the file URL). An empty file URL is not a fetch failure: the list below "
        "separates fetch failed, no direct file but a source page, and provenance unknown."
    )
    c_csv, c_zip = st.columns(2)
    c_csv.download_button(
        "Contract list · CSV", catalog_view.to_csv(index=False).encode(),
        "salt_source_contracts.csv", "text/csv", width="stretch", key="tab_docs_csv",
    )
    if c_zip.button("Prepare zip of filtered contracts", width="stretch", key="tab_docs_zip_prep"):
        rows = catalog.to_dict("records")
        blob, failures = downloads.zip_sources(rows)
        st.session_state["source_zip"] = blob
        st.session_state["source_zip_fail"] = failures
        st.session_state["source_zip_filt"] = (
            tuple(sel_states), int(fy_from), int(fy_to), vendor_label,
        )
    zip_ok = st.session_state.get("source_zip_filt") == (
        tuple(sel_states), int(fy_from), int(fy_to), vendor_label,
    )
    zip_bytes = st.session_state.get("source_zip") if zip_ok else None
    zip_fail = (st.session_state.get("source_zip_fail") or []) if zip_ok else []
    if zip_bytes:
        c_zip.download_button(
            "Save zip", zip_bytes, "salt_source_contracts.zip",
            "application/zip", width="stretch", key="tab_docs_zip",
        )
    if zip_fail:
        st.error("These sources could not be downloaded:\n" + "\n".join(f"- {e}" for e in zip_fail))
    st.dataframe(
        catalog_view,
        width="stretch", height=280, hide_index=True,
        column_config={
            "Published file URL": st.column_config.LinkColumn(
                "Published file URL", display_text="Open file"),
            "Source page URL": st.column_config.LinkColumn(
                "Source page URL", display_text="Open page"),
        },
    )
    st.markdown("**Download each contract**")
    for i, row in catalog.iterrows():
        doc = str(row.get("source_doc") or f"contract_{i}")
        url = downloads.published_link(row.get("source_url"))
        page = downloads.published_link(row.get("source_page_url"))
        left, mid, right = st.columns([5, 1.4, 1.6])
        left.write(doc)
        fetch_key = f"doc_fetch_{i}"
        data_key = f"doc_bytes_{i}"
        if url:
            if mid.button("Fetch", key=f"btn_{fetch_key}"):
                blob, err = downloads.load_source_bytes(
                    doc, row.get("state"), url, page_url=page,
                )
                st.session_state[data_key] = (blob, err, url)
        elif page:
            mid.link_button("Source page", page)
        else:
            mid.caption("Unknown")
        stored = st.session_state.get(data_key)
        if stored:
            blob, err, fetched_url = stored
            if err:
                if "provenance unknown" in err:
                    right.caption("Provenance unknown")
                elif "no stable public file URL" in err:
                    right.caption("No direct file")
                else:
                    right.caption("Fetch failed")
                st.error(err)
            else:
                right.download_button(
                    "Download", blob, file_name=os.path.basename(doc),
                    mime="application/octet-stream", key=f"dl_{i}",
                )
        elif not url and page:
            right.caption("No direct file")
        elif not url:
            right.caption("Provenance unknown")

    st.subheader("By supplier")
    st.dataframe(
        by_supplier,
        width="stretch", height=360, hide_index=True,
        column_config={
            briefing.volume_label(v): tons_fmt, "Priced tons": tons_fmt,
            "Weighted $/ton": money_fmt, "Unweighted $/ton": money_fmt,
            "Contract value ($)": value_fmt, "Volume share": share_fmt,
        },
    )

    st.subheader("By state")
    st.dataframe(
        by_state,
        width="stretch", height=280, hide_index=True,
        column_config={
            briefing.volume_label(s): tons_fmt, "Priced tons": tons_fmt,
            "Weighted $/ton": money_fmt, "Unweighted $/ton": money_fmt,
            "Contract value ($)": value_fmt,
        },
    )

    st.subheader("Line items")
    st.caption(
        "Pennsylvania lot rows are county geography (no buyer). Named COSTARS "
        "members are extra rows with a purchasing entity from the roster; "
        "PennDOT and non-PennDOT agencies are split columns on the lot, not inferred buyers."
    )
    st.dataframe(
        line_items, width="stretch", height=360, hide_index=True,
        column_config={
            tons_col: tons_fmt, "PennDOT tons": tons_fmt, "COSTARS tons": tons_fmt,
            "Non-PennDOT agency tons": tons_fmt, "Price $/ton": money_fmt,
            "Extended value ($)": value_fmt,
            "Source URL": st.column_config.LinkColumn("Source URL", display_text="Open"),
            "Source page URL": st.column_config.LinkColumn(
                "Source page URL", display_text="Open page"),
        },
    )
