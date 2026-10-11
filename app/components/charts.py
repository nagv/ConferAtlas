"""Plotly figure builders. Presentation only: inputs are pre-computed frames.

Colours come from ``app.components.theme``. Draw figures with ``plot`` so
Streamlit keeps the ConferAtlas template instead of applying its own.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.components.theme import (
    BAND_COLORS,
    COLORWAY,
    EVENT_TYPE_COLORS,
    FORMAT_COLORS,
    GRID,
    INK,
    MUTED,
    NEGATIVE,
    NEUTRAL,
    POSITIVE,
    PRIMARY,
    PRIMARY_LIGHT,
    SOURCE_COLORS,
    TIER_COLORS,
    WARNING,
)


# Keep only "save as image" and full screen in the chart toolbar: zoom/pan/lasso confuse most
# viewers and get in the way of scrolling on touch screens.
CHART_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["zoom2d", "pan2d", "select2d", "lasso2d", "zoomIn2d", "zoomOut2d",
                               "autoScale2d", "resetScale2d", "hoverClosestCartesian", "hoverCompareCartesian"],
    "toImageButtonOptions": {"format": "png", "scale": 2},
}


def plot(container, fig: go.Figure, key: str | None = None) -> None:
    """Render ``fig`` full width in ``container`` (``st`` or a column) with our template.
    Pass ``key`` when the same figure can appear twice on a page (e.g. repeated chat answers)."""
    container.plotly_chart(fig, width="stretch", theme=None, key=key, config=CHART_CONFIG)


LABEL_MAX = 34  # characters shown on an axis before a name is shortened


def short_labels(names: pd.Series, max_len: int = LABEL_MAX) -> pd.Series:
    """Shorten long names for axes, keeping a trailing year ("Boston Clinical Innovat... 2024").
    Falls back to the full names if shortening would make two of them identical."""
    def shorten(name: str) -> str:
        name = str(name)
        if len(name) <= max_len:
            return name
        head, _, tail = name.rpartition(" ")
        year = tail if tail.isdigit() and len(tail) == 4 else ""
        keep = max_len - (len(year) + 2 if year else 1)
        cut = name[:keep]
        if name[keep:keep + 1] not in ("", " ") and " " in cut:  # don't stop mid-word
            cut = cut.rsplit(" ", 1)[0]
        return cut.rstrip(" &,-") + "…" + (f" {year}" if year else "")
    names = names.astype(str)
    short = names.map(shorten)
    return short if short.nunique() == names.nunique() else names


def _style(fig: go.Figure, title: str, height: int | None = None) -> go.Figure:
    fig.update_layout(title=title, height=height)
    return fig


def _bar_height(rows: int) -> int:
    return max(320, 28 * rows + 120)


def registrations_vs_attendance(events: pd.DataFrame) -> go.Figure:
    """Grouped horizontal bars of registrations and attendance per event, in the given order."""
    labels = short_labels(events["name"])
    fig = go.Figure([
        go.Bar(y=labels, x=events["registrations"], name="Registrations", orientation="h",
               marker_color=PRIMARY_LIGHT, customdata=events[["name"]],
               hovertemplate="%{customdata[0]}<br>%{x:,} registrations<extra></extra>"),
        go.Bar(y=labels, x=events["attended"], name="Attended", orientation="h",
               marker_color=PRIMARY, customdata=events[["name", "show_up_rate"]],
               hovertemplate="%{customdata[0]}<br>%{x:,} attended (%{customdata[1]:.0%} of confirmed)<extra></extra>"),
    ])
    fig.update_layout(barmode="group", yaxis=dict(autorange="reversed"), xaxis_title="People")
    return _style(fig, "Registrations vs attendance", _bar_height(len(events)))


def registration_trend(trend: pd.DataFrame) -> go.Figure:
    """Registrations per month as a line with a light fill."""
    fig = go.Figure(go.Scatter(
        x=trend["period"], y=trend["registrations"], mode="lines+markers", name="Registrations",
        line=dict(color=PRIMARY, width=2.5), marker=dict(size=6),  # straight segments: splines overshoot
        fill="tozeroy", fillcolor="rgba(37, 99, 235, 0.08)",
        hovertemplate="%{x|%b %Y}: %{y:,} registrations<extra></extra>",
    ))
    fig.update_layout(xaxis=dict(title=None, tickformat="%b %Y"), yaxis=dict(title="Registrations per month",
                      rangemode="tozero"), showlegend=False)
    return _style(fig, "Registration trend", 340)


def event_type_mix(mix: pd.DataFrame, measure: str = "events", label: str = "Events") -> go.Figure:
    """Donut of one measure by event type, with the total in the centre."""
    names = mix["event_type"].str.title()
    fig = px.pie(names=names, values=mix[measure], hole=0.62, color=mix["event_type"].str.title(),
                 color_discrete_map={k.title(): v for k, v in EVENT_TYPE_COLORS.items()})
    fig.update_traces(textinfo="percent", sort=False, marker=dict(line=dict(color="white", width=2)),
                      hovertemplate="%{label}: %{value:,} (%{percent})<extra></extra>")
    fig.update_layout(legend=dict(orientation="v", x=1.0, y=0.5, yanchor="middle"),
                      annotations=[dict(text=f"<b>{int(mix[measure].sum()):,}</b><br>"
                                         f"<span style='color:{MUTED}'>{label.lower()}</span>",
                                    showarrow=False, font=dict(size=16))])
    return _style(fig, "Event type mix", 340)


def leads_vs_target(df: pd.DataFrame, label_col: str, title: str) -> go.Figure:
    """Grouped horizontal bars of lead target vs delivered, one pair per row of ``df``."""
    labels = short_labels(df[label_col])
    hover = "%{customdata[0]}<br>%{x:,} %{fullData.name}<extra></extra>"
    fig = go.Figure([
        go.Bar(y=labels, x=df["leads_target"], name="Target", orientation="h", marker_color=NEUTRAL,
               customdata=df[[label_col]], hovertemplate=hover.replace("%{fullData.name}", "target")),
        go.Bar(y=labels, x=df["leads"], name="Delivered", orientation="h", marker_color=PRIMARY,
               customdata=df[[label_col]], hovertemplate=hover.replace("%{fullData.name}", "leads delivered")),
    ])
    fig.update_layout(barmode="group", yaxis=dict(autorange="reversed"), xaxis_title="Leads")
    return _style(fig, title, _bar_height(len(df)))


def sponsor_attainment(perf: pd.DataFrame) -> go.Figure:
    """Each sponsor's leads as a % of target; green met, red missed, line at 100%."""
    rows = perf.sort_values("attainment")
    fig = go.Figure(go.Bar(
        y=rows["sponsor"], x=rows["attainment"], orientation="h",
        marker_color=[POSITIVE if met else NEGATIVE for met in rows["met_target"]],
        customdata=rows[["leads", "leads_target"]],
        hovertemplate="%{x:.0%} of target<br>%{customdata[0]:,} / %{customdata[1]:,} leads<extra></extra>",
    ))
    fig.add_vline(x=1, line_dash="dash", line_color=MUTED, annotation_text="Target")
    fig.update_xaxes(tickformat=".0%", title="Leads delivered as % of target")
    return _style(fig, "Sponsor lead targets: met vs missed", _bar_height(len(rows)))


