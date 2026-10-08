"""SEC EDGAR Financial Statement Data Sets — download and change detection.

URL pattern confirmed against the real SEC download page (2026-10-07):
    https://www.sec.gov/files/dera/data/financial-statement-data-sets/<quarter>.zip
e.g. https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q2.zip
Assumed consistent across the full 2009q1-present history — the owner
confirmed the 2026q2 link directly; older quarters are verified in Step 6
when a real range gets ingested.

Converting a downloaded ZIP into bronze Parquet lands in Step 5 — this
module only downloads and detects whether a quarter is new or changed.
"""

import hashlib
import json
import os
import re
import time

import requests

from ingestion import config, storage

BASE_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets"

_QUARTER_RE = re.compile(r"^(?P<year>\d{4})q(?P<q>[1-4])$")


class QuarterFormatError(ValueError):
    """Raised when a quarter string isn't in the expected YYYYqN form."""


def parse_quarter(quarter: str) -> tuple[int, int]:
    """Parse "2026q2" into (2026, 2); raises QuarterFormatError otherwise."""
    match = _QUARTER_RE.match(quarter)
    if not match:
        raise QuarterFormatError(
            f"Invalid quarter {quarter!r} - expected the form YYYYqN, e.g. '2026q2'."
        )
    return int(match.group("year")), int(match.group("q"))


def quarter_zip_url(quarter: str) -> str:
    """Build the SEC's download URL for one quarter's ZIP."""
    parse_quarter(quarter)  # raises QuarterFormatError on a bad quarter string
    return f"{BASE_URL}/{quarter}.zip"


def _is_transient(exc: Exception) -> bool:
    """True for failures worth retrying: network-level issues, and 5xx
    server errors (often transient - an overloaded server, a brief outage).
    False for 4xx client errors (e.g. 404 - the quarter genuinely isn't at
    this URL; retrying can't fix that) and anything else unexpected.
    """
    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        return response is not None and response.status_code >= 500
    return False


def download_quarter(quarter: str, lake_root: str, user_agent: str) -> str:
    """Download one quarter's ZIP into the landing zone, streamed to disk
    (quarters run 60-120MB, too large to hold fully in memory).

    Retries (count and backoff from ingestion/config.py - operational
    policy, not a fact about the SEC, so it's configurable rather than
    hardcoded here) on transient failures (dropped connection, stalled
    read, 5xx) - see _is_transient(). A genuine 4xx error (e.g. 404) raises
    immediately without retrying.

    Written atomically: downloads to a .tmp path first and renames into
    place with os.replace() (atomic, and overwrite-safe on both Windows and
    POSIX - unlike os.rename()) only once the download fully succeeds. Every
    failed attempt - transient or not - deletes its own partial .tmp file
    before either retrying or raising, so a failure never leaves a
    half-downloaded file sitting at the final path looking like a complete
    one.

    Sends the SEC's required fair-access User-Agent header (contact name +
    email - see docs/PHASE6_DESIGN.md section 4). Returns the final path the
    ZIP was written to.
    """
    url = quarter_zip_url(quarter)
    dest_path = storage.landing_zip_path(lake_root, quarter)
    tmp_path = dest_path + ".tmp"
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    max_attempts = config.get_max_download_attempts()
    backoff_seconds = config.get_retry_backoff_seconds()

    for attempt in range(max_attempts):
        try:
            with requests.get(
                url, headers={"User-Agent": user_agent}, stream=True, timeout=60
            ) as response:
                response.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            os.replace(tmp_path, dest_path)
            return dest_path
        except Exception as exc:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            is_last_attempt = attempt == max_attempts - 1
            if is_last_attempt or not _is_transient(exc):
                raise
            time.sleep(backoff_seconds[attempt])

    # Unreachable - the loop above always either returns or raises.
    raise AssertionError("unreachable")


def get_remote_metadata(quarter: str, user_agent: str) -> dict:
    """HEAD the quarter's URL and return whatever change-detection headers
    the server provides, without downloading the body.

    Returns a dict with possibly-None 'etag', 'last_modified', and
    'content_length' values - some static file servers omit one or more of
    these, so callers must treat their absence as "can't tell", never as
    "unchanged".
    """
    url = quarter_zip_url(quarter)
    response = requests.head(url, headers={"User-Agent": user_agent}, timeout=30)
    response.raise_for_status()
    return {
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "content_length": response.headers.get("Content-Length"),
    }


def needs_download(quarter: str, lake_root: str, user_agent: str) -> bool:
    """Cheap pre-check: can we tell, from headers alone, that this quarter is
    unchanged since the last successful ingest - without downloading the
    full ZIP?

    Compares the manifest's recorded etag/last_modified (written once Step 5
    lands - see docs/PHASE6_DESIGN.md section 5 item 2) against a fresh HEAD
    request's headers. Defaults to True (download) whenever there isn't
    enough information to safely say "unchanged": no existing manifest,
    missing headers on either side, or a HEAD request that fails for any
    reason. This function can only ever cause an *extra* download, never a
    missed one - has_quarter_changed()'s full sha256 comparison after a real
    download remains the authoritative check.
    """
    existing = read_manifest(lake_root, quarter)
    if existing is None:
        return True

    try:
        remote = get_remote_metadata(quarter, user_agent)
    except requests.RequestException:
        return True  # can't check cheaply - fall back to a real download

    if existing.get("etag") and remote.get("etag"):
        return existing["etag"] != remote["etag"]

    if existing.get("last_modified") and remote.get("last_modified"):
        return existing["last_modified"] != remote["last_modified"]

    return True  # neither side has enough to compare safely


def sha256_of_file(path: str) -> str:
    """Return the sha256 hex digest of a file's contents, read in chunks
    so large ZIPs never need to be fully loaded into memory.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(lake_root: str, quarter: str) -> dict | None:
    """Return a quarter's existing manifest dict, or None if none exists yet."""
    path = storage.manifest_path(lake_root, quarter)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def has_quarter_changed(quarter: str, zip_path: str, lake_root: str) -> bool:
    """True if this quarter has never been ingested, or its freshly downloaded
    ZIP's hash differs from the one recorded in the existing manifest - e.g.
    the SEC republished it (see the Dec 2024 full-history republication,
    docs/PHASE6_DESIGN.md section 4). This is the authoritative check, run
    after a real download - unlike needs_download()'s cheap header pre-check,
    this always correctly detects a real content change.
    """
    existing = read_manifest(lake_root, quarter)
    if existing is None:
        return True
    return sha256_of_file(zip_path) != existing.get("sha256")
