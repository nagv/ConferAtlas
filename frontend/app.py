"""ConferAtlas Streamlit entry point.

Run from the frontend folder:  streamlit run app.py  (the API must be running; see README)
"""
from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run app.py` puts frontend/ on sys.path, not the project root.
# Add the root so `import frontend.*` and `import shared` resolve.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from frontend import client  # noqa: E402

from frontend.components.ai_usage import render_ai_usage_panel  # noqa: E402
from frontend.components.auth import require_staff_access  # noqa: E402
from frontend.components.data_source import (  # noqa: E402
    render_brand_sidebar,
    render_data_source_sidebar,
    sync_uploaded_files,
)
from frontend.components.layout import apply_global_style  # noqa: E402
from frontend.components.filters import render_filter_sidebar  # noqa: E402
from frontend.views import (  # noqa: E402
    about as about_view,
    ai_insights,
    ask,
    audience,
    event_drilldown,
    feedback,
    home,
    overview,
    rag_lab,
    sponsor_performance,
    sponsor_report,
)

st.set_page_config(page_title="ConferAtlas", page_icon=":material/insights:", layout="wide",
                   initial_sidebar_state="auto")  # "auto" collapses the sidebar on phones
require_staff_access()
apply_global_style()
try:
    client.health()
except client.ApiError as exc:
    st.error(exc.message)
    st.info("Start the backend on port 8000 (see the README), then refresh this page.")
    st.stop()

about = st.Page(about_view.render, title="About", url_path="about", icon=":material/info:")
overview_page = st.Page(overview.render, title="Portfolio overview", url_path="overview",
                        icon=":material/dashboard:")
report_page = st.Page(sponsor_report.render, title="Sponsor report", url_path="sponsor-report",
                      icon=":material/badge:")
audience_page = st.Page(audience.render, title="Audience insights", url_path="audience-insights",
                        icon=":material/groups:")
feedback_page = st.Page(feedback.render, title="Feedback analyzer", url_path="feedback-analyzer",
                        icon=":material/reviews:")
welcome = st.Page(lambda: home.render({"overview": overview_page, "sponsor_report": report_page,
                                     "audience": audience_page, "feedback": feedback_page}),
                  title="Welcome", url_path="welcome", default=True, icon=":material/home:")
page = st.navigation({
    "Start here": [welcome],
    "Dashboards": [
        overview_page,
        st.Page(event_drilldown.render, title="Event drill-down", url_path="event-drill-down",
                icon=":material/event:"),
        st.Page(sponsor_performance.render, title="Sponsor performance", url_path="sponsor-performance",
                icon=":material/handshake:"),
        report_page,
        audience_page,
        feedback_page,
    ],
    "AI assistants": [
        st.Page(ask.render, title="Ask the portfolio", url_path="ask", icon=":material/chat:"),
        st.Page(ai_insights.render, title="AI insights", url_path="ai-insights", icon=":material/auto_awesome:"),
        st.Page(rag_lab.render, title="RAG Lab", url_path="rag-lab", icon=":material/science:"),
    ],
    "Help": [about],
})

sync_uploaded_files()
render_brand_sidebar(about)
render_filter_sidebar()
render_data_source_sidebar()
# Reserve the sidebar slot now, fill it after the page runs, so calls made on this run are included.
usage_slot = st.sidebar.container()
with st.spinner("Loading..."):
    page.run()
render_ai_usage_panel(usage_slot)
