"""Plotly figures used by both the Streamlit app and the static HTML pack."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from theme import STATE_COLORS, STATE_NAMES, VENDOR_COLORS, style


def _fy_axis(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["fy_label"] = "FY" + out["fiscal_year"].astype(int).astype(str)
    return out.sort_values("fiscal_year")


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
