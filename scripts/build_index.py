"""Build (or check) the vector index of the document corpus.

Usage, from the project root:
    python scripts/build_index.py                      # the structure-aware index (what the app uses)
    python scripts/build_index.py --strategy all       # fixed, structure and semantic (for the evaluation)
    python scripts/build_index.py --status             # list collections, models and chunk counts
    python scripts/build_index.py --reset              # delete the index first, then build

Uses EMBEDDING_PROVIDER and EMBEDDING_MODEL from .env. Only changed documents are re-embedded, and
every vector is cached, so running it again is fast and free.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import get_settings  # noqa: E402
from core.rag.chunking import PRESETS  # noqa: E402
from core.rag.embeddings import EmbeddingsNotConfiguredError, build_embeddings  # noqa: E402
from core.rag.index import VectorIndex, build  # noqa: E402
from core.rag.loaders import load_corpus  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strategy", choices=[*PRESETS, "all"], default="structure")
    parser.add_argument("--status", action="store_true", help="only list what is in the index")
    parser.add_argument("--reset", action="store_true", help="delete every collection before building")
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        embeddings = build_embeddings(settings)
    except EmbeddingsNotConfiguredError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Embedding model: {embeddings.model} ({settings.embedding_provider})")
    print(f"Index folder:    {settings.index_dir}")

    if args.status:
        _print_status(VectorIndex(settings, embeddings).status())
        return 0
    if args.reset:
        VectorIndex(settings, embeddings).reset()
        print("Deleted every collection.")

    configs = list(PRESETS.values()) if args.strategy == "all" else [PRESETS[args.strategy]]
    units = load_corpus(settings.data_dir)
    print(f"Loaded {len(units)} units from {settings.data_dir / 'docs'}\n")
    try:
        index, reports, seconds = build(settings, units, configs, embeddings)
    except Exception as exc:  # noqa: BLE001 - provider errors are shown, not raised
        print(f"Indexing failed: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 1
    print(f"{'collection':<52} {'added':>5} {'updated':>7} {'same':>5} {'removed':>7} {'chunks':>6}")
    for r in reports:
        print(f"{r.collection:<52} {r.added:>5} {r.updated:>7} {r.unchanged:>5} {r.removed:>7} {r.chunks:>6}")
    print(f"\nDone in {seconds:.1f} s; {embeddings.texts_embedded} texts sent to the embedding model "
          "(the rest came from the cache).")
    return 0


def _print_status(rows: list[dict]) -> None:
    if not rows:
        print("The index is empty. Run: python scripts/build_index.py")
        return
    print(f"\n{'collection':<52} {'model':<26} {'dim':>5} {'chunks':>6}")
    for r in rows:
        print(f"{r['collection']:<52} {r['embedding_model'][:26]:<26} {r['dimension']:>5} {r['chunks']:>6}")


if __name__ == "__main__":
    raise SystemExit(main())