def registration_curve(curve: pd.DataFrame, early_bird_days: int | None = None) -> go.Figure:
    """Daily registrations (bars) and running total (line) counting down to the event."""
    fig = go.Figure([
        go.Bar(x=curve["days_before"], y=curve["registrations"], name="Per day", marker_color=PRIMARY),
        go.Scatter(x=curve["days_before"], y=curve["cumulative"], name="Running total", yaxis="y2",
                   line=dict(color=WARNING)),
    ])
    if early_bird_days is not None:
        fig.add_vline(x=early_bird_days, line_dash="dash", line_color=MUTED,
                      annotation_text="Early-bird deadline")
    fig.update_layout(
        xaxis=dict(title="Days before the event", autorange="reversed"),
        yaxis=dict(title="Registrations per day", rangemode="tozero"),
        yaxis2=dict(title="Running total", overlaying="y", side="right", showgrid=False,
                    rangemode="tozero"),
        hovermode="x unified",
    )
    _style(fig, "Registrations over time")
    fig.update_layout(legend=dict(yanchor="top", y=0.98, x=0.02))  # inside the plot, clear of the title
    return fig


def lead_sources(mix: pd.Series, title: str = "Where leads came from") -> go.Figure:
    """Donut of leads by source; empty sources are dropped."""
    mix = mix[mix > 0]
    fig = px.pie(names=mix.index, values=mix.to_numpy(), hole=0.55, color=mix.index,
                 color_discrete_map=SOURCE_COLORS)
    fig.update_traces(textinfo="label+percent", sort=False, marker=dict(line=dict(color="white", width=2)))
    fig.update_layout(showlegend=False)
    return _style(fig, title)


