"""RAG Lab: see what document search sees, change one setting, and watch the result change.

Four tabs, all served by the backend (this page only displays):
- Chunks: one document cut by a chunking setting, chunk by chunk, beside a second setting, plus the
  size of every chunk in the corpus.
- Search: the tuning panel (index, mode, hybrid weight, candidates, top-k, re-ranking, thresholds) and
  the retrieval inspector (every candidate's scores, how re-ranking moved it, the refusal line).
- Embedding map: every chunk as a point in 2D, with a question placed among them.
- Experiments: the evaluation results, once scripts/run_eval.py has run.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from frontend import client
from frontend.components.charts import plot
from frontend.components.data_source import call, scope_request
from frontend.components.layout import page_header
from frontend.components.theme import COLORWAY

CORPUS_LABELS = {"event_reports": "Event reports", "pricing": "Pricing", "sponsor_decks": "Sponsor decks",
                 "operations": "Operations SOPs", "session_abstracts": "Session abstracts"}
CORPUS_COLORS = dict(zip(CORPUS_LABELS, COLORWAY))
CORPUS_SYMBOLS = dict(zip(CORPUS_LABELS, ("circle", "square", "diamond", "triangle-up", "hexagon")))
STRATEGIES = {"structure": "Structure-aware", "fixed": "Fixed size", "semantic": "Semantic"}
DEFAULT_SIZES = {"structure": 400, "fixed": 500, "semantic": 400}
BANDS = (":blue-background", ":orange-background")


def render() -> None:
    page_header("RAG Lab", "See how documents are cut into chunks, how search scores them, and where they sit "
                "in embedding space. Change one setting and watch the result change.", icon=":material/science:")
    status = client.status()
    docs = status.get("documents") or {}
    if not docs.get("available"):
        st.info(f"{docs.get('note') or 'Document search is off.'} The Chunks tab works without it.",
                icon=":material/info:")
    else:
        st.caption(f"Index: {docs['chunks']} structure-aware chunks, {docs['embedding_model']} "
                   f"({docs['dimension']} dimensions). Built settings: {', '.join(docs['chunk_settings'])}.")
    chunks_tab, search_tab, map_tab, experiments_tab = st.tabs(["Chunks", "Search", "Embedding map", "Experiments"])
    with chunks_tab:
        _chunks(status)
    with search_tab:
        _search(docs.get("available", False))
    with map_tab:
        _map(docs.get("available", False))
    with experiments_tab:
        _experiments()


# --- chunks ---------------------------------------------------------------------------------------

def _setting_controls(prefix: str, default: str) -> dict:
    strategy = st.selectbox("Strategy", list(STRATEGIES), index=list(STRATEGIES).index(default),
                            format_func=STRATEGIES.get, key=f"{prefix}_strategy")
    size = st.slider("Chunk size (tokens)", 100, 800, DEFAULT_SIZES[strategy], step=50, key=f"{prefix}_size_{strategy}")
    overlap = 0
    if strategy == "fixed":
        overlap = st.slider("Overlap (tokens)", 0, 200, 50, step=10, key=f"{prefix}_overlap")
    return {"strategy": strategy, "chunk_size": size, "overlap": overlap}


def _chunks(status: dict) -> None:
    documents = client.rag_documents()
    by_id = {d["doc_id"]: d for d in documents}
    doc_id = st.selectbox("Document", list(by_id), key="lab_doc",
                          format_func=lambda d: f"{CORPUS_LABELS[by_id[d]['corpus']]} · {by_id[d]['title']}")
    contextual = st.toggle("Add the contextual header to each chunk", value=True, key="lab_contextual")
    left, right = st.columns(2)
    results = []
    for column, prefix, default in ((left, "lab_a", "structure"), (right, "lab_b", "fixed")):
        with column:
            setting = _setting_controls(prefix, default)
            if setting["strategy"] == "semantic" and not (status.get("documents") or {}).get("embedding_model"):
                st.info("Semantic chunking needs the embedding model (see the note above).")
                results.append(None)
                continue
            with st.spinner("Chunking..."):
                result = _safe(client.rag_chunks, {**setting, "doc_id": doc_id, "contextual": contextual})
            results.append(result)
            if result is None:
                continue
            built = "searchable" if result["built"] else "not indexed yet"
            st.markdown(f"**{len(result['chunks'])} chunks** from {result['units']} loaded "
                        f"{'pages' if result['source_type'] == 'pdf' else 'units'} · `{result['key']}` ({built})")
            for chunk in result["chunks"]:
                where = (f"p. {chunk['page']}" if chunk["page"] else f"slide {chunk['slide']}" if chunk["slide"]
                         else chunk["section"])
                band = BANDS[chunk["index"] % 2]
                with st.container(border=True):
                    st.markdown(f"{band}[**Chunk {chunk['index'] + 1}** · {chunk['tokens']} tokens · {where}]")
                    if chunk["header"]:
                        st.caption(chunk["header"])
                    st.markdown(_inline_headings(chunk["body"]))
    st.subheader("Chunk sizes across the whole corpus", anchor=False)
    fig = go.Figure()
    for result, name, colour in zip(results, ("Left", "Right"), COLORWAY):
        if result:
            fig.add_trace(go.Histogram(x=result["corpus_tokens"], name=f"{name}: {result['key']}", opacity=0.7,
                                       marker_color=colour, nbinsx=30))
    fig.update_layout(barmode="overlay", xaxis_title="Tokens per chunk", yaxis_title="Chunks", height=320,
                      margin={"t": 30, "b": 40, "l": 50, "r": 20}, legend={"orientation": "h", "y": 1.15})
    plot(st, fig, key="lab_hist")


def _inline_headings(text: str) -> str:
    """Show a chunk's "# Title" / "## Section" lines as bold text, so they don't become page headings."""
    return "\n".join(f"**{line.lstrip('#').strip()}**" if line.startswith("#") else line for line in text.splitlines())


def _safe(fn, *args):
    """A RAG Lab request; the problem is shown and None returned if it fails."""
    try:
        return fn(*args)
    except client.ApiError as exc:
        st.warning(exc.message, icon=":material/warning:")
        return None


# --- search ---------------------------------------------------------------------------------------

def _search(available: bool) -> None:
    if not available:
        st.caption("Search needs the document index (see the note above).")
        return
    question = st.text_input("Question", "Which sponsor at the San Francisco DevSecOps Forum 2025 asked for "
                             "attendee job titles in the lead export?", key="lab_question")
    with st.expander("Tuning panel", icon=":material/tune:", expanded=True):
        a, b, c = st.columns(3)
        chunking = a.selectbox("Index (chunking)", list(STRATEGIES), format_func=STRATEGIES.get, key="lab_s_chunking")
        size = a.slider("Chunk size (tokens)", 100, 800, DEFAULT_SIZES[chunking], step=50, key=f"lab_s_size_{chunking}")
        overlap = a.slider("Overlap", 0, 200, 50, step=10, key="lab_s_overlap") if chunking == "fixed" else None
        mode = b.radio("Search", ["hybrid", "dense", "bm25"], horizontal=True, key="lab_s_mode",
                       format_func={"hybrid": "Hybrid", "dense": "Dense", "bm25": "BM25"}.get)
        alpha = b.slider("Hybrid weight (dense share)", 0.0, 1.0, 0.5, 0.05, key="lab_s_alpha",
                         disabled=mode != "hybrid")
        corpora = b.multiselect("Corpora", list(CORPUS_LABELS), format_func=CORPUS_LABELS.get, key="lab_s_corpora",
                                placeholder="All corpora")
        rerank = c.toggle("Re-rank with FlashRank", True, key="lab_s_rerank")
        candidates = c.slider("Candidates", 5, 50, 20, key="lab_s_candidates")
        top_k = c.slider("Top-k kept", 1, 10, 5, key="lab_s_topk")
        threshold = c.slider("Refusal threshold (re-rank score)" if rerank else "Refusal threshold (cosine)",
                             0.0, 1.0, 0.05 if rerank else 0.45, 0.01, key=f"lab_s_threshold_{rerank}")
    settings = {"chunking": chunking, "mode": mode, "alpha": alpha, "candidates": candidates,
                "top_k": min(top_k, candidates), "rerank": rerank,
                "threshold": threshold if rerank else 0.05, "dense_threshold": threshold if not rerank else 0.45}
    if size != DEFAULT_SIZES[chunking]:
        settings["chunk_size"] = size
    if overlap is not None and overlap != 50:
        settings["overlap"] = overlap
    if not question.strip():
        return
    with st.spinner("Searching..."):
        result = call(client.rag_search, {**scope_request(), "question": question.strip(), "settings": settings,
                                          "corpora": corpora})
    if result is None:
        return
    if result["needs_build"]:
        st.warning(result["note"], icon=":material/construction:")
        if st.button("Build this setting now", icon=":material/build:", key="lab_build"):
            with st.spinner("Indexing (only new or changed chunks are embedded)..."):
                built = _safe(client.rag_build, {k: v for k, v in settings.items()
                                                 if k in ("chunking", "chunk_size", "overlap")})
            if built:
                client.clear_caches()
                st.success(f"Built `{built['key']}` in {built['seconds']:.0f}s "
                           f"({built['texts_embedded']} texts embedded).")
                st.rerun()
        return
    _inspector(result)


def _inspector(result: dict) -> None:
    seconds = result["seconds"]
    timing = " · ".join(f"{k} {v:.2f}s" for k, v in seconds.items())
    if result["refused"]:
        st.error(f"Refused: the best {result['score_name']} score is {result['best_score']:.3f}, below the threshold "
                 f"{result['threshold']:.2f}. The assistant would answer \"I couldn't find this in our documents.\"",
                 icon=":material/block:")
    else:
        st.success(f"{len(result['hit_ids'])} chunks kept as evidence · best {result['score_name']} score "
                   f"{result['best_score']:.3f} (threshold {result['threshold']:.2f})", icon=":material/check:")
    st.caption(f"Time: {timing}")
    rows = pd.DataFrame(result["candidates"])
    if rows.empty:
        st.info("No candidates matched.")
        return
    rows["kept"] = rows["chunk_id"].isin(result["hit_ids"])
    rows["moved"] = [(f"↑{b - a}" if a and a < b else f"↓{a - b}" if a and a > b else "–" if a else "dropped")
                     for a, b in zip(rows["rank_after"], rows["rank_before"])]
    score = "rerank" if result["score_name"] == "rerank" else ("dense" if result["score_name"] == "dense" else "hybrid")
    ordered = rows.sort_values(["kept", "rank_after", "rank_before"], ascending=[False, True, True])
    fig = go.Figure(go.Bar(
        x=ordered[score].fillna(0), y=[f"{r.citation} ({r.chunk_id.split(':')[-1]})" for r in ordered.itertuples()],
        orientation="h", marker_color=[COLORWAY[0] if k else "#CBD5E1" for k in ordered["kept"]],
        hovertext=[t[:200] for t in ordered["text"]], hoverinfo="text+x"))
    if result["score_name"] in ("rerank", "dense"):
        fig.add_vline(x=result["threshold"], line_dash="dash", line_color="#DC2626",
                      annotation_text="refusal threshold", annotation_position="top")
    fig.update_layout(height=max(260, 26 * len(ordered) + 80), yaxis={"autorange": "reversed"},
                      xaxis_title=f"{score} score", margin={"l": 10, "r": 20, "t": 30, "b": 40})
    plot(st, fig, key="lab_scores")
    st.dataframe(
        ordered[["rank_after", "rank_before", "moved", "citation", "dense", "bm25", "hybrid", "rerank", "corpus"]]
        .rename(columns={"rank_after": "final rank", "rank_before": "rank before re-rank"}),
        hide_index=True, width="stretch",
        column_config={"final rank": st.column_config.NumberColumn(help="0 = not kept"),
                       "dense": st.column_config.NumberColumn(format="%.3f", help="Cosine similarity"),
                       "bm25": st.column_config.NumberColumn(format="%.2f", help="Keyword score"),
                       "hybrid": st.column_config.NumberColumn(format="%.3f", help="Mixed, scaled 0..1"),
                       "rerank": st.column_config.NumberColumn(format="%.3f", help="FlashRank relevance")})
    with st.expander("Text of the kept chunks", icon=":material/article:"):
        for r in rows[rows["kept"]].sort_values("rank_after").itertuples():
            st.markdown(f"**{r.rank_after}. {r.citation}**")
            st.caption(r.text[:800])


# --- embedding map --------------------------------------------------------------------------------

def _map(available: bool) -> None:
    if not available:
        st.caption("The map needs the document index (see the note above).")
        return
    left, right = st.columns([1, 3])
    chunking = left.selectbox("Index", list(STRATEGIES), format_func=STRATEGIES.get, key="lab_map_chunking")
    question = right.text_input("Place a question on the map", "By what time must badge scanners be charged?",
                                key="lab_map_question")
    with st.spinner("Projecting vectors..."):
        result = _safe(client.rag_map, {"chunking": chunking, "question": question.strip() or None})
    if result is None:
        return
    points = pd.DataFrame(result["points"])
    fig = go.Figure()
    for corpus, label in CORPUS_LABELS.items():
        part = points[points["corpus"] == corpus]
        fig.add_trace(go.Scatter(
            x=part["x"], y=part["y"], mode="markers", name=label,
            marker={"color": CORPUS_COLORS[corpus], "symbol": CORPUS_SYMBOLS[corpus], "size": 9,
                    "line": {"color": "white", "width": 1.5}},
            hovertext=[f"<b>{t}</b><br>{s}<br>{p[:160]}…" for t, s, p in zip(part["title"], part["section"],
                                                                         part["preview"])],
            hoverinfo="text"))
    if result.get("question_point"):
        nearest = points[points["chunk_id"].isin(result["nearest"])]
        fig.add_trace(go.Scatter(x=nearest["x"], y=nearest["y"], mode="markers", name="Nearest 5",
                                 marker={"size": 18, "color": "rgba(0,0,0,0)", "line": {"color": "#0F172A", "width": 2}},
                                 hovertext=nearest["title"], hoverinfo="text"))
        qx, qy = result["question_point"]
        fig.add_trace(go.Scatter(x=[qx], y=[qy], mode="markers", name="Question",
                                 marker={"symbol": "star", "size": 18, "color": "#0F172A"},
                                 hovertext=[question], hoverinfo="text"))
    fig.update_layout(height=620, xaxis_title="PCA axis 1", yaxis_title="PCA axis 2",
                      legend={"orientation": "h", "y": -0.12}, margin={"t": 30, "l": 50, "r": 20, "b": 60})
    plot(st, fig, key="lab_map")
    st.caption(f"{len(points)} chunks embedded with {result['model']}. The two axes keep "
               f"{result['explained']:.0%} of the spread, so distances on the map are approximate; search uses the "
               "full vectors. Hover a point to read it.")


# --- experiments ----------------------------------------------------------------------------------

def _experiments() -> None:
    data = _safe(client.rag_experiments)
    if not data or not data["available"]:
        st.info("No evaluation results yet. They appear here after the evaluation runs "
                "(`python scripts/run_eval.py`, Step 10).", icon=":material/lab_research:")
        return
    results = pd.DataFrame(data["results"])
    st.caption(f"Generated {data['generated_at']}. One row per setting, over the 20 golden questions.")
    metrics = [c for c in ("hit_rate", "mrr", "faithfulness", "relevance", "refusal_accuracy", "routing_accuracy")
               if c in results.columns]
    fig = go.Figure([go.Bar(name=m.replace("_", " "), x=results["setting"], y=results[m], marker_color=colour)
                     for m, colour in zip(metrics, COLORWAY)])
    fig.update_layout(barmode="group", yaxis={"range": [0, 1], "tickformat": ".0%"}, height=380,
                      legend={"orientation": "h", "y": 1.15}, margin={"t": 40, "b": 60, "l": 50, "r": 20})
    plot(st, fig, key="lab_experiments")
    st.dataframe(results, hide_index=True, width="stretch")
