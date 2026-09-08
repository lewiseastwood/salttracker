"""Plotly figures used by both the Streamlit app and the static HTML pack."""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go

from theme import PRICE_LINE, STATE_COLORS, STATE_NAMES, VENDOR_COLORS, style


def _fy_axis(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["fy_label"] = "FY" + out["fiscal_year"].astype(int).astype(str)
    return out.sort_values("fiscal_year")


def _vendor_year(vendor_df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """One row per supplier × fiscal year, combining states when both are in view."""
    named = vendor_df.copy()
    if "is_attributed" in named.columns:
        named = named[named["is_attributed"]]
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
    fys = sorted(int(x) for x in named["fiscal_year"].unique())
    vendors = sorted(named["vendor"].unique())
    grid = pd.MultiIndex.from_product(
        [vendors, fys], names=["vendor", "fiscal_year"]
    ).to_frame(index=False)
    out = grid.merge(rolled, on=["vendor", "fiscal_year"], how="left")
    out["fy_label"] = "FY " + out["fiscal_year"].astype(int).astype(str)
    return out


def volume_price_comparison(vendor_df: pd.DataFrame, metric: str = "weighted_avg_price") -> go.Figure:
    """Dual-axis volume bars + average price, grouped by vendor then fiscal year."""
    df = _vendor_year(vendor_df, metric)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title="Volume-Pricing Comparison")
        return style(fig, height=520)

    fig.add_trace(go.Bar(
        x=[df["vendor"], df["fy_label"]],
        y=df["contracted_tons"] / 1000.0,
        marker=dict(color=[VENDOR_COLORS.get(v, "#4E79A7") for v in df["vendor"]]),
        name="Volume (T)",
        hovertemplate="%{x}<br>%{customdata:,.0f} tons<extra>Volume</extra>",
        customdata=df["contracted_tons"],
    ))
    shown_price_legend = False
    for vendor, g in df.groupby("vendor", sort=False):
        g = g.sort_values("fiscal_year")
        labels = [f"${p:,.2f}" if pd.notna(p) else "" for p in g["price"]]
        fig.add_trace(go.Scatter(
            x=[g["vendor"], g["fy_label"]],
            y=g["price"],
            mode="lines+markers+text",
            text=labels,
            textposition="top center",
            textfont=dict(size=10, color="#3D3D3D"),
            cliponaxis=False,
            line=dict(color=PRICE_LINE, width=2),
            marker=dict(size=7, color=PRICE_LINE),
            yaxis="y2",
            name="Avg. Price Per Ton",
            showlegend=not shown_price_legend,
            legendgroup="price",
            connectgaps=False,
            hovertemplate="%{x}<br>%{y:$,.2f}/ton<extra>Avg. price</extra>",
        ))
        shown_price_legend = True

    ymax = pd.to_numeric(df["price"], errors="coerce").max()
    fig.update_layout(
        title="Volume-Pricing Comparison",
        showlegend=False,
        bargap=0.28,
        hovermode="closest",
        margin=dict(l=64, r=72, t=56, b=88),
        yaxis=dict(title="Volume (T)", ticksuffix="K", rangemode="tozero", gridcolor="#D7DCE0"),
        yaxis2=dict(
            title="Avg. Price Per Ton",
            overlaying="y",
            side="right",
            tickprefix="$",
            tickformat=",.2f",
            rangemode="tozero",
            range=[0, (float(ymax) * 1.25) if pd.notna(ymax) else 120],
            showgrid=False,
        ),
        xaxis=dict(tickangle=-90, tickfont=dict(size=10)),
    )
    return style(fig, height=560)


def _latest(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "fiscal_year" not in df.columns:
        return df
    return df[df["fiscal_year"] == int(df["fiscal_year"].max())].copy()


def _vendor_totals(vendor_df: pd.DataFrame, metric: str) -> pd.DataFrame:
    named = vendor_df.copy()
    if "is_attributed" in named.columns:
        named = named[named["is_attributed"]]
    named = named[named["vendor"].notna() & named["vendor"].ne("Unattributed")]
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


def state_overview_map(state_df: pd.DataFrame) -> go.Figure:
    """USA map with only Michigan and Pennsylvania filled — the two states in scope."""
    g = _latest(state_df)
    fy = int(g["fiscal_year"].max()) if not g.empty else None
    fig = go.Figure()
    if not g.empty:
        hover = pd.DataFrame({
            "name": g["state"].map(STATE_NAMES),
            "price": g["weighted_avg_price"],
        })
        fig.add_trace(go.Choropleth(
            locations=g["state"],
            locationmode="USA-states",
            z=g["contracted_tons"].fillna(0),
            customdata=hover.to_numpy(),
            colorscale=[[0, "#D7EEF2"], [0.5, "#4E9AA8"], [1, "#1B3A4B"]],
            colorbar=dict(title="Tons", thickness=14, len=0.65, outlinewidth=0),
            hovertemplate="%{customdata[0]}<br>%{z:,.0f} tons<br>%{customdata[1]:$,.2f}/ton<extra></extra>",
            marker_line_color="#FFFFFF",
            marker_line_width=0.6,
            showscale=True,
        ))
    fig.update_layout(
        title=f"Contracted volume by state · FY{fy}" if fy else "Contracted volume by state",
        geo=dict(
            scope="usa",
            projection=dict(type="albers usa"),
            showlakes=False,
            bgcolor="white",
            landcolor="#EEF1F3",
            subunitcolor="#D7DCE0",
            lakecolor="white",
        ),
        margin=dict(l=0, r=0, t=48, b=0),
    )
    return style(fig, height=400)


def vendor_bubbles(vendor_df: pd.DataFrame) -> go.Figure:
    """Packed-style bubbles of supplier volume for the latest year in view."""
    df = _vendor_totals(_latest(vendor_df), "weighted_avg_price")
    df = df[df["contracted_tons"].fillna(0) > 0].sort_values("contracted_tons", ascending=False)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title="Supplier volume")
        return style(fig, height=400)

    names = df["vendor"].tolist()
    tons = df["contracted_tons"].tolist()
    n = len(names)
    xs, ys = [], []
    if n == 1:
        xs, ys = [0], [0]
    else:
        xs.append(0.0)
        ys.append(0.0)
        for i in range(1, n):
            ang = 2 * math.pi * (i - 1) / (n - 1)
            xs.append(1.35 * math.cos(ang))
            ys.append(1.35 * math.sin(ang))

    fig.add_trace(go.Scatter(
        x=xs, y=ys,
        mode="markers+text",
        marker=dict(
            size=[max(48, (t / max(tons)) ** 0.5 * 140) for t in tons],
            color=[VENDOR_COLORS.get(v, "#4E79A7") for v in names],
            line=dict(width=2, color="white"),
            opacity=0.92,
        ),
        text=[f"<b>{v}</b><br>{t:,.0f}" for v, t in zip(names, tons)],
        textfont=dict(size=11, color="#1A2332"),
        textposition="middle center",
        hovertemplate="%{text} tons<extra></extra>",
    ))
    fig.update_xaxes(visible=False, range=[-2.4, 2.4])
    fig.update_yaxes(visible=False, range=[-2.2, 2.2], scaleanchor="x", scaleratio=1)
    fig.update_layout(
        title="Supplier volume",
        showlegend=False,
        plot_bgcolor="white",
        margin=dict(l=10, r=10, t=48, b=10),
    )
    return style(fig, height=400)


def vendor_price_bars(vendor_df: pd.DataFrame, metric: str = "weighted_avg_price") -> go.Figure:
    df = _vendor_totals(_latest(vendor_df), metric)
    df = df[df["price"].notna()].sort_values("price", ascending=True)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title="Avg. Price Per Ton")
        return style(fig, height=400)
    fig.add_trace(go.Bar(
        y=df["vendor"],
        x=df["price"],
        orientation="h",
        marker_color=[VENDOR_COLORS.get(v, "#4E79A7") for v in df["vendor"]],
        text=[f"${p:,.2f}" for p in df["price"]],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>%{x:$,.2f}/ton<extra></extra>",
    ))
    fig.update_xaxes(title=None, tickprefix="$", rangemode="tozero", ticksuffix="")
    fig.update_yaxes(title=None)
    fig.update_layout(title="Avg. Price Per Ton", margin=dict(l=16, r=72, t=48, b=24))
    return style(fig, height=400)