def session_fill_rates(sessions: pd.DataFrame) -> go.Figure:
    """Each session's attendance as a share of capacity, coloured by mode, with a 100% line."""
    rows = sessions.sort_values("fill_rate")
    labels = rows["track"] + " · " + rows["speaker"]
    fig = go.Figure()
    for mode in ("in-person", "hybrid", "virtual"):
        part = rows["mode"] == mode
        if part.any():
            fig.add_bar(y=labels[part], x=rows.loc[part, "fill_rate"], name=mode.title(), orientation="h",
                        marker_color=FORMAT_COLORS[mode],
                        customdata=rows.loc[part, ["attendance", "capacity"]],
                        hovertemplate="%{y}<br>%{customdata[0]:,} of %{customdata[1]:,} seats "
                                      "(%{x:.0%})<extra></extra>")
    fig.add_vline(x=1, line_dash="dash", line_color=MUTED, annotation_text="Full")
    fig.update_layout(barmode="overlay", yaxis=dict(categoryorder="array", categoryarray=labels.tolist()),
                      xaxis=dict(tickformat=".0%", title="Attendance as % of capacity"))
    return _style(fig, "Session attendance vs capacity", _bar_height(len(rows)))


ENGAGEMENT_METRICS = {  # column -> (title, number format)
    "avg_watch_minutes": ("Minutes in session", ".1f"),
    "polls_per_attendee": ("Polls answered", ".2f"),
    "questions_per_attendee": ("Questions asked", ".2f"),
    "booth_visits_per_attendee": ("Booth visits", ".2f"),
}


def engagement_comparison(engagement: pd.DataFrame) -> go.Figure:
    """Small multiples: each engagement metric per attendee, one bar per session mode."""
    fig = make_subplots(rows=1, cols=len(ENGAGEMENT_METRICS),
                        subplot_titles=[title for title, _ in ENGAGEMENT_METRICS.values()])
    for col, (metric, (title, fmt)) in enumerate(ENGAGEMENT_METRICS.items(), start=1):
        fig.add_bar(x=engagement["mode"].str.title(), y=engagement[metric], showlegend=False,
                    marker_color=[FORMAT_COLORS.get(m, MUTED) for m in engagement["mode"]],
                    text=[format(v, fmt) for v in engagement[metric]], textposition="outside",
                    hovertemplate=f"%{{x}}: %{{y:{fmt}}} {title.lower()} per attendee<extra></extra>",
                    row=1, col=col)
    fig.update_yaxes(showticklabels=False, showgrid=False, rangemode="tozero")
    fig.update_annotations(font=dict(size=12, color=MUTED))
    fig.update_layout(margin=dict(t=90))
    return _style(fig, "Engagement per attendee by session mode", 330)


def breakdown_bars(df: pd.DataFrame, label_col: str, title: str, order: list[str] | None = None) -> go.Figure:
    """Horizontal bars of registrations per category, with show-up rate on hover."""
    rows = df.copy()
    if order:
        rows[label_col] = pd.Categorical(rows[label_col], categories=[o for o in order if o in set(rows[label_col])],
                                         ordered=True)
        rows = rows.sort_values(label_col)
    fig = go.Figure(go.Bar(
        y=rows[label_col].astype(str), x=rows["registrations"], orientation="h", marker_color=PRIMARY,
        customdata=rows[["share", "show_up_rate"]],
        hovertemplate="%{y}: %{x:,} registrations (%{customdata[0]:.0%})<br>"
                      "Show-up rate %{customdata[1]:.0%}<extra></extra>",
    ))
    fig.update_layout(yaxis=dict(autorange="reversed"), xaxis_title="Registrations")
    return _style(fig, title, _bar_height(len(rows)) if len(rows) > 6 else 320)


