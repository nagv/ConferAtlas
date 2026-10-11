"""Generate (or just validate) the synthetic multi-table event dataset.

Usage, from the project root:
    python scripts/generate_data.py                       # writes data/synthetic/
    python scripts/generate_data.py --seed 7 --out data/other
    python scripts/generate_data.py --validate data/synthetic
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.generator import DEFAULT_SEED, generate_dataset, write_dataset  # noqa: E402
from core.schema import DatasetValidationError, load_dataset  # noqa: E402

DEFAULT_OUT = PROJECT_ROOT / "data" / "synthetic"


def _check(directory: Path) -> int:
    try:
        tables = load_dataset(directory)
    except DatasetValidationError as exc:
        print(f"Validation failed for {directory}:\n- " + "\n- ".join(exc.errors), file=sys.stderr)
        return 1
    for name, df in tables.items():
        print(f"  {name:<20} {len(df):>7,} rows")
    print(f"All {len(tables)} files are valid.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"random seed (default {DEFAULT_SEED})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default data/synthetic)")
    parser.add_argument("--validate", type=Path, metavar="DIR", help="only validate the CSVs in DIR")
    args = parser.parse_args(argv)

    if args.validate:
        return _check(args.validate)

    write_dataset(generate_dataset(args.seed), args.out)
    print(f"Wrote dataset (seed {args.seed}) to {args.out}")
    # Re-read from disk so we validate exactly what a user would load.
    return _check(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
