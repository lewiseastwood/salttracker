"""Plotly figures used by both the Streamlit app and the static HTML pack."""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from theme import PRICE_LINE, STATE_COLORS, STATE_NAMES, VENDOR_COLORS, style

SHORT_VENDOR = {
    "Riverside Construction Materials": "Riverside",
}

# Fiscal-year quarters. Q1 is Oct–Dec, the start of the winter the contract covers.
# Source documents publish one award per fiscal year, so quarterly charts place
# that annual award in Q1 and leave Q2–Q4 blank rather than inventing a split.
FY_QUARTERS = (1, 2, 3, 4)
AWARD_QUARTER = 1


def _short(vendor: str) -> str:
    return SHORT_VENDOR.get(vendor, vendor)


def _named(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "is_attributed" in out.columns:
        out = out[out["is_attributed"]]
    return out[out["vendor"].notna() & out["vendor"].ne("Unattributed")]


def _with_period(df: pd.DataFrame, grain: str) -> pd.DataFrame:
    """Label rows for annual or quarterly axes without splitting annual totals."""
    if df.empty:
        return df
    base = df.copy()
    if grain != "quarter":
        base["period"] = "FY " + base["fiscal_year"].astype(int).astype(str)
        base["period_sort"] = base["fiscal_year"].astype(int) * 10
        return base

    rows = []
    measure_cols = [c for c in ("contracted_tons", "priced_tons", "price",
                                "weighted_avg_price", "simple_avg_price",
                                "volume_share", "contract_value") if c in base.columns]
    for _, row in base.iterrows():
        fy = int(row["fiscal_year"])
        for q in FY_QUARTERS:
            item = row.copy()
            item["period"] = f"FY{fy} Q{q}"
            item["period_sort"] = fy * 10 + q
            if q != AWARD_QUARTER:
                for col in measure_cols:
                    item[col] = pd.NA
            rows.append(item)
    return pd.DataFrame(rows)


def _vendor_year(vendor_df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """One row per supplier × fiscal year, combining states when both are in view."""
    named = _named(vendor_df)
    if named.empty:
        return named
    rolled = (
        named.groupby(["vendor", "fiscal_year"], as_index=False)
        .agg(
            contracted_tons=("contracted_tons", "sum"),
            priced_tons=("priced_tons", "sum"),
            contract_value=("contract_value", "sum"),
            simple_avg_price=("simple_avg_price", "mean"),
        )
    )
    weighted = rolled["contract_value"].div(rolled["priced_tons"].replace(0, pd.NA))
    rolled["price"] = (
        rolled["simple_avg_price"] if metric == "simple_avg_price"
        else weighted.fillna(rolled["simple_avg_price"])
    )
    # Only years that supplier actually has a row — do not grid every vendor
    # onto FY2022–FY2027 or PA FY2024 prices sit on an FY2022 tick.
    return rolled


def _ton_tick_text(n: float) -> str:
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.0f}M" if abs(v - round(v)) < 1e-9 else f"{v:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return "0"


def _vendor_order_by_latest_volume(rolled: pd.DataFrame, vendors: list[str]) -> list[str]:
    if rolled.empty or "fiscal_year" not in rolled.columns:
        return vendors
    latest = int(rolled["fiscal_year"].max())
    vol = (
        rolled[rolled["fiscal_year"] == latest]
        .groupby("vendor")["contracted_tons"]
        .sum()
    )

    def key(v: str) -> float:
        x = vol.get(v, 0)
        return float(x) if pd.notna(x) else 0.0

    return sorted(vendors, key=key, reverse=True)


def volume_price_comparison(
    vendor_df: pd.DataFrame,
    metric: str = "weighted_avg_price",
    grain: str = "annual",
) -> go.Figure:
    """Volume bars + average price, one panel per supplier.

    Every facet uses the same fiscal-year category set so a one-year
    supplier like Riverside is not drawn under a neighbour's FY2024 tick.
    Volume axis labels only on the left of each row; price only on the right.
    """
    rolled = _vendor_year(vendor_df, metric)
    fys = sorted(int(x) for x in vendor_df["fiscal_year"].dropna().unique())
    vendors = _vendor_order_by_latest_volume(
        rolled, sorted(rolled["vendor"].unique()) if not rolled.empty else [],
    )
    if rolled.empty or not fys:
        fig = go.Figure()
        fig.update_layout(title="Volume-Pricing Comparison")
        return style(fig, height=480)

    grid = pd.MultiIndex.from_product(
        [vendors, fys], names=["vendor", "fiscal_year"],
    ).to_frame(index=False)
    df = _with_period(grid.merge(rolled, on=["vendor", "fiscal_year"], how="left"), grain)
    periods = list(dict.fromkeys(df.sort_values("period_sort")["period"].tolist()))

    n = len(vendors)
    ncols = min(3, n)
    nrows = max(1, math.ceil(n / ncols))
    specs = []
    for r in range(nrows):
        row = []
        for c in range(ncols):
            row.append({"secondary_y": True} if r * ncols + c < n else None)
        specs.append(row)
    titles = [_short(v) for v in vendors] + [""] * (nrows * ncols - n)
    fig = make_subplots(
        rows=nrows, cols=ncols,
        shared_xaxes=False,
        shared_yaxes=False,
        specs=specs,
        subplot_titles=titles,
        horizontal_spacing=0.05,
        vertical_spacing=0.16 if nrows > 1 else 0.12,
    )

    ton_max = pd.to_numeric(df["contracted_tons"], errors="coerce").max()
    y1_top = (float(ton_max) * 1.12) if pd.notna(ton_max) and ton_max else 1
    step = 200_000
    tickvals = list(range(0, int(y1_top) + step, step))
    if tickvals[-1] < y1_top:
        tickvals.append(int(math.ceil(y1_top / step) * step))
    ticktext = [_ton_tick_text(v) for v in tickvals]
    ymax = pd.to_numeric(df["price"], errors="coerce").max()
    y2_top = (float(ymax) * 1.18) if pd.notna(ymax) else 120

    for i, vendor in enumerate(vendors):
        r, c = divmod(i, ncols)
        r, c = r + 1, c + 1
        row_count = ncols if r < nrows else n - (nrows - 1) * ncols
        show_vol = c == 1
        show_price = c == row_count
        g = df[df["vendor"] == vendor].sort_values("period_sort")
        tons = [None if pd.isna(t) or float(t) == 0 else float(t) for t in g["contracted_tons"]]
        fig.add_trace(
            go.Bar(
                x=g["period"], y=tons,
                marker_color=VENDOR_COLORS.get(vendor, "#4E79A7"),
                name="Contracted tons",
                legendgroup="tons",
                showlegend=i == 0,
                hovertemplate="%{x}<br>%{y:,.0f} tons<extra>Volume</extra>",
            ),
            row=r, col=c, secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=g["period"], y=g["price"],
                mode="lines+markers",
                line=dict(color=PRICE_LINE, width=2),
                marker=dict(size=6, color=PRICE_LINE),
                name="Avg. $/ton",
                legendgroup="price",
                showlegend=i == 0,
                connectgaps=False,
                hovertemplate="%{x}<br>%{y:$,.2f}/ton<extra>Avg. price</extra>",
            ),
            row=r, col=c, secondary_y=True,
        )
        fig.update_xaxes(
            categoryorder="array",
            categoryarray=periods,
            tickangle=-90,
            tickfont=dict(size=9),
            row=r, col=c,
        )
        fig.update_yaxes(
            range=[0, y1_top],
            tickvals=tickvals,
            ticktext=ticktext if show_vol else [""] * len(tickvals),
            showticklabels=show_vol,
            ticks="outside" if show_vol else "",
            title_text="Contracted tons" if show_vol else "",
            row=r, col=c, secondary_y=False,
        )
        fig.update_yaxes(
            range=[0, y2_top],
            showgrid=False,
            tickprefix="$" if show_price else "",
            tickformat=",.0f",
            showticklabels=show_price,
            ticks="outside" if show_price else "",
            title_text="Avg. $/ton" if show_price else "",
            row=r, col=c, secondary_y=True,
        )

    states = list(vendor_df["state"].dropna().unique()) if "state" in vendor_df.columns else []
    title = "Volume-Pricing Comparison"
    if len(states) == 1:
        title += f" — {STATE_NAMES.get(states[0], states[0])}"
    fig.update_layout(
        title=title,
        bargap=0.35,
        hovermode="closest",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.12, x=0.5, xanchor="center",
            title=None,
        ),
        margin=dict(l=72, r=64, t=88, b=88),
    )
    price_no_vol = df["price"].notna() & df["contracted_tons"].isna()
    if grain == "quarter":
        price_no_vol = price_no_vol & df["period"].str.endswith("Q1")
    if price_no_vol.any():
        fig.add_annotation(
            text="Bars absent where contracted tonnage was not published (PA FY2025 renewal).",
            xref="paper", yref="paper", x=0, y=-0.14,
            showarrow=False, xanchor="left",
            font=dict(size=11, color="#5C6770"),
        )
    return style(fig, height=420 * nrows + 40)


