"""Draw the vector index as a 2D map: one point per chunk, coloured and shaped by corpus.

Usage, from the project root:
    python scripts/plot_embeddings.py
    python scripts/plot_embeddings.py --question "By what time must badge scanners be charged?"
    python scripts/plot_embeddings.py --strategy semantic --out my_map.html

With --question, the question is embedded, placed on the map as a star, and its five nearest chunks
(by real cosine distance, across every corpus) are ringed and listed. Writes an HTML file you open
in a browser; hover a point to see its document, section and text.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import get_settings  # noqa: E402
from core.docgen import CORPORA  # noqa: E402
from core.rag.chunking import PRESETS  # noqa: E402
from core.rag.embeddings import EmbeddingsNotConfiguredError, build_embeddings  # noqa: E402
from core.rag.index import VectorIndex  # noqa: E402
from core.rag.projection import pca_2d  # noqa: E402

# Categorical slots 1-5 of the reference palette (validated for colour-blind separation); shapes add
# a second cue because three of the colours are below 3:1 contrast on white.
STYLE = {
    "event_reports": ("#2a78d6", "circle", "Event reports"),
    "pricing": ("#eb6834", "square", "Pricing"),
    "sponsor_decks": ("#1baf7a", "diamond", "Sponsor decks"),
    "operations": ("#eda100", "triangle-up", "Operations SOPs"),
    "session_abstracts": ("#e87ba4", "hexagon", "Session abstracts"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strategy", choices=list(PRESETS), default="structure")
    parser.add_argument("--question", help="place this question on the map and ring its nearest chunks")
    parser.add_argument("--out", type=Path, help="HTML file to write (default data/index/embedding_map.html)")
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        embeddings = build_embeddings(settings)
    except EmbeddingsNotConfiguredError as exc:
        print(exc, file=sys.stderr)
        return 1
    index, config = VectorIndex(settings, embeddings), PRESETS[args.strategy]
    rows = []
    for corpus in CORPORA:
        collection = index.collection(corpus, config)
        if collection is None:
            print(f"No {args.strategy} index yet. Run: python scripts/build_index.py --strategy {args.strategy}",
                  file=sys.stderr)
            return 1
        got = index.get(corpus, config, include=["embeddings", "metadatas", "documents"])
        rows += [(v, m, d) for v, m, d in zip(got["embeddings"], got["metadatas"], got["documents"])]

    projection = pca_2d([r[0] for r in rows])
    nearest, star = [], None
    if args.question:
        import numpy as np
        q = np.asarray(embeddings.embed_query(args.question), dtype=float)
        vectors = np.asarray([r[0] for r in rows], dtype=float)
        sims = vectors @ q / (np.linalg.norm(vectors, axis=1) * np.linalg.norm(q))
        nearest = [int(i) for i in np.argsort(-sims)[:5]]
        star = projection.place([q])[0]
        print(f"\nNearest chunks to: {args.question}")
        for rank, i in enumerate(nearest, start=1):
            print(f"  {rank}. distance {1 - sims[i]:.3f}  {rows[i][1]['chunk_id']}")

    out = args.out or settings.index_dir / "embedding_map.html"
    _write(out, rows, projection, nearest, star, args, embeddings.model)
    print(f"\nWrote {out} ({len(rows)} chunks). Open it in your browser.")
    return 0


def _hover(meta: dict, text: str) -> str:
    body = text.split("\n\n", 1)[-1][:220].replace("\n", " ")
    return (f"<b>{html.escape(meta['title'])}</b><br>{html.escape(meta['section'] or '')}"
            f"<br><i>{meta['chunk_id']}</i> · {meta['tokens']} tokens<br>{html.escape(body)}…")


def _write(out: Path, rows, projection, nearest, star, args, model: str) -> None:
    import plotly.graph_objects as go

    fig = go.Figure()
    for corpus in CORPORA:
        colour, symbol, label = STYLE[corpus]
        idx = [i for i, r in enumerate(rows) if r[1]["corpus"] == corpus]
        fig.add_trace(go.Scatter(
            x=projection.points[idx, 0], y=projection.points[idx, 1], mode="markers", name=label,
            marker={"color": colour, "symbol": symbol, "size": 9, "line": {"color": "#fcfcfb", "width": 2}},
            text=[_hover(rows[i][1], rows[i][2]) for i in idx], hovertemplate="%{text}<extra></extra>"))
    if nearest:
        fig.add_trace(go.Scatter(
            x=projection.points[nearest, 0], y=projection.points[nearest, 1], mode="markers",
            name="Nearest 5 (rank on hover)",
            marker={"color": "rgba(0,0,0,0)", "size": 18, "line": {"color": "#1f1f1e", "width": 2}},
            hovertext=[f"<b>#{rank} nearest</b><br>" + _hover(rows[i][1], rows[i][2])
                       for rank, i in enumerate(nearest, start=1)], hoverinfo="text"))
        fig.add_trace(go.Scatter(
            x=[star[0]], y=[star[1]], mode="markers", name="Question",
            marker={"color": "#1f1f1e", "symbol": "star", "size": 18, "line": {"color": "#fcfcfb", "width": 2}},
            hovertext=[html.escape(args.question)], hoverinfo="text"))
    subtitle = (f"{len(rows)} {args.strategy} chunks · {model} · the two axes keep "
                f"{projection.explained:.0%} of the spread, so distances are approximate")
    fig.update_layout(
        title={"text": f"ConferAtlas vector index<br><sup>{subtitle}</sup>", "x": 0.02},
        template="plotly_white", paper_bgcolor="#fcfcfb", plot_bgcolor="#fcfcfb",
        font={"family": "system-ui, sans-serif", "color": "#1f1f1e"},
        xaxis={"title": "PCA axis 1", "showgrid": True, "gridcolor": "#ecebe7", "zeroline": False},
        yaxis={"title": "PCA axis 2", "showgrid": True, "gridcolor": "#ecebe7", "zeroline": False},
        legend={"orientation": "h", "y": -0.12}, height=720, margin={"l": 60, "r": 30, "t": 90, "b": 90},
        hoverlabel={"bgcolor": "#ffffff", "font": {"color": "#1f1f1e"}})
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out), include_plotlyjs="cdn", full_html=True)


if __name__ == "__main__":
    raise SystemExit(main())
