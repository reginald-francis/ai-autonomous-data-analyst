"""Command-line entry point for ingestion: python -m ingestion ...

This module only turns flags into calls and results into output. The decisions
about what a quarter needs live in sources/sec_fsds_pipeline.py.

Exit codes - what a scheduler (Phase 9's Airflow / Cloud Scheduler) sees:
  0  everything succeeded (or --check ran)
  1  at least one quarter failed
  2  couldn't start: bad arguments or missing configuration
"""

import argparse
import logging
import sys
from collections import Counter

from ingestion import config, storage
from ingestion.sources import sec_fsds, sec_fsds_pipeline

_EXAMPLES = """\
examples:
  python -m ingestion --source sec_fsds --quarter 2026q2
  python -m ingestion --source sec_fsds --from 2024q1 --to 2026q2
  python -m ingestion --source sec_fsds --from 2024q1 --to 2026q2 --check
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ingestion",
        description="Download SEC Financial Statement Data Sets and land them as bronze Parquet.",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Only one source exists today; the flag is there so adding another later
    # doesn't change how the command is run.
    parser.add_argument("--source", required=True, choices=["sec_fsds"], help="Which source to ingest")
    parser.add_argument("--quarter", help="A single quarter, e.g. 2026q2")
    parser.add_argument("--from", dest="from_quarter", help="Start of a quarter range, e.g. 2024q1")
    parser.add_argument("--to", dest="to_quarter", help="End of a quarter range, e.g. 2026q2")
    parser.add_argument(
        "--check", action="store_true",
        help="List which quarters would be ingested, without downloading anything",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Rebuild even quarters that look up to date, from a fresh download",
    )
    return parser


def _resolve_quarters(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[str]:
    """The list of quarters the flags ask for, or a usage error (exit code 2)."""
    has_range = args.from_quarter is not None or args.to_quarter is not None
    if args.quarter and has_range:
        parser.error("use either --quarter or --from/--to, not both")
    if args.check and args.force:
        parser.error("--force has no effect with --check")
    try:
        if args.quarter:
            sec_fsds.parse_quarter(args.quarter)  # validate the format up front
            return [args.quarter]
        if args.from_quarter and args.to_quarter:
            return sec_fsds.quarters_in_range(args.from_quarter, args.to_quarter)
    except ValueError as e:  # a malformed quarter (QuarterFormatError) or a reversed range
        parser.error(str(e))
    parser.error("give --quarter, or both --from and --to")


def _format_outcome(outcome: sec_fsds_pipeline.QuarterOutcome) -> str:
    """One output line: quarter, status, row counts if known, and the detail."""
    parts = [f"{outcome.quarter}  {outcome.status:<12}"]
    if outcome.row_counts:
        # Always in table order. A fresh ingest has the counts in that order, but a
        # manifest stores its keys sorted, so a quarter read back from one wouldn't.
        order = {table: i for i, table in enumerate(storage.TABLES)}
        tables = sorted(outcome.row_counts, key=lambda t: order.get(t, len(order)))
        parts.append(" ".join(f"{table}={outcome.row_counts[table]:,}" for table in tables))
    if outcome.detail:
        parts.append(outcome.detail)
    return "  ".join(parts)


def _summary(outcomes: list[sec_fsds_pipeline.QuarterOutcome]) -> str:
    counts = Counter(o.status for o in outcomes)
    noun = "quarter" if len(outcomes) == 1 else "quarters"
    return f"{len(outcomes)} {noun}: " + ", ".join(f"{n} {status}" for status, n in counts.items())


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    quarters = _resolve_quarters(parser, args)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        user_agent = config.get_sec_user_agent()
    except config.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    lake_root = config.get_lake_root()

    if args.check:
        outcomes = sec_fsds_pipeline.run_check(quarters, lake_root, user_agent)
    else:
        outcomes = sec_fsds_pipeline.run_ingest(quarters, lake_root, user_agent, force=args.force)

    for outcome in outcomes:
        print(_format_outcome(outcome))
    print(_summary(outcomes))
    return 1 if any(o.status == "failed" for o in outcomes) else 0
