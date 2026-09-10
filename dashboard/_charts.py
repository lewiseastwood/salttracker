"""Plotly figures used by both the Streamlit app and the static HTML pack."""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from _theme import INK, LINE, NAVY, PAPER, PRICE_LINE, STATE_COLORS, STATE_NAMES, VENDOR_COLORS, WHITE, style
from _briefing import STATE_VOLUME_MEASURE, volume_label

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


def _y_none(series) -> list:
    """Plotly draws a continuous line through None when connectgaps is True."""
    out = []
    for v in series:
        if v is None or pd.isna(v):
            out.append(None)
        else:
            try:
                n = float(v)
            except (TypeError, ValueError):
                out.append(None)
                continue
            out.append(None if n != n else n)
    return out


def _periods(df: pd.DataFrame) -> list[str]:
    if df.empty or "period" not in df.columns:
        return []
    return list(dict.fromkeys(df.sort_values("period_sort")["period"].tolist()))


def _quarter_tick_label(period: str) -> str:
    """Show Q1–Q4 on the axis. Year sits on Q1 so later quarters stay readable."""
    label = str(period)
    if " Q" not in label:
        return label
    fy_part, q_part = label.replace("FY", "").split()
    fy = fy_part.strip()
    q = q_part.strip()
    if q == "Q1":
        return f"Q1 '{fy[-2:]}"
    return q


def _period_xaxis(periods: list[str], grain: str, tickfont_size: int | None = None) -> dict:
    """Annual ticks stay FY 2022. Quarter ticks are Q1 '22, Q2, Q3, Q4."""
    axis = dict(
        categoryorder="array",
        categoryarray=periods,
        title=None,
        automargin=True,
        tickangle=0,
    )
    if tickfont_size:
        axis["tickfont"] = dict(size=tickfont_size)
    if grain != "quarter":
        return axis
    ticktext = [_quarter_tick_label(p) for p in periods]
    axis.update(
        tickmode="array",
        tickvals=periods,
        ticktext=ticktext,
        tickangle=-45,
        tickfont=dict(size=tickfont_size or 10),
    )
    return axis


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
    return rolled


def _legend_below(y: float = -0.18) -> dict:
    return dict(
        orientation="h", yanchor="top", y=y, x=0, xanchor="left",
        title=None, font=dict(size=12), bgcolor="rgba(0,0,0,0)",
    )


def _ton_ticks(ymax: float, headroom: float = 1.12) -> dict:
    """Compact 200K / 1M ticks so the y-axis title does not sit on the numbers."""
    top = (float(ymax) * headroom) if pd.notna(ymax) and ymax else 1
    if top >= 1_000_000:
        step = 200_000
    elif top >= 400_000:
        step = 200_000
    elif top >= 100_000:
        step = 50_000
    else:
        step = 20_000
    tickvals = list(range(0, int(top) + step, step))
    if tickvals[-1] < top:
        tickvals.append(int(math.ceil(top / step) * step))
    return dict(
        range=[0, max(top, tickvals[-1])],
        tickvals=tickvals,
        ticktext=[_ton_tick_text(v) for v in tickvals],
        title_standoff=18,
        automargin=True,
    )


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
        horizontal_spacing=0.07,
        vertical_spacing=0.32 if nrows > 1 else 0.12,
    )

    ton_max = pd.to_numeric(df["contracted_tons"], errors="coerce").max()
    ton_axis = _ton_ticks(ton_max)
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
                name=volume_label(vendor_df),
                legendgroup="tons",
                showlegend=i == 0,
                hovertemplate="%{x}<br>%{y:,.0f} tons<extra>Volume</extra>",
            ),
            row=r, col=c, secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=g["period"], y=_y_none(g["price"]),
                mode="lines+markers",
                line=dict(color=PRICE_LINE, width=2),
                marker=dict(size=6, color=PRICE_LINE),
                name="Avg. $/ton",
                legendgroup="price",
                showlegend=i == 0,
                connectgaps=True,
                hovertemplate="%{x}<br>%{y:$,.2f}/ton<extra>Avg. price</extra>",
            ),
            row=r, col=c, secondary_y=True,
        )
        fig.update_xaxes(**_period_xaxis(periods, grain, tickfont_size=9 if grain == "quarter" else 11), row=r, col=c)
        fig.update_yaxes(
            range=ton_axis["range"],
            tickvals=ton_axis["tickvals"],
            ticktext=ton_axis["ticktext"] if show_vol else [""] * len(ton_axis["tickvals"]),
            showticklabels=show_vol,
            ticks="outside" if show_vol else "",
            title_text="",
            title_standoff=8,
            automargin=show_vol,
            row=r, col=c, secondary_y=False,
        )
        fig.update_yaxes(
            range=[0, y2_top],
            showgrid=False,
            tickprefix="$" if show_price else "",
            tickformat=",.0f",
            showticklabels=show_price,
            ticks="outside" if show_price else "",
            title_text="",
            automargin=show_price,
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
    )
    fig = style(fig, height=460 * nrows + 90)
    fig.update_annotations(font=dict(size=14, color=NAVY, family="Georgia, 'Times New Roman', serif"))
    fig.update_layout(
        legend=_legend_below(-0.32 if nrows == 1 else -0.16),
        margin=dict(l=64, r=72, t=88, b=128 if grain == "quarter" else 96),
    )
    return fig


