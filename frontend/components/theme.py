"""One visual language for every page: colour tokens and the Plotly template.

Charts must be drawn with ``theme=None`` in ``st.plotly_chart`` (use
``charts.plot``), otherwise Streamlit's own chart theme replaces this one.
"""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

# Neutrals and brand colours shared with the dashboard surfaces.
INK, MUTED, GRID, SURFACE = "#172B4D", "#64748B", "#E8EEF5", "#F3F6FB"
PRIMARY, PRIMARY_LIGHT = "#2563EB", "#93C5FD"
POSITIVE, NEGATIVE, WARNING, NEUTRAL = "#059669", "#DC2626", "#D97706", "#94A3B8"
FONT = "Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

# Fixed colour per category, so "medical" is the same green on every chart.
EVENT_TYPE_COLORS = {"tech": "#2563EB", "business": "#D97706", "medical": "#059669", "education": "#7C3AED"}
FORMAT_COLORS = {"in-person": "#2563EB", "hybrid": "#0EA5E9", "virtual": "#14B8A6"}
SOURCE_COLORS = {"booth": "#2563EB", "demo": "#D97706", "virtual": "#14B8A6"}
TIER_COLORS = {"platinum": "#334155", "gold": "#CA8A04", "silver": "#94A3B8", "bronze": "#B45309"}
BAND_COLORS = {"Below 80%": NEGATIVE, "80-99%": WARNING, "Met target": POSITIVE, "No target": NEUTRAL}
COLORWAY = [PRIMARY, "#D97706", "#059669", "#7C3AED", "#0EA5E9", "#DB2777", "#64748B"]

TEMPLATE = "conferatlas"


def register_template() -> None:
    axis = dict(gridcolor=GRID, linecolor=GRID, zeroline=False, tickfont=dict(color=MUTED),
                title=dict(font=dict(color=MUTED, size=12)), automargin=True)
    pio.templates[TEMPLATE] = go.layout.Template(layout=dict(
        font=dict(family=FONT, color=INK, size=13),
        title=dict(font=dict(size=15, color=INK), x=0, xanchor="left"),
        colorway=COLORWAY,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=axis,
        yaxis=axis,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None, font=dict(color=MUTED)),
        hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(family=FONT, color=INK)),
        margin=dict(l=10, r=10, t=56, b=10),
        bargap=0.25,
    ))
    pio.templates.default = f"plotly_white+{TEMPLATE}"


register_template()
