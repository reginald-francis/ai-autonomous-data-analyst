"""Ingestion configuration — a small, standalone config module.

Deliberately separate from src/config.py: ingestion is a separate deployable
component (its own requirements file, its own container in Phase 9) with its
own environment, not a submodule of the serving app's Settings.
"""

import os


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
