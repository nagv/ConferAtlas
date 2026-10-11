"""Shared page furniture: header with description and filter chips, empty states, global styling."""
from __future__ import annotations

from datetime import date

import streamlit as st

from app.components.data_source import applied_filters, get_data, using_uploaded_data
from core.filters import describe

# Shared surface styling; keep Streamlit's native controls, fonts, and page structure.
_GLOBAL_CSS = """
<style>
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(ellipse at 88% 0%, rgba(14, 165, 164, 0.065), transparent 42%),
    radial-gradient(ellipse at 30% 0%, rgba(37, 99, 235, 0.07), transparent 46%),
    #f3f6fb;
}
[data-testid="stHeader"] { background: rgba(243, 246, 251, 0.94); }
[data-testid="stSidebar"] {
  background: linear-gradient(175deg, #f8faff 0%, #edf2f9 100%);
  border-right: 1px solid #dce5f0;
}
[data-testid="stSidebarNav"] { border-bottom: 1px solid #dce5f0; }
[data-testid="stSidebarNavLink"] {
  border-radius: 9px;
  transition: background-color 160ms ease;
}
[data-testid="stSidebarNavLink"][aria-current="page"] {
  background: #e2ebfc;
  box-shadow: inset 3px 0 0 #2563eb;
}
[data-testid="stMainBlockContainer"] { padding-bottom: 3rem; }
[data-testid="stHeadingWithActionElements"] h2 { color: #172b4d; }
[data-testid="stMetric"], [data-testid="stPlotlyChart"],
[data-testid="stDataFrame"], [data-testid="stTable"] {
  background: #ffffff;
  border: 1px solid #dfe7f1;
  border-radius: 14px;
  box-shadow: 0 3px 14px rgba(23, 43, 77, 0.045);
}
[data-testid="stMetric"] {
  position: relative;
  overflow: hidden;
  padding: 1rem 1.1rem;
  background: linear-gradient(135deg, #ffffff 55%, #f5f8ff);
}
[data-testid="stMetric"]::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 3px;
  background: linear-gradient(90deg, #2563eb, #38a3b8);
}
[data-testid="stMetricValue"] { font-size: clamp(1.35rem, 2.6vw, 2rem); }
[data-testid="stMetricValue"] > div { color: #172b4d; }
[data-testid="stMetricLabel"] p { white-space: normal; }
[data-testid="stPlotlyChart"] { padding: 0.65rem; }
[data-testid="stDataFrame"], [data-testid="stTable"] { overflow: hidden; }
[data-testid="stExpander"] {
  background: rgba(255, 255, 255, 0.8);
  border-radius: 12px;
}
[data-testid="stExpander"] details { border-color: #dfe7f1; border-radius: 12px; }
[data-testid="stExpander"] summary:hover { color: #1d4ed8; }
[data-testid="stTabs"] [role="tablist"] { border-bottom: 1px solid #dce5f0; }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: #1d4ed8; }
[data-testid="stButton"] button, [data-testid="stDownloadButton"] button {
  border-radius: 9px;
  transition: box-shadow 160ms ease, border-color 160ms ease;
}
[data-testid="stButton"] button:hover, [data-testid="stDownloadButton"] button:hover {
  box-shadow: 0 3px 10px rgba(37, 99, 235, 0.12);
}
[data-testid="stButton"] button[kind="primary"] {
  background: linear-gradient(110deg, #2563eb, #1d4ed8);
  border-color: #2563eb;
  box-shadow: 0 3px 9px rgba(37, 99, 235, 0.16);
}
hr { border-color: #dce5f0; }
@media (prefers-reduced-motion: reduce) {
  [data-testid="stSidebarNavLink"], [data-testid="stButton"] button,
  [data-testid="stDownloadButton"] button { transition: none; }
}
@media (max-width: 640px) {
  .block-container, [data-testid="stMainBlockContainer"] { padding: 2.5rem 0.9rem 3rem; }
  h1, h2 { font-size: 1.45rem !important; }
  h3 { font-size: 1.15rem !important; }
  [data-testid="stMetricValue"] { font-size: 1.5rem; }
  [data-testid="stPlotlyChart"] { padding: 0.25rem; }
}
</style>
"""


def apply_global_style() -> None:
    st.html(_GLOBAL_CSS)


def filter_chips() -> str:
    """Markdown badges for the global filters that are switched on (or one 'All data' badge)."""
    full = get_data(show_errors=False)
    if full is None:
        return ""
    active = [(label, value) for label, value in describe(applied_filters(), full, date.today())
              if value not in ("All", "All time")]
    chips = [f":blue-badge[:material/filter_alt: {label}: {value}]" for label, value in active] or \
            [":gray-badge[:material/filter_alt_off: All events · all time]"]
    source = (":orange-badge[:material/upload_file: Your uploaded data]" if using_uploaded_data()
              else ":violet-badge[:material/science: Synthetic demo data]")
    return " ".join(chips + [source])


def page_header(title: str, description: str, icon: str | None = None) -> None:
    """Every page starts the same way: title, one-line description, then what data you are looking at."""
    st.header(title, anchor=False, icon=icon)
    st.caption(description)
    if chips := filter_chips():
        st.markdown(chips)


def empty_state(what: str = "events", key: str = "empty_reset") -> None:
    """Explain that the filters removed everything, show which ones, and offer a one-click reset."""
    from app.components.filters import _reset  # local import: filters imports data_source too

    st.info(f"**No {what} match the current filters.** Widen the date range or clear a filter in the sidebar.",
            icon=":material/filter_alt_off:")
    st.button("Reset all filters", key=key, on_click=_reset, icon=":material/restart_alt:")
