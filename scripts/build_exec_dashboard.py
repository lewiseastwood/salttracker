#!/usr/bin/env python3
"""Build a self-contained HTML briefing that opens in any browser.

    python scripts/build_exec_dashboard.py

Output: data/output/Road_Salt_Contract_Tracker.html
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd
import plotly.io as pio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

import charts  # noqa: E402
from theme import STATE_NAMES  # noqa: E402

OUT = os.path.join(ROOT, "data", "output")
HTML = os.path.join(OUT, "Road_Salt_Contract_Tracker.html")


def fig_html(fig) -> str:
    return pio.to_html(fig, include_plotlyjs=False, full_html=False,
                       config={"displaylogo": False, "responsive": True})


def main() -> None:
    vendor = pd.read_csv(os.path.join(OUT, "salt_contracts_by_vendor.csv"))
    state = pd.read_csv(os.path.join(OUT, "salt_contracts_by_state.csv"))
    named = vendor[vendor["is_attributed"]].copy()

    plots = {
        "compare_all_annual": fig_html(charts.volume_price_comparison(named, "weighted_avg_price", "annual")),
        "compare_all_quarter": fig_html(charts.volume_price_comparison(named, "weighted_avg_price", "quarter")),
        "map_all": fig_html(charts.state_overview_map(state)),
        "bubbles_all": fig_html(charts.vendor_bubbles(named)),
        "bars_all": fig_html(charts.vendor_price_bars(named, "weighted_avg_price")),
        "price_all": fig_html(charts.price_timeseries(state, "weighted_avg_price", "annual")),
        "volume_all": fig_html(charts.volume_timeseries(state, "annual")),
    }
    for code in ("MI", "PA"):
        sub = named[named["state"] == code]
        st_sub = state[state["state"] == code]
        plots[f"compare_{code}_annual"] = fig_html(charts.volume_price_comparison(sub, "weighted_avg_price", "annual"))
        plots[f"compare_{code}_quarter"] = fig_html(charts.volume_price_comparison(sub, "weighted_avg_price", "quarter"))
        plots[f"map_{code}"] = fig_html(charts.state_overview_map(st_sub))
        plots[f"bubbles_{code}"] = fig_html(charts.vendor_bubbles(sub))
        plots[f"bars_{code}"] = fig_html(charts.vendor_price_bars(sub, "weighted_avg_price"))
        plots[f"price_{code}"] = fig_html(charts.vendor_price(named, "weighted_avg_price", code, "annual"))
        plots[f"vol_{code}"] = fig_html(charts.vendor_volume(vendor[vendor["state"] == code], code, "annual"))
        plots[f"share_{code}"] = fig_html(charts.vendor_share(named, code))

    table_rows = []
    for _, row in named.sort_values(["state", "fiscal_year", "vendor"]).iterrows():
        table_rows.append({
            "state": STATE_NAMES.get(row["state"], row["state"]),
            "fy": f"FY{int(row['fiscal_year'])}",
            "vendor": row["vendor"],
            "tons": None if pd.isna(row["contracted_tons"]) else float(row["contracted_tons"]),
            "price": None if pd.isna(row["weighted_avg_price"]) else float(row["weighted_avg_price"]),
            "value": None if pd.isna(row["contract_value"]) else float(row["contract_value"]),
            "share": None if pd.isna(row["volume_share"]) else float(row["volume_share"]),
        })
    state_rows = []
    for _, row in state.sort_values(["state", "fiscal_year"]).iterrows():
        state_rows.append({
            "state": STATE_NAMES.get(row["state"], row["state"]),
            "fy": f"FY{int(row['fiscal_year'])}",
            "tons": None if pd.isna(row["contracted_tons"]) else float(row["contracted_tons"]),
            "price": None if pd.isna(row["weighted_avg_price"]) else float(row["weighted_avg_price"]),
            "value": None if pd.isna(row["contract_value"]) else float(row["contract_value"]),
        })

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Road Salt Contract Tracker — Michigan &amp; Pennsylvania</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {{ --navy:#1B3A4B; --ink:#1A2332; --muted:#5C6770; --line:#D7DCE0; --paper:#F4F5F7; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
         font-family: "Source Sans 3", "Segoe UI", sans-serif; }}
  header {{ background:var(--navy); color:#fff; padding:22px 40px; }}
  header h1 {{ font-family: Georgia, serif; font-size:1.5rem; margin:0; font-weight:700; }}
  header p {{ margin:6px 0 0; color:#c9d4dc; font-size:0.9rem; }}
  .wrap {{ max-width:1400px; margin:0 auto; padding:24px 40px 48px; }}
  .filters {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:20px; align-items:flex-end; }}
  .filters label {{ font-size:0.75rem; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted); display:block; margin-bottom:4px; }}
  select {{ font: inherit; padding:8px 10px; border:1px solid var(--line); border-radius:6px; background:#fff; min-width:180px; }}
  .seg {{ display:flex; gap:0; border:1px solid var(--navy); border-radius:6px; overflow:hidden; }}
  .seg label {{ margin:0; text-transform:none; letter-spacing:0; font-size:0.85rem; color:var(--navy); padding:8px 14px; cursor:pointer; }}
  .seg input {{ display:none; }}
  .seg input:checked + span {{ background:var(--navy); color:#fff; }}
  .seg span {{ display:block; padding:0; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  .grid3 {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:8px; }}
  .card.wide {{ margin-bottom:16px; }}
  h2 {{ font-family: Georgia, serif; color:var(--navy); font-size:1.15rem; margin:28px 0 10px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; font-size:0.9rem; }}
  th {{ text-align:left; background:var(--navy); color:#fff; padding:8px 10px; font-weight:600; }}
  td {{ border-bottom:1px solid var(--line); padding:8px 10px; }}
  tr:hover td {{ background:#f7f3ee; }}
  .exports {{ margin:8px 0 16px; display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
  .exports button, .exports a {{ color:#fff; background:var(--navy); border:0; text-decoration:none; padding:8px 14px; border-radius:6px; font-size:0.85rem; cursor:pointer; font-family:inherit; }}
  .exports button.secondary, .exports a.secondary {{ background:#fff; color:var(--navy); border:1px solid var(--navy); }}
  .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:8px; }}
  .note {{ color:var(--muted); font-size:0.85rem; margin-top:24px; }}
  @media (max-width: 900px) {{ .grid, .grid3 {{ grid-template-columns:1fr; }} .wrap, header {{ padding-left:18px; padding-right:18px; }} }}
</style>
</head>
<body>
<header>
  <h1>Road salt contract tracker</h1>
  <p>Michigan and Pennsylvania &nbsp;·&nbsp; Contracted volume and price from published state awards &nbsp;·&nbsp; Fiscal years run 1 Oct – 30 Sep</p>
</header>
<div class="wrap">
  <div class="filters">
    <div>
      <label for="state">State</label>
      <select id="state">
        <option value="all">Both states</option>
        <option value="MI">Michigan</option>
        <option value="PA">Pennsylvania</option>
      </select>
    </div>
    <div>
      <label>Period</label>
      <div class="seg">
        <label><input type="radio" name="grain" value="annual" checked><span>Annual</span></label>
        <label><input type="radio" name="grain" value="quarter"><span>Quarter</span></label>
      </div>
    </div>
  </div>

  <div class="grid3">
    <div class="card" data-panel="all">{plots['map_all']}</div>
    <div class="card" data-panel="MI">{plots['map_MI']}</div>
    <div class="card" data-panel="PA">{plots['map_PA']}</div>
    <div class="card" data-panel="all">{plots['bubbles_all']}</div>
    <div class="card" data-panel="MI">{plots['bubbles_MI']}</div>
    <div class="card" data-panel="PA">{plots['bubbles_PA']}</div>
    <div class="card" data-panel="all">{plots['bars_all']}</div>
    <div class="card" data-panel="MI">{plots['bars_MI']}</div>
    <div class="card" data-panel="PA">{plots['bars_PA']}</div>
  </div>
  <div class="card wide" data-panel="all" data-grain="annual" style="margin-top:16px">{plots['compare_all_annual']}</div>
  <div class="card wide" data-panel="all" data-grain="quarter">{plots['compare_all_quarter']}</div>
  <div class="card wide" data-panel="MI" data-grain="annual">{plots['compare_MI_annual']}</div>
  <div class="card wide" data-panel="MI" data-grain="quarter">{plots['compare_MI_quarter']}</div>
  <div class="card wide" data-panel="PA" data-grain="annual">{plots['compare_PA_annual']}</div>
  <div class="card wide" data-panel="PA" data-grain="quarter">{plots['compare_PA_quarter']}</div>
  <h2>Over time</h2>
  <div class="grid">
    <div class="card" data-panel="all">{plots['price_all']}</div>
    <div class="card" data-panel="all">{plots['volume_all']}</div>
    <div class="card" data-panel="MI">{plots['price_MI']}</div>
    <div class="card" data-panel="MI">{plots['vol_MI']}</div>
    <div class="card" data-panel="PA">{plots['price_PA']}</div>
    <div class="card" data-panel="PA">{plots['vol_PA']}</div>
    <div class="card" data-panel="MI">{plots['share_MI']}</div>
    <div class="card" data-panel="PA">{plots['share_PA']}</div>
  </div>

  <h2>Tables</h2>
  <div class="exports">
    <button type="button" onclick="downloadCsv('supplier')">Download supplier table (CSV)</button>
    <button type="button" onclick="downloadCsv('state')">Download state table (CSV)</button>
    <button type="button" onclick="downloadXlsx()">Download Excel (both tables)</button>
  </div>
  <h2>By supplier</h2>
  <div class="table-wrap">
  <table id="tbl">
    <thead><tr><th>State</th><th>Fiscal year</th><th>Supplier</th><th>Tons</th><th>Weighted $/t</th><th>Contract value</th><th>Share</th></tr></thead>
    <tbody></tbody>
  </table>
  </div>
  <h2>By state</h2>
  <div class="table-wrap">
  <table id="tbl-state">
    <thead><tr><th>State</th><th>Fiscal year</th><th>Tons</th><th>Weighted $/t</th><th>Contract value</th></tr></thead>
    <tbody></tbody>
  </table>
  </div>
  <p class="note">CSV and Excel download the tables as currently filtered by the State dropdown. Weighted price is total contract value divided by priced tonnage. PA FY2022–FY2023 volume is published without a supplier award and is excluded from share. FY2027 is the awarded upcoming winter. Quarterly view places each annual award in Q1 (Oct–Dec); Q2–Q4 are blank because the states do not publish quarterly contracted tons or prices.</p>
</div>
<script src="https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js"></script>
<script>
const rows = {json.dumps(table_rows)};
const stateRows = {json.dumps(state_rows)};
const tbody = document.querySelector("#tbl tbody");
const tbodyS = document.querySelector("#tbl-state tbody");
const sel = document.getElementById("state");
const grainInputs = document.querySelectorAll("input[name=grain]");
function fmtTons(n) {{ return n == null ? "—" : n.toLocaleString("en-US", {{maximumFractionDigits: 0}}); }}
function fmtMoney(n) {{ return n == null ? "—" : "$" + n.toLocaleString("en-US", {{minimumFractionDigits: 2, maximumFractionDigits: 2}}); }}
function fmtValue(n) {{ return n == null ? "—" : "$" + n.toLocaleString("en-US", {{maximumFractionDigits: 0}}); }}
function fmtShare(n) {{ return n == null ? "—" : (n * 100).toFixed(1) + "%"; }}
function stateName(code) {{ return code === "MI" ? "Michigan" : "Pennsylvania"; }}
function grainValue() {{
  const checked = document.querySelector("input[name=grain]:checked");
  return checked ? checked.value : "annual";
}}
function visible(list) {{
  const v = sel.value;
  if (v === "all") return list;
  const name = stateName(v);
  return list.filter(r => r.state === name);
}}
function paint() {{
  const v = sel.value;
  const grain = grainValue();
  document.querySelectorAll("[data-panel]").forEach(el => {{
    const p = el.getAttribute("data-panel");
    const g = el.getAttribute("data-grain");
    const panelOk = v === "all" ? p === "all" : p === v;
    const grainOk = !g || g === grain;
    el.style.display = (panelOk && grainOk) ? "" : "none";
  }});
  tbody.innerHTML = "";
  visible(rows).forEach(r => {{
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${{r.state}}</td><td>${{r.fy}}</td><td>${{r.vendor}}</td><td>${{fmtTons(r.tons)}}</td><td>${{fmtMoney(r.price)}}</td><td>${{fmtValue(r.value)}}</td><td>${{fmtShare(r.share)}}</td>`;
    tbody.appendChild(tr);
  }});
  tbodyS.innerHTML = "";
  visible(stateRows).forEach(r => {{
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${{r.state}}</td><td>${{r.fy}}</td><td>${{fmtTons(r.tons)}}</td><td>${{fmtMoney(r.price)}}</td><td>${{fmtValue(r.value)}}</td>`;
    tbodyS.appendChild(tr);
  }});
}}
function csvEscape(x) {{
  if (x == null || x === "") return "";
  const s = String(x);
  return /[",\\n]/.test(s) ? '"' + s.replaceAll('"', '""') + '"' : s;
}}
function saveBlob(name, blob) {{
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}}
function downloadCsv(kind) {{
  if (kind === "state") {{
    const header = ["State","Fiscal year","Contracted tons","Weighted $/ton","Contract value"];
    const lines = [header.join(",")].concat(visible(stateRows).map(r =>
      [r.state, r.fy, r.tons, r.price, r.value].map(csvEscape).join(",")));
    saveBlob("salt_by_state.csv", new Blob([lines.join("\\n")], {{type: "text/csv"}}));
    return;
  }}
  const header = ["State","Fiscal year","Supplier","Contracted tons","Weighted $/ton","Contract value","Volume share"];
  const lines = [header.join(",")].concat(visible(rows).map(r =>
    [r.state, r.fy, r.vendor, r.tons, r.price, r.value, r.share].map(csvEscape).join(",")));
  saveBlob("salt_by_supplier.csv", new Blob([lines.join("\\n")], {{type: "text/csv"}}));
}}
function downloadXlsx() {{
  if (typeof XLSX === "undefined") {{
    alert("Excel export needs an internet connection the first time (loads SheetJS). CSV still works offline.");
    return;
  }}
  const wb = XLSX.utils.book_new();
  const sup = visible(rows).map(r => ({{
    "State": r.state, "Fiscal year": r.fy, "Supplier": r.vendor,
    "Contracted tons": r.tons, "Weighted $/ton": r.price,
    "Contract value": r.value, "Volume share": r.share,
  }}));
  const st = visible(stateRows).map(r => ({{
    "State": r.state, "Fiscal year": r.fy, "Contracted tons": r.tons,
    "Weighted $/ton": r.price, "Contract value": r.value,
  }}));
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(sup), "By supplier");
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(st), "By state");
  XLSX.writeFile(wb, "salt_contract_tables.xlsx");
}}
sel.addEventListener("change", paint);
grainInputs.forEach(el => el.addEventListener("change", paint));
paint();
</script>
</body>
</html>
"""
    os.makedirs(OUT, exist_ok=True)
    with open(HTML, "w") as fh:
        fh.write(html)
    print(HTML)


if __name__ == "__main__":
    main()
