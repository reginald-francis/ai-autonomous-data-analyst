"""Ingestion configuration — a small, standalone config module.

Deliberately separate from src/config.py: ingestion is a separate deployable
component (its own requirements file, its own container in Phase 9) with its
own environment, not a submodule of the serving app's Settings.
"""

import os

from dotenv import load_dotenv

# Mirrors src/services/llm_service.py's exact pattern: load .env once, at
# import time, so a local `python -m ingestion ...` picks up values from the
# repo's .env the same way the FastAPI app already does. Production (Phase
# 9's Cloud Run Jobs) sets real environment variables directly - load_dotenv()
# is a no-op there since there's no .env file to find.
load_dotenv()


class ConfigError(Exception):
    """Raised when required ingestion configuration is missing or invalid."""


def get_sec_user_agent() -> str:
    """Return the declared User-Agent for SEC EDGAR requests.

    Required by the SEC's fair-access policy (contact name + email) — see
    https://www.sec.gov/os/accessing-edgar-data. Fails fast rather than
    silently sending a generic header and risking a block.
    """
    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        raise ConfigError(
            "SEC_USER_AGENT environment variable is required, e.g. "
            '"Your Name your.email@example.com" — see docs/PHASE6_DESIGN.md section 5.'
        )
    return user_agent


def get_lake_root() -> str:
    """Return the root path of the data lake.

    Local disk by default; Phase 9 points this at a gs:// bucket via the
    same environment variable, since pyarrow has a built-in GCS filesystem.
    """
    return os.environ.get("INGESTION_LAKE_ROOT", "data/lake")


def get_max_download_attempts() -> int:
    """How many times to try a quarter download before giving up.

    Configurable because this is operational policy, not a fact about the
    SEC's API - unlike BASE_URL/the quarter-format regex in
    ingestion/sources/sec_fsds.py, which stay hardcoded there because they
    describe the source itself, not something an operator would ever tune.
    """
    return int(os.environ.get("INGESTION_MAX_DOWNLOAD_ATTEMPTS", "4"))


def get_retry_backoff_seconds() -> tuple[int, ...]:
    """Seconds to wait before each retry attempt (length = attempts - 1).

    Default widened 2026-10-08 after review: an initial (1, 2) was too short
    to ride out a real transient server issue (overload, brief outage) -
    these typically need 10-30s to recover. This runs as an unattended
    weekly background job (docs/PHASE6_DESIGN.md D7), so there's no cost to
    being patient rather than fast.
    """
    raw = os.environ.get("INGESTION_RETRY_BACKOFF_SECONDS")
    if raw:
        return tuple(int(part.strip()) for part in raw.split(","))
    return (2, 8, 20)
