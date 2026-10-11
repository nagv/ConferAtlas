"""Feedback analyzer: ratings, sentiment by event, keyword themes, and the AI comment analysis."""
from __future__ import annotations

import hashlib

import pandas as pd

from backend.serialize import dataclass_dict, table
from backend.services.selection import scoped
from backend.store import StoredDataset
from core.ai.feedback import (
    DEFAULT_BATCH_TOKENS,
    DEFAULT_MAX_TOTAL_TOKENS,
    analyze_feedback,
    batch_comments,
    estimate_tokens,
    prepare_comments,
)
from core.ai.llm import UsageRecorder
from core.analysis import Tables, feedback_summary, keyword_themes, rating_distribution, sentiment_by_event
from core.config import get_settings
from core.filters import prune
from shared.models import FeedbackPageResponse, FeedbackPreview, FeedbackRequest

RATINGS = [1, 2, 3, 4, 5]


def selected_feedback(t: Tables, req: FeedbackRequest) -> pd.DataFrame:
    """Feedback rows with their session track, narrowed by the page's rating and track filters."""
    fb = t["feedback.csv"].merge(t["sessions.csv"][["session_id", "track"]], on="session_id", how="left")
    ratings = list(prune(req.ratings, RATINGS))
    tracks = list(prune(req.tracks, sorted(fb["track"].dropna().unique())))
    if ratings:
        fb = fb[fb["rating"].isin(ratings)]
    if tracks:
        fb = fb[fb["track"].isin(tracks)]
    return fb.reset_index(drop=True)


def fingerprint(prepared: pd.DataFrame) -> str:
    text = prepared[["comment", "mentions", "avg_rating"]].to_json(orient="records")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(ds: StoredDataset, req: FeedbackRequest) -> FeedbackPageResponse:
    _, t = scoped(ds, req)
    if t["feedback.csv"].empty:
        return FeedbackPageResponse(empty=True)
    merged = t["feedback.csv"].merge(t["sessions.csv"][["session_id", "track"]], on="session_id", how="left")
    out = FeedbackPageResponse(empty=False, track_options=sorted(merged["track"].dropna().unique()))
    fb = selected_feedback(t, req)
    if fb.empty:
        out.no_match = True
        return out
    out.summary = dataclass_dict(feedback_summary(fb))
    out.rating_distribution = table(rating_distribution(fb))
    out.sentiment_by_event = table(sentiment_by_event(fb, t["events.csv"]))
    out.keyword_themes = table(keyword_themes(fb))
    prepared = prepare_comments(fb)
    if not prepared.empty:
        batches, skipped = batch_comments(prepared, DEFAULT_BATCH_TOKENS, DEFAULT_MAX_TOTAL_TOKENS)
        out.preview = FeedbackPreview(
            comments=len(prepared) - skipped, prepared=len(prepared), mentions=int(prepared["mentions"].sum()),
            tokens=sum(estimate_tokens(c) for b in batches for c in b["comment"]), batches=len(batches),
            skipped=skipped)
        out.fingerprint = fingerprint(prepared)
    return out


def analyse(ds: StoredDataset, req: FeedbackRequest, recorder: UsageRecorder):
    """Run the AI analysis on the selected comments (raises the core errors unchanged)."""
    _, t = scoped(ds, req)
    return analyze_feedback(selected_feedback(t, req), settings=get_settings(), recorder=recorder)