def industry_treemap(df: pd.DataFrame) -> go.Figure:
    """Treemap of registrations by industry."""
    fig = px.treemap(df, path=["industry"], values="registrations", color="industry",
                     color_discrete_sequence=COLORWAY)
    fig.update_traces(texttemplate="%{label}<br>%{percentRoot:.0%}",
                      hovertemplate="%{label}: %{value:,} registrations<extra></extra>",
                      marker=dict(line=dict(color="white", width=2)))
    fig.update_layout(margin=dict(l=0, r=0, t=56, b=0))
    return _style(fig, "Industry mix", 360)


# Country name (as written in registrations.csv) -> ISO 3166 alpha-3, for the map.
COUNTRY_ISO3 = {
    "Argentina": "ARG", "Australia": "AUS", "Austria": "AUT", "Belgium": "BEL", "Brazil": "BRA",
    "Canada": "CAN", "Chile": "CHL", "China": "CHN", "Colombia": "COL", "Czech Republic": "CZE",
    "Denmark": "DNK", "Egypt": "EGY", "Finland": "FIN", "France": "FRA", "Germany": "DEU", "Greece": "GRC",
    "Hong Kong": "HKG", "India": "IND", "Indonesia": "IDN", "Ireland": "IRL", "Israel": "ISR", "Italy": "ITA",
    "Japan": "JPN", "Kenya": "KEN", "Malaysia": "MYS", "Mexico": "MEX", "Netherlands": "NLD",
    "New Zealand": "NZL", "Nigeria": "NGA", "Norway": "NOR", "Pakistan": "PAK", "Peru": "PER",
    "Philippines": "PHL", "Poland": "POL", "Portugal": "PRT", "Saudi Arabia": "SAU", "Singapore": "SGP",
    "South Africa": "ZAF", "South Korea": "KOR", "Spain": "ESP", "Sweden": "SWE", "Switzerland": "CHE",
    "Taiwan": "TWN", "Thailand": "THA", "Turkey": "TUR", "United Arab Emirates": "ARE",
    "United Kingdom": "GBR", "UK": "GBR", "United States": "USA", "USA": "USA", "US": "USA", "Vietnam": "VNM",
}


def country_map(df: pd.DataFrame) -> tuple[go.Figure, list[str]]:
    """World choropleth of attendees by country, plus the names that could not be placed on the map."""
    rows = df.assign(iso3=df["country"].map(COUNTRY_ISO3))
    unmapped = sorted(rows.loc[rows["iso3"].isna(), "country"].tolist())
    rows = rows.dropna(subset=["iso3"])
    fig = px.choropleth(rows, locations="iso3", locationmode="ISO-3", color="attended",
                        color_continuous_scale=["#DBEAFE", PRIMARY, "#1E3A8A"], hover_name="country",
                        hover_data={"iso3": False, "attended": ":,", "registrations": ":,"},
                        labels={"attended": "Attendees", "registrations": "Registrations"})
    fig.update_geos(showframe=False, showcoastlines=False, showcountries=True, countrycolor="white",
                    landcolor="#F1F5F9", projection_type="natural earth", bgcolor="rgba(0,0,0,0)")
    fig.update_layout(margin=dict(l=0, r=0, t=56, b=0), coloraxis_colorbar=dict(title="Attendees", thickness=12))
    return _style(fig, "Attendees by country", 420), unmapped


