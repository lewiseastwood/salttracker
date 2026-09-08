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
        "price_all": fig_html(charts.price_timeseries(state, "weighted_avg_price")),
        "volume_all": fig_html(charts.volume_timeseries(state)),
    }
    for code in ("MI", "PA"):
        plots[f"price_{code}"] = fig_html(charts.vendor_price(named, "weighted_avg_price", code))
        plots[f"vol_{code}"] = fig_html(charts.vendor_volume(vendor[vendor["state"] == code], code))
        plots[f"share_{code}"] = fig_html(charts.vendor_share(named, code))

    table_rows = []
    for _, row in named.sort_values(["state", "fiscal_year", "vendor"]).iterrows():
        price = row["weighted_avg_price"]
        share = row["volume_share"]
        tons = row["contracted_tons"]
        table_rows.append({
            "state": STATE_NAMES.get(row["state"], row["state"]),
            "fy": f"FY{int(row['fiscal_year'])}",
            "vendor": row["vendor"],
            "tons": "" if pd.isna(tons) else f"{tons:,.0f}",
            "price": "" if pd.isna(price) else f"${price:,.2f}",
            "share": "" if pd.isna(share) else f"{share:.1%}",
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
  .wrap {{ max-width:1200px; margin:0 auto; padding:24px 40px 48px; }}
  .filters {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:20px; }}
  .filters label {{ font-size:0.75rem; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted); display:block; margin-bottom:4px; }}
  select {{ font: inherit; padding:8px 10px; border:1px solid var(--line); border-radius:6px; background:#fff; min-width:180px; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:8px; }}
  h2 {{ font-family: Georgia, serif; color:var(--navy); font-size:1.15rem; margin:28px 0 10px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; font-size:0.9rem; }}
  th {{ text-align:left; background:var(--navy); color:#fff; padding:8px 10px; font-weight:600; }}
  td {{ border-bottom:1px solid var(--line); padding:8px 10px; }}
  tr:hover td {{ background:#f7f3ee; }}
  .exports {{ margin:12px 0 28px; display:flex; gap:10px; }}
  .exports a {{ color:#fff; background:var(--navy); text-decoration:none; padding:8px 14px; border-radius:6px; font-size:0.85rem; }}
  .note {{ color:var(--muted); font-size:0.85rem; margin-top:24px; }}
  @media (max-width: 900px) {{ .grid {{ grid-template-columns:1fr; }} .wrap, header {{ padding-left:18px; padding-right:18px; }} }}
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
  </div>

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

  <h2>Supplier detail</h2>
  <div class="exports">
    <a href="salt_contracts_by_vendor.csv" download>Download supplier CSV</a>
    <a href="salt_contracts_by_state.csv" download>Download state CSV</a>
    <a href="salt_contract_tracker.xlsx" download>Download Excel workbook</a>
  </div>
  <table id="tbl">
    <thead><tr><th>State</th><th>Fiscal year</th><th>Supplier</th><th>Tons</th><th>Weighted $/t</th><th>Share</th></tr></thead>
    <tbody></tbody>
  </table>
  <p class="note">Weighted price is total contract value divided by priced tonnage. PA FY2022–FY2023 volume is published without a supplier award and is excluded from share. FY2027 is the awarded upcoming winter.</p>
</div>
<script>
const rows = {json.dumps(table_rows)};
const tbody = document.querySelector("#tbl tbody");
const sel = document.getElementById("state");
function paint() {{
  const v = sel.value;
  document.querySelectorAll("[data-panel]").forEach(el => {{
    const p = el.getAttribute("data-panel");
    el.style.display = (v === "all" || p === "all" || p === v) ? "" : "none";
  }});
  tbody.innerHTML = "";
  rows.filter(r => v === "all" || r.state === (v === "MI" ? "Michigan" : "Pennsylvania") || (v === "all"))
      .forEach(r => {{
        if (v !== "all" && r.state !== (v === "MI" ? "Michigan" : "Pennsylvania")) return;
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${{r.state}}</td><td>${{r.fy}}</td><td>${{r.vendor}}</td><td>${{r.tons}}</td><td>${{r.price}}</td><td>${{r.share}}</td>`;
        tbody.appendChild(tr);
      }});
}}
sel.addEventListener("change", paint);
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
