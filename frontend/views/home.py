"""A concise introduction and starting points for the analytics workspace."""
from __future__ import annotations

import streamlit as st


_INTRO = """
<style>
.ca-hero {
  position: relative;
  overflow: hidden;
  padding: clamp(1.5rem, 4vw, 3rem);
  border: 1px solid #29476c;
  border-radius: 22px;
  background: radial-gradient(ellipse at 100% 0%, #245a73 0%, transparent 55%),
              linear-gradient(120deg, #132743, #1e3a60);
  box-shadow: 0 12px 32px rgba(23, 43, 77, 0.12);
  color: #ffffff;
}
.ca-eyebrow {
  margin: 0 0 1.25rem;
  color: #b9d9f5;
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.ca-hero h1 {
  max-width: 760px;
  margin: 0;
  padding: 0;
  color: #ffffff;
  font-size: clamp(2rem, 3.5vw, 3.2rem);
  line-height: 1.15;
}
.ca-hero h1 span { color: #9bdeeb; }
.ca-hero .ca-description {
  max-width: 720px;
  margin: 1.25rem 0 1.5rem;
  color: #e0eaf6;
  line-height: 1.75;
}
.ca-tags { display: flex; flex-wrap: wrap; gap: 0.6rem; }
.ca-tags span {
  padding: 0.4rem 0.8rem;
  border: 1px solid rgba(196, 220, 247, 0.3);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  color: #e0eaf6;
  font-size: 0.8rem;
}
.ca-benefits { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
.ca-benefit {
  padding: 1.5rem;
  background: #ffffff;
  border: 1px solid #dfe7f1;
  border-radius: 14px;
  box-shadow: 0 3px 14px rgba(23, 43, 77, 0.045);
}
.ca-benefit .ca-index {
  display: inline-block;
  padding: 0.4rem 0.65rem;
  border-radius: 8px;
  color: #1d4ed8;
  background: #edf3ff;
  font-size: 0.8rem;
  font-weight: 600;
}
.ca-benefit h3 { color: #172b4d; font-size: 1.1rem; padding: 1rem 0 0.5rem; margin: 0; }
.ca-benefit p { color: #526580; line-height: 1.7; margin: 0; }
.ca-audience {
  border-left: 3px solid #38a3b8;
  padding: 0.3rem 1rem;
  margin-top: 0.5rem;
  color: #526580;
  line-height: 1.7;
}
.ca-audience strong { color: #172b4d; }
@media (max-width: 800px) {
  .ca-benefits { grid-template-columns: 1fr; }
}
</style>
<section class="ca-hero" aria-label="Welcome to ConferAtlas">
  <p class="ca-eyebrow">ConferAtlas · Conference intelligence</p>
  <h1>Turn conference data into<br><span>a clearer story of impact.</span></h1>
  <p class="ca-description">Understand how your events performed, show sponsors the value they received,
  and use audience feedback to shape what comes next. One workspace brings the evidence together.</p>
  <div class="ca-tags"><span>Portfolio analytics</span><span>Sponsor reporting</span><span>AI-assisted insights</span></div>
</section>
"""

_BENEFITS = """
<div class="ca-benefits">
  <article class="ca-benefit">
    <span class="ca-index">01 / Event performance</span>
    <h3>See the whole picture</h3>
    <p>Compare attendance, engagement and satisfaction across your portfolio. Drill into an event to
    understand what worked and where performance changed.</p>
  </article>
  <article class="ca-benefit">
    <span class="ca-index">02 / Sponsor value</span>
    <h3>Make the value visible</h3>
    <p>Track leads against targets, review cost per lead and understand the audience reached.
    Bring the results together in a report your sponsors can use.</p>
  </article>
  <article class="ca-benefit">
    <span class="ca-index">03 / Audience insights</span>
    <h3>Plan with better evidence</h3>
    <p>Explore who attended, uncover feedback themes and ask questions about the portfolio.
    AI-assisted summaries help turn the findings into a useful starting point.</p>
  </article>
</div>
<p class="ca-audience"><strong>For conference teams.</strong> Built for organisers, event leaders and
sponsorship teams. Staff use the workspace; sponsors receive exported reports.</p>
"""


def render(pages: dict) -> None:
    st.html(_INTRO)
    left, right = st.columns(2)
    left.page_link(pages["overview"], label="Explore the portfolio", icon=":material/arrow_forward:")
    right.page_link(pages["sponsor_report"], label="Prepare a sponsor report", icon=":material/description:")

    st.subheader("From event results to informed decisions", anchor=False)
    st.html(_BENEFITS)

    st.subheader("Find your starting point", anchor=False)
    left, right = st.columns(2)
    with left:
        st.page_link(pages["audience"], label="Explore audience insights", icon=":material/groups:")
        st.caption("Understand attendee roles, industries and returning audiences.")
    with right:
        st.page_link(pages["feedback"], label="Review attendee feedback", icon=":material/reviews:")
        st.caption("Review ratings and comments to guide your next event.")

    st.caption("New here? Explore the built-in synthetic demo, or use your team's dataset once staff sign-in "
               "is configured. AI features require a configured provider and human review before sharing.")
