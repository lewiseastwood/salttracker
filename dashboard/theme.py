"""Shared visual language for the executive dashboard."""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

NAVY = "#1B3A4B"
INK = "#1A2332"
MUTED = "#5C6770"
LINE = "#D7DCE0"
PAPER = "#F4F5F7"
WHITE = "#FFFFFF"

VENDOR_COLORS = {
    "American Rock Salt": "#4E79A7",
    "Cargill": "#EDC948",
    "Compass Minerals": "#76B7B2",
    "Morton Salt": "#E15759",
    "Detroit Salt": "#59A14F",
    "Eastern Salt": "#F28E2B",
    "Riverside Construction Materials": "#B07AA1",
    "Unattributed": "#9AA3B2",
}
PRICE_LINE = "#6B4C9A"
STATE_COLORS = {"MI": "#1B3A4B", "PA": "#C45C26"}
STATE_NAMES = {"MI": "Michigan", "PA": "Pennsylvania"}

TEMPLATE = go.layout.Template(
    layout=go.Layout(
        font=dict(family="Georgia, 'Times New Roman', serif", size=13, color=INK),
        paper_bgcolor=WHITE,
        plot_bgcolor=WHITE,
        colorway=list(VENDOR_COLORS.values()),
        margin=dict(l=56, r=24, t=56, b=48),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=WHITE, font_size=12, font_family="Georgia, serif"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None,
                    font=dict(size=12)),
        xaxis=dict(showgrid=False, linecolor=LINE, ticks="outside", tickcolor=LINE),
        yaxis=dict(gridcolor=LINE, zeroline=False, linecolor=LINE),
        title=dict(font=dict(size=16, color=NAVY), x=0, xanchor="left"),
    )
)
pio.templates["salttracker"] = TEMPLATE


def style(fig: go.Figure, height: int = 420) -> go.Figure:
    fig.update_layout(template="salttracker", height=height)
    return fig