def new_vs_returning_donut(split: pd.DataFrame) -> go.Figure:
    """Donut of new vs returning attendees."""
    fig = px.pie(split, names="segment", values="attendees", hole=0.62, color="segment",
                 color_discrete_map={"New": PRIMARY_LIGHT, "Returning": PRIMARY})
    fig.update_traces(textinfo="percent", sort=False, marker=dict(line=dict(color="white", width=2)),
                      hovertemplate="%{label}: %{value:,} attendees (%{percent})<extra></extra>")
    fig.update_layout(legend=dict(orientation="v", x=1.0, y=0.5, yanchor="middle"))
    return _style(fig, "New vs returning attendees", 340)


def returning_by_event_bars(df: pd.DataFrame) -> go.Figure:
    """Stacked bars of new and returning attendees per event, oldest first."""
    labels = short_labels(df["name"], 24)
    fig = go.Figure([
        go.Bar(x=labels, y=df["new"], name="New", marker_color=PRIMARY_LIGHT, customdata=df[["name"]],
               hovertemplate="%{customdata[0]}<br>%{y:,} new<extra></extra>"),
        go.Bar(x=labels, y=df["returning"], name="Returning", marker_color=PRIMARY,
               customdata=df[["name", "returning_share"]],
               hovertemplate="%{customdata[0]}<br>%{y:,} returning (%{customdata[1]:.0%})<extra></extra>"),
    ])
    fig.update_layout(barmode="stack", yaxis_title="Attendees", xaxis=dict(tickangle=-30, title=None))
    return _style(fig, "Returning attendees by event", 380)


def sponsor_leads_vs_target(cards: pd.DataFrame) -> go.Figure:
    """Target (grey) vs delivered leads per sponsor; delivered bars coloured by attainment band,
    with a tick at 80% of each target. Lowest attainment at the top."""
    rows = cards.sort_values("attainment", na_position="last")
    hover = "%{y}<br>%{x:,} leads of %{customdata[0]:,} target (%{customdata[1]:.0%})<extra></extra>"
    fig = go.Figure(go.Bar(y=rows["sponsor"], x=rows["leads_target"], name="Target", orientation="h",
                           marker_color=GRID, hovertemplate="%{y}<br>Target %{x:,}<extra></extra>"))
    for band, color in BAND_COLORS.items():
        part = rows["band"] == band
        if part.any():
            fig.add_bar(y=rows.loc[part, "sponsor"], x=rows.loc[part, "leads"], name=band, orientation="h",
                        marker_color=color, width=0.45, customdata=rows.loc[part, ["leads_target", "attainment"]],
                        hovertemplate=hover)
    fig.add_scatter(y=rows["sponsor"], x=rows["leads_target"] * 0.8, mode="markers", name="80% of target",
                    marker=dict(symbol="line-ns", size=14, line=dict(width=2, color=INK)),
                    hovertemplate="80% of target: %{x:,.0f}<extra></extra>")
    fig.update_layout(barmode="overlay", xaxis_title="Leads",
                      yaxis=dict(categoryorder="array", categoryarray=rows["sponsor"].tolist()[::-1]))
    _style(fig, "Leads vs target by sponsor", _bar_height(len(rows)) + 30)
    # Title on its own line, legend on the next, so they never overlap.
    fig.update_layout(margin=dict(t=95), title=dict(y=0.99, yanchor="top", yref="container"),
                      legend=dict(y=1.0, yanchor="bottom"))
    return fig


def revenue_by_tier_chart(tiers: pd.DataFrame) -> go.Figure:
    """Sponsor revenue per tier, with sponsors, leads and cost per lead on hover."""
    fig = go.Figure(go.Bar(
        x=tiers["tier"].str.title(), y=tiers["spend"],
        marker_color=[TIER_COLORS.get(t, NEUTRAL) for t in tiers["tier"]],
        text=[f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.0f}K" for v in tiers["spend"]],
        textposition="outside", customdata=tiers[["sponsors", "leads", "cost_per_lead"]],
        hovertemplate="%{x}: $%{y:,.0f}<br>%{customdata[0]} sponsors, %{customdata[1]:,} leads<br>"
                      "$%{customdata[2]:,.0f} per lead<extra></extra>",
    ))
    fig.update_layout(yaxis=dict(title="Sponsor revenue", tickprefix="$", tickformat="~s", rangemode="tozero"),
                      xaxis_title=None, showlegend=False)
    return _style(fig, "Revenue by sponsor tier", 360)


