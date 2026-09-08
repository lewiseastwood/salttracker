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
.block-container { padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1280px; }
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


def excel_bytes(vendor: pd.DataFrame, state: pd.DataFrame, raw: pd.DataFrame) -> bytes:
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
                if isinstance(cell.value, float) and cell.column_letter:
                    pass
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            letter = col[0].column_letter
            ws.column_dimensions[letter].width = min(28, max(12, len(str(col[0].value or "")) + 4))

    write("By supplier", vendor)
    write("By state", state)
    write("Line items", raw.head(5000) if len(raw) > 5000 else raw)
    # drop the empty default sheet
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


raw, vendor, state = load()
if vendor is None:
    st.error("No dataset found. Run:  python scripts/refresh.py --no-download")
    st.stop()

named_vendors = sorted(v for v in vendor["vendor"].unique() if v != "Unattributed")
fys = sorted(int(x) for x in vendor["fiscal_year"].unique())

st.markdown(
    f"""<div class="masthead">
      <div class="title">Road salt contract tracker</div>
      <div class="meta">Michigan · Pennsylvania &nbsp;|&nbsp; {date.today().strftime("%d %b %Y")}</div>
    </div>""",
    unsafe_allow_html=True,
)

f1, f2, f3, f4, f5 = st.columns([1.3, 1.6, 1, 1, 1.6])
with f1:
    state_label = st.selectbox("State", ["Both states", "Michigan", "Pennsylvania"])
with f2:
    vendor_label = st.selectbox("Supplier", ["All suppliers"] + named_vendors)
with f3:
    fy_from = st.selectbox("From", fys, index=0)
with f4:
    fy_to = st.selectbox("To", fys, index=len(fys) - 1)
with f5:
    price_label = st.selectbox(
        "Price basis",
        ["Weighted (revenue ÷ volume)", "Unweighted average of posted prices"],
    )

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

latest_fy = int(s["fiscal_year"].max())
prev_fy = int(s[s["fiscal_year"] < latest_fy]["fiscal_year"].max()) if (s["fiscal_year"] < latest_fy).any() else None
cur = s[s["fiscal_year"] == latest_fy]
prev = s[s["fiscal_year"] == prev_fy] if prev_fy else None

def _delta(now, then, fmt):
    if then is None or then != then or not then:
        return None
    return fmt.format(now - then)

k1, k2, k3, k4 = st.columns(4)
tons_now = float(cur["contracted_tons"].fillna(0).sum())
tons_prev = float(prev["contracted_tons"].fillna(0).sum()) if prev is not None and not prev.empty else None
k1.metric(
    f"FY{latest_fy} contracted volume",
    f"{tons_now:,.0f} t",
    _delta(tons_now, tons_prev, "{:+,.0f} t vs prior year") if tons_prev else None,
)
priced_now = float(cur["priced_tons"].fillna(0).sum())
if priced_now:
    wavg = float(cur["contract_value"].sum() / priced_now)
    wavg_prev = None
    if prev is not None and not prev.empty and prev["priced_tons"].fillna(0).sum():
        wavg_prev = float(prev["contract_value"].sum() / prev["priced_tons"].sum())
        delta = f"{(wavg / wavg_prev - 1) * 100:+.1f}% vs FY{prev_fy}"
    else:
        delta = None
    k2.metric(f"FY{latest_fy} effective price", f"${wavg:,.2f}/t", delta)
    k3.metric(f"FY{latest_fy} contract value", f"${cur['contract_value'].sum() / 1e6:,.1f}m")
else:
    k2.metric(f"FY{latest_fy} effective price", "—")
    k3.metric(f"FY{latest_fy} contract value", "—")
k4.metric("Suppliers in view", f"{v.loc[v['is_attributed'], 'vendor'].nunique()}")

st.markdown(
    '<p class="note">Fiscal years run 1 Oct – 30 Sep and are named for the year they end. '
    "Price is the volume-weighted average unless you switch the basis. "
    "FY2027 is the awarded upcoming winter, not delivered volume. "
    "PA FY2022–FY2023 volume has no published supplier award and is held out of share.</p>",
    unsafe_allow_html=True,
)

if latest_fy == 2027:
    st.caption("FY2027 contracts were awarded in summer 2026 and may still be amended by change notice.")

left, right = st.columns(2)
with left:
    st.plotly_chart(charts.price_timeseries(s, metric), width="stretch")
with right:
    st.plotly_chart(charts.volume_timeseries(s), width="stretch")

tab_price, tab_vol, tab_share, tab_table = st.tabs(
    ["Price by supplier", "Volume by supplier", "Market share", "Tables & export"]
)

with tab_price:
    for code in sel_states:
        st.plotly_chart(charts.vendor_price(v, metric, code), width="stretch")

with tab_vol:
    for code in sel_states:
        st.plotly_chart(charts.vendor_volume(v, code), width="stretch")

with tab_share:
    st.caption("Share of volume that names a supplier. Unattributed PA years are omitted.")
    for code in sel_states:
        vs = v[(v["state"] == code) & v["is_attributed"]]
        if vs.empty:
            continue
        st.plotly_chart(charts.vendor_share(v, code), width="stretch")

with tab_table:
    show = v[[
        "state", "fiscal_year", "season", "vendor", "contracted_tons",
        "priced_tons", "weighted_avg_price", "simple_avg_price",
        "contract_value", "volume_share", "n_counties",
    ]].sort_values(["state", "fiscal_year", "vendor"])
    st.dataframe(
        show.style.format({
            "contracted_tons": "{:,.0f}", "priced_tons": "{:,.0f}",
            "weighted_avg_price": "${:,.2f}", "simple_avg_price": "${:,.2f}",
            "contract_value": "${:,.0f}", "volume_share": "{:.1%}",
        }, na_rep="—"),
        width="stretch", height=380, hide_index=True,
    )

    e1, e2, e3, e4 = st.columns(4)
    e1.download_button(
        "Export supplier table (CSV)", show.to_csv(index=False).encode(),
        "salt_by_supplier.csv", "text/csv", width="stretch",
    )
    e2.download_button(
        "Export state table (CSV)", s.to_csv(index=False).encode(),
        "salt_by_state.csv", "text/csv", width="stretch",
    )
    e3.download_button(
        "Export line items (CSV)", r.to_csv(index=False).encode(),
        "salt_line_items.csv", "text/csv", width="stretch",
    )
    e4.download_button(
        "Export briefing pack (Excel)", excel_bytes(show, s, r),
        "salt_contract_briefing.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    full = os.path.join(OUT_DIR, "salt_contract_tracker.xlsx")
    if os.path.exists(full):
        with open(full, "rb") as fh:
            st.download_button(
                "Full unfiltered workbook (Excel)", fh.read(),
                "salt_contract_tracker.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