def _latest(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "fiscal_year" not in df.columns:
        return df
    return df[df["fiscal_year"] == int(df["fiscal_year"].max())].copy()


def state_overview_map(state_df: pd.DataFrame) -> go.Figure:
    """Back-compat alias; the choropleth was misleading and was removed."""
    return state_volume_bars(state_df)


def _vendor_totals(vendor_df: pd.DataFrame, metric: str) -> pd.DataFrame:
    named = _named(vendor_df)
    if named.empty:
        return named
    rolled = (
        named.groupby("vendor", as_index=False)
        .agg(
            contracted_tons=("contracted_tons", "sum"),
            priced_tons=("priced_tons", "sum"),
            contract_value=("contract_value", "sum"),
            simple_avg_price=("simple_avg_price", "mean"),
        )
    )
    weighted = rolled["contract_value"].div(rolled["priced_tons"].replace(0, pd.NA))
    rolled["price"] = (
        rolled["simple_avg_price"] if metric == "simple_avg_price"
        else weighted.fillna(rolled["simple_avg_price"])
    )
    return rolled


def state_volume_bars(state_df: pd.DataFrame) -> go.Figure:
    """Two (or one) volume bars for the latest year — not a choropleth."""
    g = _latest(state_df)
    fy = int(g["fiscal_year"].max()) if not g.empty else None
    fig = go.Figure()
    if not g.empty:
        g = g.sort_values("state")
        fig.add_trace(go.Bar(
            x=[STATE_NAMES.get(c, c) for c in g["state"]],
            y=g["contracted_tons"],
            marker_color=[STATE_COLORS.get(c, "#1B3A4B") for c in g["state"]],
            text=[f"{t:,.0f} t" if pd.notna(t) else "—" for t in g["contracted_tons"]],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{x}<br>%{y:,.0f} tons<extra></extra>",
        ))
    fig.update_yaxes(title="Contracted tons", tickformat=",.0f", rangemode="tozero")
    fig.update_layout(
        title=f"Contracted volume · FY{fy}" if fy else "Contracted volume",
        margin=dict(l=48, r=24, t=48, b=32),
        showlegend=False,
    )
    return style(fig, height=380)


def vendor_bubbles(vendor_df: pd.DataFrame, state_code: str | None = None) -> go.Figure:
    """Treemap of supplier volume for one state, latest year in view."""
    src = vendor_df if state_code is None else vendor_df[vendor_df["state"] == state_code]
    latest = _latest(src)
    fy = int(latest["fiscal_year"].max()) if not latest.empty else None
    df = _vendor_totals(latest, "weighted_avg_price")
    df = df[df["contracted_tons"].fillna(0) > 0].sort_values("contracted_tons", ascending=False)
    where = STATE_NAMES.get(state_code, "") if state_code else ""
    title = " · ".join(p for p in [f"{where} supplier volume".strip(), f"FY{fy}" if fy else ""] if p)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title=title or "Supplier volume")
        return style(fig, height=380)
    fig.add_trace(go.Treemap(
        labels=[_short(v) for v in df["vendor"]],
        parents=[""] * len(df),
        values=df["contracted_tons"],
        texttemplate="%{label}<br>%{value:,.0f} t<br>%{percentRoot:.0%}",
        textfont=dict(size=13, color="#1A2332"),
        marker=dict(
            colors=[VENDOR_COLORS.get(v, "#4E79A7") for v in df["vendor"]],
            line=dict(width=2, color="white"),
        ),
        hovertemplate="%{label}<br>%{value:,.0f} tons (%{percentRoot:.0%})<extra></extra>",
        root=dict(color="#FFFFFF"),
    ))
    fig.update_layout(title=title, margin=dict(l=8, r=8, t=48, b=8))
    return style(fig, height=380)


