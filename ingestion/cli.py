"""Command-line entry point for ingestion.

Usage (once Steps 4-6 land):
    python -m ingestion --source sec_fsds --quarter 2026q2
    python -m ingestion --source sec_fsds --from 2024q1 --to 2026q2
    python -m ingestion --source sec_fsds --check

Argument parsing is wired up now; the actual check/download/convert dispatch
is implemented in Steps 4-6 as the downloader and bronze writer land.
"""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ingestion", description=__doc__)
    parser.add_argument("--source", required=True, choices=["sec_fsds"], help="Which source to ingest")
    parser.add_argument("--quarter", help="A single quarter, e.g. 2026q2")
    parser.add_argument("--from", dest="from_quarter", help="Start of a quarter range, e.g. 2024q1")
    parser.add_argument("--to", dest="to_quarter", help="End of a quarter range, e.g. 2026q2")
    parser.add_argument("--check", action="store_true", help="List new or changed quarters without downloading")
    return parser


def main(argv=None) -> int:
    build_parser().parse_args(argv)
    # Real dispatch (check / download / convert) lands in Steps 4-6.
    raise NotImplementedError(
        "Ingestion dispatch not yet implemented — see docs/PHASE6_DESIGN.md Phase 6 Steps 4-6."
    )


if __name__ == "__main__":
    sys.exit(main())