def price_timeseries(state_df: pd.DataFrame, metric: str) -> go.Figure:
    """State-level contracted price over fiscal years."""
    fig = go.Figure()
    for code, g in _fy_axis(state_df).groupby("state"):
        fig.add_trace(go.Scatter(
            x=g["fy_label"], y=g[metric], name=STATE_NAMES.get(code, code),
            mode="lines+markers",
            line=dict(color=STATE_COLORS.get(code, "#1B3A4B"), width=2.5),
            marker=dict(size=8),
            hovertemplate="%{y:$,.2f}/ton<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="USD per short ton", tickprefix="$", tickformat=",.0f")
    fig.update_xaxes(title="Fiscal year")
    fig.update_layout(title="Contracted price over time")
    return style(fig)


def volume_timeseries(state_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for code, g in _fy_axis(state_df).groupby("state"):
        fig.add_trace(go.Bar(
            x=g["fy_label"], y=g["contracted_tons"], name=STATE_NAMES.get(code, code),
            marker_color=STATE_COLORS.get(code, "#1B3A4B"),
            hovertemplate="%{y:,.0f} tons<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="Contracted tons", tickformat=",.0f")
    fig.update_xaxes(title="Fiscal year")
    fig.update_layout(barmode="group", title="Contracted volume over time")
    return style(fig)


def vendor_price(vendor_df: pd.DataFrame, metric: str, state_code: str) -> go.Figure:
    fig = go.Figure()
    g = _fy_axis(vendor_df[vendor_df["state"] == state_code])
    g = g[g["is_attributed"] & g[metric].notna()]
    for vendor, vg in g.groupby("vendor"):
        fig.add_trace(go.Scatter(
            x=vg["fy_label"], y=vg[metric], name=vendor, mode="lines+markers",
            line=dict(color=VENDOR_COLORS.get(vendor, "#1B3A4B"), width=2.2),
            marker=dict(size=7),
            hovertemplate="%{y:$,.2f}/ton<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="USD per short ton", tickprefix="$")
    fig.update_xaxes(title="Fiscal year")
    fig.update_layout(title=f"{STATE_NAMES.get(state_code, state_code)} — price by supplier")
    return style(fig)


def vendor_volume(vendor_df: pd.DataFrame, state_code: str) -> go.Figure:
    g = _fy_axis(vendor_df[vendor_df["state"] == state_code])
    fig = go.Figure()
    for vendor, vg in g.groupby("vendor"):
        fig.add_trace(go.Bar(
            x=vg["fy_label"], y=vg["contracted_tons"], name=vendor,
            marker_color=VENDOR_COLORS.get(vendor, "#9AA3B2"),
            hovertemplate="%{y:,.0f} tons<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="Contracted tons", tickformat=",.0f")
    fig.update_xaxes(title="Fiscal year")
    fig.update_layout(
        barmode="stack",
        title=f"{STATE_NAMES.get(state_code, state_code)} — volume by supplier",
    )
    return style(fig)


def vendor_share(vendor_df: pd.DataFrame, state_code: str) -> go.Figure:
    g = _fy_axis(vendor_df[(vendor_df["state"] == state_code) & vendor_df["is_attributed"]])
    fig = go.Figure()
    for vendor, vg in g.groupby("vendor"):
        fig.add_trace(go.Scatter(
            x=vg["fy_label"], y=vg["volume_share"], name=vendor,
            mode="lines", stackgroup="one",
            line=dict(width=0.5, color=VENDOR_COLORS.get(vendor, "#9AA3B2")),
            fillcolor=VENDOR_COLORS.get(vendor, "#9AA3B2"),
            hovertemplate="%{y:.1%}<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="Share of attributed volume", tickformat=".0%")
    fig.update_xaxes(title="Fiscal year")
    fig.update_layout(title=f"{STATE_NAMES.get(state_code, state_code)} — supplier share")
    return style(fig)