def vendor_price_bars(
    vendor_df: pd.DataFrame,
    metric: str = "weighted_avg_price",
    state_code: str | None = None,
) -> go.Figure:
    src = vendor_df if state_code is None else vendor_df[vendor_df["state"] == state_code]
    latest = _latest(src)
    fy = int(latest["fiscal_year"].max()) if not latest.empty else None
    df = _vendor_totals(latest, metric)
    df = df[df["price"].notna()].sort_values("price", ascending=True)
    where = STATE_NAMES.get(state_code, "") if state_code else ""
    title = " · ".join(p for p in [f"{where} avg. price per ton".strip(), f"FY{fy}" if fy else ""] if p)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title=title or "Avg. Price Per Ton")
        return style(fig, height=380)
    fig.add_trace(go.Bar(
        y=[_short(v) for v in df["vendor"]],
        x=df["price"],
        orientation="h",
        marker_color=[VENDOR_COLORS.get(v, "#4E79A7") for v in df["vendor"]],
        text=[f"${p:,.2f}" for p in df["price"]],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>%{x:$,.2f}/ton<extra></extra>",
    ))
    fig.update_xaxes(title=None, tickprefix="$", rangemode="tozero")
    fig.update_yaxes(title=None, automargin=True)
    fig.update_layout(title=title, margin=dict(l=8, r=72, t=48, b=24))
    return style(fig, height=380)


