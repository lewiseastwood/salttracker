"""Executive dashboard for contracted road-salt price and volume.

    streamlit run dashboard/app.py
"""
from __future__ import annotations

import io
import os
from datetime import date

import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils.dataframe import dataframe_to_rows

import charts
from theme import STATE_NAMES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "output")
VENDOR_CSV = os.path.join(OUT_DIR, "salt_contracts_by_vendor.csv")
STATE_CSV = os.path.join(OUT_DIR, "salt_contracts_by_state.csv")
RAW_CSV = os.path.join(OUT_DIR, "salt_contracts_raw.csv")

st.set_page_config(
    page_title="Road Salt Contracts | Michigan & Pennsylvania",
    layout="wide",
    initial_sidebar_state="collapsed",
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
.stTabs [data-baseweb="tab-list"] { gap: 8px; }
.stTabs [data-baseweb="tab"] { font-weight: 600; }
footer { visibility: hidden; }
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
    out = df.copy()
    out["state"] = out["state"].map(lambda s: STATE_NAMES.get(s, s))
    out = out.rename(columns={
        "state": "State",
        "fiscal_year": "Fiscal year",
        "season": "Season",
        "vendor": "Supplier",
        "contracted_tons": "Contracted tons",
        "priced_tons": "Priced tons",
        "weighted_avg_price": "Weighted $/ton",
        "simple_avg_price": "Unweighted $/ton",
        "contract_value": "Contract value ($)",
        "volume_share": "Volume share",
        "n_counties": "Counties",
    })
    cols = [c for c in [
        "State", "Fiscal year", "Season", "Supplier", "Contracted tons",
        "Priced tons", "Weighted $/ton", "Unweighted $/ton",
        "Contract value ($)", "Volume share", "Counties",
    ] if c in out.columns]
    return out[cols].sort_values(["State", "Fiscal year", "Supplier"])


def state_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["state"] = out["state"].map(lambda s: STATE_NAMES.get(s, s))
    out = out.rename(columns={
        "state": "State",
        "fiscal_year": "Fiscal year",
        "season": "Season",
        "contracted_tons": "Contracted tons",
        "priced_tons": "Priced tons",
        "weighted_avg_price": "Weighted $/ton",
        "simple_avg_price": "Unweighted $/ton",
        "contract_value": "Contract value ($)",
        "n_counties": "Counties",
    })
    cols = [c for c in [
        "State", "Fiscal year", "Season", "Contracted tons", "Priced tons",
        "Weighted $/ton", "Unweighted $/ton", "Contract value ($)", "Counties",
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
    "state", "fiscal_year", "vendor", "county", "program", "channel",
    "contracted_tons", "price_per_ton", "extended_value", "record_type",
    "source_doc", "source_page",
] if c in r.columns]
line_items = r[line_keep].copy()
if "state" in line_items.columns:
    line_items["state"] = line_items["state"].map(lambda s: STATE_NAMES.get(s, s))
line_items = line_items.rename(columns={
    "state": "State", "fiscal_year": "Fiscal year", "vendor": "Supplier",
    "county": "County / drop point", "program": "Program", "channel": "Channel",
    "contracted_tons": "Contracted tons", "price_per_ton": "Price $/ton",
    "extended_value": "Extended value ($)", "record_type": "Record type",
    "source_doc": "Source document", "source_page": "Page",
})
pack = excel_bytes(
    ("By supplier", by_supplier),
    ("By state", by_state),
    ("Line items", line_items.head(10_000)),
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
    f"FY{latest_fy} Volume (T)",
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
        "Pennsylvania FY2022–FY2023 have published county estimates but **no supplier award**, so those years have volume without a named price. "
        "Both states quote **delivered** $/short ton (not FOB); programs still differ, so the MI–PA price gap is real in the documents but not a like-for-like bid. "
        "Quarterly view places each annual award in Q1 (Oct–Dec); Q2–Q4 are blank because the states do not publish quarterly contracted tons or prices."
    )

st.plotly_chart(charts.price_timeseries(s, metric, grain), width="stretch")

share_cols = st.columns(len(sel_states))
for i, code in enumerate(sel_states):
    vs = v[(v["state"] == code) & v["is_attributed"]]
    if vs.empty:
        continue
    with share_cols[i]:
        st.plotly_chart(charts.vendor_share(v, code), width="stretch")

tab_compare, tab_suppliers, tab_vol, tab_table = st.tabs(
    ["Volume & price by supplier", "Suppliers in latest year", "Volume over time", "Tables & export"]
)

money_fmt = st.column_config.NumberColumn(format="$%.2f")
tons_fmt = st.column_config.NumberColumn(format="%.0f")
share_fmt = st.column_config.NumberColumn(format="%.1%")
value_fmt = st.column_config.NumberColumn(format="$%.0f")

with tab_compare:
    st.caption(
        "One chart per state. Detroit Salt is Michigan-only; it will not appear on the Pennsylvania figure."
    )
    for code in sel_states:
        st.plotly_chart(
            charts.volume_price_comparison(v[v["state"] == code], metric, grain),
            width="stretch",
        )
    for code in sel_states:
        st.plotly_chart(charts.vendor_price(v, metric, code, grain), width="stretch")

with tab_suppliers:
    for code in sel_states:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(charts.vendor_bubbles(v, code), width="stretch")
        with c2:
            st.plotly_chart(charts.vendor_price_bars(v, metric, code), width="stretch")

with tab_vol:
    st.plotly_chart(charts.volume_timeseries(s, grain), width="stretch")
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
        "All three · Excel", pack, "salt_contract_tables.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch", key="tab_excel",
    )

    st.subheader("By supplier")
    st.dataframe(
        by_supplier,
        width="stretch", height=360, hide_index=True,
        column_config={
            "Contracted tons": tons_fmt, "Priced tons": tons_fmt,
            "Weighted $/ton": money_fmt, "Unweighted $/ton": money_fmt,
            "Contract value ($)": value_fmt, "Volume share": share_fmt,
        },
    )

    st.subheader("By state")
    st.dataframe(
        by_state,
        width="stretch", height=280, hide_index=True,
        column_config={
            "Contracted tons": tons_fmt, "Priced tons": tons_fmt,
            "Weighted $/ton": money_fmt, "Unweighted $/ton": money_fmt,
            "Contract value ($)": value_fmt,
        },
    )

    st.subheader("Line items")
    st.caption("One row per Pennsylvania county or Michigan drop point.")
    st.dataframe(
        line_items, width="stretch", height=360, hide_index=True,
        column_config={
            "Contracted tons": tons_fmt, "Price $/ton": money_fmt,
            "Extended value ($)": value_fmt,
        },
    )
