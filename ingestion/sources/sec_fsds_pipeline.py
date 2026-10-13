"""Joins the downloader and the bronze writer: decide what a quarter needs,
download if necessary, convert to bronze, and report what happened.

Nothing here talks to the command line - cli.py parses flags and prints; this
module just takes quarters in and returns QuarterOutcome objects, which keeps
it easy to test and reusable (Phase 9's scheduled job can call it directly).

For one quarter, ingest_quarter() takes the cheapest path that is still correct:
  1. SEC headers match the manifest and bronze is complete  -> do nothing.
  2. Headers match but bronze is stale or missing (e.g. SCHEMA_VERSION was
     bumped, or files were deleted)                          -> rebuild from the
     ZIP already on disk, no re-download.
  3. Otherwise (new quarter, headers differ, or --force)     -> download, then
     convert. write_bronze() makes the final call by comparing the ZIP's
     sha256, so an identical re-download still changes nothing.
"""

import logging
import os
from dataclasses import dataclass

import requests

from ingestion import storage
from ingestion.sources import sec_fsds, sec_fsds_bronze, sec_fsds_schema

logger = logging.getLogger(__name__)

# Failures we expect in normal operation - a quarter the SEC hasn't published
# yet (404), a dropped connection, a file that doesn't match the schema. These
# become a "failed" outcome so one bad quarter doesn't stop a backfill. Anything
# else is a bug, and is left to crash loudly with a full traceback.
_EXPECTED_ERRORS = (sec_fsds_bronze.BronzeError, requests.RequestException)


@dataclass
class QuarterOutcome:
    """What happened to one quarter.

    status is one of:
      ingested, unchanged, failed          (from ingest_quarter / run_ingest)
      would_ingest, up_to_date             (from check_quarter / run_check)
    """

    quarter: str
    status: str
    detail: str = ""
    row_counts: dict | None = None


def _bronze_is_complete(manifest: dict, lake_root: str, quarter: str) -> bool:
    """True if the manifest was built with the current schema and all four
    Parquet files are still on disk.
    """
    if manifest.get("schema_version") != sec_fsds_schema.SCHEMA_VERSION:
        return False
    return all(
        os.path.exists(storage.bronze_table_file(lake_root, table, quarter))
        for table in storage.TABLES
    )


def _remote_headers(quarter: str, user_agent: str) -> dict:
    """The SEC's current ETag/Last-Modified for a quarter, or Nones if the
    HEAD request fails - the manifest then records "unknown", which only
    means the next run does a full download rather than trusting headers.
    """
    try:
        return sec_fsds.get_remote_metadata(quarter, user_agent)
    except requests.RequestException:
        return {"etag": None, "last_modified": None}


def ingest_quarter(
    quarter: str, lake_root: str, user_agent: str, force: bool = False
) -> QuarterOutcome:
    """Bring one quarter's bronze up to date. Lets errors propagate - use
    run_ingest() to loop over several quarters without stopping at a failure.

    force=True skips the shortcuts and rebuilds from a fresh download.
    """
    manifest = storage.read_manifest(lake_root, quarter)
    zip_path = storage.landing_zip_path(lake_root, quarter)

    headers_unchanged = (
        not force
        and manifest is not None
        and not sec_fsds.needs_download(quarter, lake_root, user_agent)
    )

    if headers_unchanged and _bronze_is_complete(manifest, lake_root, quarter):
        logger.info(f"{quarter}: up to date, nothing downloaded")
        return QuarterOutcome(
            quarter, "unchanged", "SEC headers match the manifest; nothing downloaded",
            manifest.get("row_counts"),
        )

    if headers_unchanged and os.path.exists(zip_path):
        logger.info(f"{quarter}: rebuilding bronze from the saved ZIP, no re-download")
        etag, last_modified = manifest.get("etag"), manifest.get("last_modified")
    else:
        zip_path = sec_fsds.download_quarter(quarter, lake_root, user_agent)
        remote = _remote_headers(quarter, user_agent)
        etag, last_modified = remote["etag"], remote["last_modified"]

    result = sec_fsds_bronze.write_bronze(
        quarter, zip_path, lake_root, etag=etag, last_modified=last_modified, force=force
    )

    if result.status == "unchanged":
        # Downloaded, but the content was identical. If the SEC's headers have
        # still moved (e.g. a re-upload of the same bytes), record the new ones -
        # otherwise the cheap header check would flag this quarter every week.
        current = result.manifest
        if (etag or last_modified) and (etag, last_modified) != (
            current.get("etag"), current.get("last_modified")
        ):
            storage.write_manifest(
                lake_root, quarter, {**current, "etag": etag, "last_modified": last_modified}
            )
        return QuarterOutcome(
            quarter, "unchanged", "downloaded, but identical to what's already in bronze",
            current.get("row_counts"),
        )

    return QuarterOutcome(quarter, "ingested", "", result.manifest.get("row_counts"))


def check_quarter(quarter: str, lake_root: str, user_agent: str) -> QuarterOutcome:
    """Would ingest_quarter() do any work? Looks at the manifest, bronze files
    and a HEAD request - never downloads.
    """
    manifest = storage.read_manifest(lake_root, quarter)
    if manifest is None:
        return QuarterOutcome(quarter, "would_ingest", "never ingested")
    if sec_fsds.needs_download(quarter, lake_root, user_agent):
        return QuarterOutcome(
            quarter, "would_ingest", "SEC headers differ from the manifest, or can't be compared"
        )
    if not _bronze_is_complete(manifest, lake_root, quarter):
        return QuarterOutcome(
            quarter, "would_ingest", "bronze is incomplete or built with an older schema"
        )
    return QuarterOutcome(quarter, "up_to_date", "", manifest.get("row_counts"))


def run_ingest(
    quarters: list[str], lake_root: str, user_agent: str, force: bool = False
) -> list[QuarterOutcome]:
    """ingest_quarter() for each quarter in turn. An expected failure (see
    _EXPECTED_ERRORS) is recorded as a "failed" outcome and the loop carries
    on, so one unpublished quarter doesn't abandon a 70-quarter backfill.
    """
    outcomes = []
    for quarter in quarters:
        try:
            outcomes.append(ingest_quarter(quarter, lake_root, user_agent, force=force))
        except _EXPECTED_ERRORS as e:
            logger.error(f"{quarter}: failed - {type(e).__name__}: {e}")
            outcomes.append(QuarterOutcome(quarter, "failed", f"{type(e).__name__}: {e}"))
    return outcomes


def run_check(quarters: list[str], lake_root: str, user_agent: str) -> list[QuarterOutcome]:
    """check_quarter() for each quarter."""
    return [check_quarter(quarter, lake_root, user_agent) for quarter in quarters]