def price_timeseries(
    state_df: pd.DataFrame, metric: str, grain: str = "annual",
) -> go.Figure:
    fig = go.Figure()
    for code, g in _with_period(state_df, grain).groupby("state"):
        g = g.sort_values("period_sort")
        fig.add_trace(go.Scatter(
            x=g["period"], y=g[metric], name=STATE_NAMES.get(code, code),
            mode="lines+markers",
            line=dict(color=STATE_COLORS.get(code, "#1B3A4B"), width=2.5),
            marker=dict(size=8),
            connectgaps=False,
            hovertemplate="%{y:$,.2f}/ton<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="USD per short ton", tickprefix="$", tickformat=",.0f")
    fig.update_xaxes(title=None, tickangle=-45 if grain == "quarter" else 0)
    fig.update_layout(
        title="Contracted price over time",
        margin=dict(l=56, r=24, t=72, b=48),
    )
    if "PA" in set(state_df["state"]):
        fig.add_annotation(
            text="Pennsylvania FY2022–FY2023: estimates list volume, not a supplier award — no PA price those years.",
            xref="paper", yref="paper", x=0, y=1.14,
            showarrow=False, xanchor="left",
            font=dict(size=11, color="#5C6770"),
        )
    fig.add_annotation(
        text="Both states post delivered $/ton (not FOB). Programs still differ, so the MI–PA gap is not a like-for-like bid spread.",
        xref="paper", yref="paper", x=0, y=-0.18,
        showarrow=False, xanchor="left",
        font=dict(size=11, color="#5C6770"),
    )
    return style(fig, height=440)


def volume_timeseries(state_df: pd.DataFrame, grain: str = "annual") -> go.Figure:
    fig = go.Figure()
    for code, g in _with_period(state_df, grain).groupby("state"):
        g = g.sort_values("period_sort")
        fig.add_trace(go.Bar(
            x=g["period"], y=g["contracted_tons"], name=STATE_NAMES.get(code, code),
            marker_color=STATE_COLORS.get(code, "#1B3A4B"),
            hovertemplate="%{y:,.0f} tons<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="Contracted tons", tickformat=",.0f")
    fig.update_xaxes(title=None, tickangle=-45 if grain == "quarter" else 0)
    fig.update_layout(barmode="group", title="Contracted volume over time")
    return style(fig)


def vendor_price(
    vendor_df: pd.DataFrame, metric: str, state_code: str, grain: str = "annual",
) -> go.Figure:
    fig = go.Figure()
    g = vendor_df[(vendor_df["state"] == state_code) & vendor_df["is_attributed"] & vendor_df[metric].notna()]
    g = _with_period(g, grain)
    for vendor, vg in g.groupby("vendor"):
        vg = vg.sort_values("period_sort")
        fig.add_trace(go.Scatter(
            x=vg["period"], y=vg[metric], name=_short(vendor), mode="lines+markers",
            line=dict(color=VENDOR_COLORS.get(vendor, "#1B3A4B"), width=2.2),
            marker=dict(size=7),
            connectgaps=False,
            hovertemplate="%{y:$,.2f}/ton<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="USD per short ton", tickprefix="$")
    fig.update_xaxes(title=None, tickangle=-45 if grain == "quarter" else 0)
    fig.update_layout(
        title=f"{STATE_NAMES.get(state_code, state_code)} — price by supplier",
        margin=dict(l=56, r=24, t=72, b=48),
    )
    if state_code == "PA":
        fig.add_annotation(
            text="No published supplier award in FY2022–FY2023. First priced year is FY2024 (ARS ~$81.79, Morton ~$80.77).",
            xref="paper", yref="paper", x=0, y=1.14,
            showarrow=False, xanchor="left",
            font=dict(size=11, color="#5C6770"),
        )
    return style(fig)


def vendor_volume(vendor_df: pd.DataFrame, state_code: str, grain: str = "annual") -> go.Figure:
    g = _named(vendor_df[vendor_df["state"] == state_code])
    g = _with_period(g, grain)
    fig = go.Figure()
    for vendor, vg in g.groupby("vendor"):
        vg = vg.sort_values("period_sort")
        fig.add_trace(go.Bar(
            x=vg["period"], y=vg["contracted_tons"], name=_short(vendor),
            marker_color=VENDOR_COLORS.get(vendor, "#9AA3B2"),
            hovertemplate="%{y:,.0f} tons<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="Contracted tons", tickformat=",.0f")
    fig.update_xaxes(title=None, tickangle=-45 if grain == "quarter" else 0)
    fig.update_layout(
        barmode="stack",
        title=f"{STATE_NAMES.get(state_code, state_code)} — volume by supplier",
    )
    return style(fig)


def vendor_share(vendor_df: pd.DataFrame, state_code: str) -> go.Figure:
    g = vendor_df[(vendor_df["state"] == state_code) & vendor_df["is_attributed"]].copy()
    g["fy_label"] = "FY " + g["fiscal_year"].astype(int).astype(str)
    g = g.sort_values(["fiscal_year", "vendor"])
    fig = go.Figure()
    for vendor, vg in g.groupby("vendor"):
        vg = vg.sort_values("fiscal_year")
        fig.add_trace(go.Scatter(
            x=vg["fy_label"], y=vg["volume_share"], name=_short(vendor),
            mode="lines", stackgroup="one",
            line=dict(width=0.5, color=VENDOR_COLORS.get(vendor, "#9AA3B2")),
            fillcolor=VENDOR_COLORS.get(vendor, "#9AA3B2"),
            hovertemplate="%{y:.1%}<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="Share of attributed volume", tickformat=".0%", range=[0, 1])
    fig.update_xaxes(title="Fiscal year")
    fig.update_layout(title=f"{STATE_NAMES.get(state_code, state_code)} — supplier share")
    fig = style(fig, height=440)
    fig.update_layout(
        legend=dict(
            orientation="h", yanchor="top", y=-0.22, x=0, xanchor="left",
            title=None, font=dict(size=12),
        ),
        margin=dict(l=56, r=24, t=48, b=96),
        hovermode="closest",
    )
    return fig