SENTIMENT_COLORS = {"positive": POSITIVE, "neutral": NEUTRAL, "negative": NEGATIVE}


def rating_distribution_chart(dist: pd.DataFrame) -> go.Figure:
    """Responses per rating 1-5: red for 1-2, grey for 3, green for 4-5."""
    colors = [NEGATIVE if r <= 2 else NEUTRAL if r == 3 else POSITIVE for r in dist["rating"]]
    fig = go.Figure(go.Bar(
        x=dist["rating"].astype(str), y=dist["responses"], marker_color=colors,
        text=[f"{s:.0%}" if s == s else "" for s in dist["share"]], textposition="outside",
        hovertemplate="Rating %{x}: %{y:,} responses<extra></extra>",
    ))
    fig.update_layout(xaxis_title="Rating", yaxis=dict(title="Responses", rangemode="tozero"), showlegend=False)
    return _style(fig, "Rating distribution", 340)


def sentiment_by_event_chart(df: pd.DataFrame) -> go.Figure:
    """100% stacked bars of positive / neutral / negative feedback per event, oldest at top."""
    fig = go.Figure()
    for sentiment, color in SENTIMENT_COLORS.items():
        fig.add_bar(y=short_labels(df["name"]), x=df[f"{sentiment}_share"], name=sentiment.title(), orientation="h",
                    marker_color=color, customdata=df[["name", sentiment]],
                    hovertemplate="%{customdata[0]}<br>%{x:.0%} " + sentiment + " (%{customdata[1]:,})<extra></extra>")
    fig.update_layout(barmode="stack", yaxis=dict(autorange="reversed"),
                      xaxis=dict(tickformat=".0%", title="Share of ratings", range=[0, 1]))
    return _style(fig, "Sentiment by event", _bar_height(len(df)))


def keyword_themes_chart(themes: pd.DataFrame) -> go.Figure:
    """Mentions per keyword theme, coloured by how positive those comments are."""
    colors = [POSITIVE if s >= 0.6 else NEGATIVE if s < 0.4 else WARNING for s in themes["positive_share"]]
    fig = go.Figure(go.Bar(
        y=themes["theme"], x=themes["mentions"], orientation="h", marker_color=colors,
        customdata=themes[["positive_share", "example"]],
        hovertemplate="%{y}: %{x:,} mentions, %{customdata[0]:.0%} positive<br>"
                      "e.g. \"%{customdata[1]}\"<extra></extra>",
    ))
    fig.update_layout(yaxis=dict(autorange="reversed"), xaxis_title="Comments mentioning the theme")
    return _style(fig, "Top themes (keyword match)", _bar_height(len(themes)))


def chart_from_spec(spec, table: pd.DataFrame) -> go.Figure:
    """Draw an agent-requested chart. ``table`` is the tool result it names, never raw data."""
    labels = (table[spec.x].astype(str).str.title() if spec.x in ("event_type", "tier", "format")
              else short_labels(table[spec.x]) if table[spec.x].dtype == object else table[spec.x])
    if spec.chart_type == "pie":
        fig = px.pie(table, names=labels, values=spec.y, hole=0.55, color_discrete_sequence=COLORWAY)
        fig.update_traces(textinfo="percent", marker=dict(line=dict(color="white", width=2)))
        return _style(fig, spec.title, 360)
    if spec.chart_type == "line":
        fig = go.Figure(go.Scatter(x=labels, y=table[spec.y], mode="lines+markers", line=dict(color=PRIMARY, width=2.5)))
        fig.update_layout(xaxis_title=spec.x.replace("_", " "), yaxis_title=spec.y.replace("_", " "))
        return _style(fig, spec.title, 340)
    fig = go.Figure(go.Bar(y=labels.astype(str), x=table[spec.y], orientation="h", marker_color=PRIMARY,
                           hovertemplate="%{y}: %{x:,}<extra></extra>"))
    fig.update_layout(yaxis=dict(autorange="reversed"), xaxis_title=spec.y.replace("_", " "))
    return _style(fig, spec.title, _bar_height(len(table)) if len(table) > 6 else 320)