def _latest(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "fiscal_year" not in df.columns:
        return df
    return df[df["fiscal_year"] == int(df["fiscal_year"].max())].copy()


# Approximate state centroids for share labels (not used for the fill).
_STATE_LABEL_LONLAT = {
    "MI": (-85.4, 44.3),
    "PA": (-77.8, 40.9),
}


def _fy_span(df: pd.DataFrame) -> str:
    if df is None or df.empty or "fiscal_year" not in df.columns:
        return ""
    fys = sorted(int(x) for x in df["fiscal_year"].dropna().unique())
    if not fys:
        return ""
    if fys[0] == fys[-1]:
        return f"FY{fys[0]}"
    return f"FY{fys[0]}–FY{fys[-1]}"


def state_volume_map(state_df: pd.DataFrame, state_code: str) -> go.Figure:
    """One-state choropleth of tons in the filtered year range.

    Never a share and never a two-state denominator. Color and label are that
    state's own tons, summed across every fiscal year still in the frame
    (the From/To filter), not pinned to the latest year.
    """
    measure = STATE_VOLUME_MEASURE.get(state_code, "published tons")
    name = STATE_NAMES.get(state_code, state_code)
    g = state_df[state_df["state"] == state_code] if not state_df.empty else state_df
    span = _fy_span(g)
    title = f"{name} — {measure}" + (f" · {span}" if span else "")
    fig = go.Figure()
    tons = float(pd.to_numeric(g["contracted_tons"], errors="coerce").sum()) if not g.empty else 0.0
    if g.empty or tons != tons:
        fig.update_layout(title=title)
        return style(fig, height=420, legend="none")

    fig.add_trace(go.Choropleth(
        locations=[state_code],
        z=[tons],
        locationmode="USA-states",
        colorscale=[[0, "#E8EEF1"], [1, STATE_COLORS.get(state_code, NAVY)]],
        zmin=0,
        zmax=max(tons, 1.0),
        colorbar=dict(
            title=dict(text="Tons", side="right"),
            thickness=12,
            len=0.72,
            x=1.0,
        ),
        marker_line_color=WHITE,
        marker_line_width=1.2,
        customdata=[[name, tons, measure]],
        hovertemplate="%{customdata[0]}<br>%{customdata[1]:,.0f} %{customdata[2]}<extra></extra>",
        name="",
    ))
    lonlat = _STATE_LABEL_LONLAT.get(state_code)
    if lonlat:
        fig.add_trace(go.Scattergeo(
            lon=[lonlat[0]], lat=[lonlat[1]],
            text=[f"{name}<br>{tons:,.0f} t"],
            mode="text",
            textfont=dict(size=12, color=INK, family="Georgia, 'Times New Roman', serif"),
            hoverinfo="skip", showlegend=False,
        ))
    fig.update_geos(
        scope="usa",
        fitbounds="locations",
        visible=False,
        showland=True,
        landcolor=PAPER,
        showlakes=True,
        lakecolor=WHITE,
        bgcolor=WHITE,
        showsubunits=True,
        subunitcolor=LINE,
        subunitwidth=0.4,
        projection_type="albers usa",
    )
    fig.update_layout(title=title, margin=dict(l=16, r=72, t=64, b=24))
    return style(fig, height=420, legend="none")


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
    fig.update_yaxes(title=volume_label(state_df), **_ton_ticks(
        pd.to_numeric(g["contracted_tons"], errors="coerce").max() if not g.empty else 1,
        headroom=1.22,
    ))
    fig.update_layout(
        title=f"{volume_label(g)} · FY{fy}" if fy else volume_label(g),
        margin=dict(l=80, r=24, t=64, b=40),
        showlegend=False,
    )
    return style(fig, height=380, legend="none")


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
    fig.update_layout(
        title=title,
        margin=dict(l=16, r=16, t=64, b=16),
        uniformtext=dict(minsize=11, mode="hide"),
        showlegend=False,
    )
    return style(fig, height=380, legend="none")


def vendor_price_bars(
    vendor_df: pd.DataFrame,
    metric: str = "weighted_avg_price",
    state_code: str | None = None,
) -> go.Figure:
    src = vendor_df if state_code is None else vendor_df[vendor_df["state"] == state_code]
    latest = _latest(src)
    fy = int(latest["fiscal_year"].max()) if not latest.empty else None
    where = STATE_NAMES.get(state_code, "") if state_code else ""
    title = " · ".join(p for p in [f"{where} avg. price per ton".strip(), f"FY{fy}" if fy else ""] if p)
    df = _vendor_totals(latest, metric)
    fig = go.Figure()
    if df.empty or "price" not in df.columns:
        fig.update_layout(title=title or "Avg. Price Per Ton")
        return style(fig, height=380, legend="none")
    df = df[df["price"].notna()].sort_values("price", ascending=True)
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
    xmax = float(pd.to_numeric(df["price"], errors="coerce").max() or 0)
    fig.update_xaxes(title=None, tickprefix="$", range=[0, xmax * 1.18 if xmax else 1], automargin=True)
    fig.update_yaxes(title=None, automargin=True)
    fig.update_layout(title=title, margin=dict(l=16, r=72, t=64, b=40), showlegend=False)
    return style(fig, height=380, legend="none")


def price_timeseries(
    state_df: pd.DataFrame, metric: str, grain: str = "annual",
) -> go.Figure:
    fig = go.Figure()
    framed = _with_period(state_df, grain)
    periods = _periods(framed)
    for code, g in framed.groupby("state"):
        g = g.sort_values("period_sort")
        fig.add_trace(go.Scatter(
            x=g["period"], y=_y_none(g[metric]), name=STATE_NAMES.get(code, code),
            mode="lines+markers",
            line=dict(color=STATE_COLORS.get(code, "#1B3A4B"), width=2.5),
            marker=dict(size=8),
            connectgaps=True,
            hovertemplate="%{x}<br>%{y:$,.2f}/ton<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="USD per short ton", tickprefix="$", tickformat=",.0f", title_standoff=18, automargin=True)
    fig.update_xaxes(**_period_xaxis(periods, grain))
    fig.update_layout(
        title="Contracted price over time",
    )
    fig = style(fig, height=480)
    fig.update_layout(
        legend=_legend_below(-0.22 if grain == "quarter" else -0.20),
        margin=dict(l=88, r=24, t=64, b=112 if grain == "quarter" else 88),
    )
    return fig


def volume_timeseries(state_df: pd.DataFrame, grain: str = "annual") -> go.Figure:
    fig = go.Figure()
    framed = _with_period(state_df, grain)
    periods = _periods(framed)
    for code, g in framed.groupby("state"):
        g = g.sort_values("period_sort")
        fig.add_trace(go.Bar(
            x=g["period"], y=_y_none(g["contracted_tons"]), name=STATE_NAMES.get(code, code),
            marker_color=STATE_COLORS.get(code, "#1B3A4B"),
            hovertemplate="%{x}<br>%{y:,.0f} tons<extra>%{fullData.name}</extra>",
        ))
    tons = pd.to_numeric(state_df["contracted_tons"], errors="coerce")
    fig.update_yaxes(title=volume_label(state_df), **_ton_ticks(tons.max() if len(tons) else 1))
    fig.update_xaxes(**_period_xaxis(periods, grain))
    fig.update_layout(
        barmode="group",
        title=f"{volume_label(state_df)} over time",
    )
    fig = style(fig, height=480)
    fig.update_layout(
        legend=_legend_below(-0.22 if grain == "quarter" else -0.20),
        margin=dict(l=88, r=24, t=64, b=112 if grain == "quarter" else 88),
    )
    return fig


def vendor_price(
    vendor_df: pd.DataFrame, metric: str, state_code: str, grain: str = "annual",
) -> go.Figure:
    fig = go.Figure()
    g = vendor_df[(vendor_df["state"] == state_code) & vendor_df["is_attributed"] & vendor_df[metric].notna()]
    g = _with_period(g, grain)
    periods = _periods(g)
    for vendor, vg in g.groupby("vendor"):
        vg = vg.sort_values("period_sort")
        fig.add_trace(go.Scatter(
            x=vg["period"], y=_y_none(vg[metric]), name=_short(vendor), mode="lines+markers",
            line=dict(color=VENDOR_COLORS.get(vendor, "#1B3A4B"), width=2.2),
            marker=dict(size=7),
            connectgaps=True,
            hovertemplate="%{x}<br>%{y:$,.2f}/ton<extra>%{fullData.name}</extra>",
        ))
    fig.update_yaxes(title="USD per short ton", tickprefix="$", title_standoff=18, automargin=True)
    fig.update_xaxes(**_period_xaxis(periods, grain))
    fig.update_layout(
        title=f"{STATE_NAMES.get(state_code, state_code)} — price by supplier",
    )
    fig = style(fig, height=500)
    fig.update_layout(
        legend=_legend_below(-0.24 if grain == "quarter" else -0.22),
        margin=dict(l=88, r=24, t=64, b=120 if grain == "quarter" else 96),
    )
    return fig


def vendor_volume(vendor_df: pd.DataFrame, state_code: str, grain: str = "annual") -> go.Figure:
    g = _named(vendor_df[vendor_df["state"] == state_code])
    g = _with_period(g, grain)
    periods = _periods(g)
    fig = go.Figure()
    for vendor, vg in g.groupby("vendor"):
        vg = vg.sort_values("period_sort")
        fig.add_trace(go.Bar(
            x=vg["period"], y=_y_none(vg["contracted_tons"]), name=_short(vendor),
            marker_color=VENDOR_COLORS.get(vendor, "#9AA3B2"),
            hovertemplate="%{x}<br>%{y:,.0f} tons<extra>%{fullData.name}</extra>",
        ))
    tons = pd.to_numeric(g["contracted_tons"], errors="coerce") if not g.empty else pd.Series(dtype=float)
    fig.update_yaxes(title=volume_label(g), **_ton_ticks(tons.max() if len(tons) else 1))
    fig.update_xaxes(**_period_xaxis(periods, grain))
    fig.update_layout(
        barmode="stack",
        title=f"{STATE_NAMES.get(state_code, state_code)} — volume by supplier",
    )
    fig = style(fig, height=500)
    fig.update_layout(
        legend=_legend_below(-0.24 if grain == "quarter" else -0.22),
        margin=dict(l=88, r=24, t=64, b=120 if grain == "quarter" else 96),
    )
    return fig


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
    fig.update_yaxes(
        title="Share", tickformat=".0%", range=[0, 1],
        title_standoff=18, automargin=True,
    )
    fig.update_xaxes(title=None, automargin=True)
    fig.update_layout(
        title=f"{STATE_NAMES.get(state_code, state_code)} — supplier share",
        hovermode="closest",
    )
    fig = style(fig, height=480)
    fig.update_layout(
        legend=_legend_below(-0.24),
        margin=dict(l=72, r=24, t=72, b=112),
    )
    return fig
