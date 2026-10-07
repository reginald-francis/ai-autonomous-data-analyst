"""Bronze-layer path helpers for the SEC FSDS source.

Layout decided in docs/PHASE6_DESIGN.md section 5:
  <lake_root>/bronze/sec_fsds/<table>/quarter=<quarter>/part-0.parquet
  <lake_root>/bronze/sec_fsds/_manifests/<quarter>.json
  <lake_root>/landing/sec_fsds/<quarter>.zip   (raw ZIP retention, item 9)

Manifest read/write and atomic-write logic land in Step 5 — this module
currently holds only the path-building functions, since the layout is
already decided and every later step needs it.
"""

import os

SOURCE_NAME = "sec_fsds"
TABLES = ("sub", "num", "tag", "pre")


def bronze_table_dir(lake_root: str, table: str, quarter: str) -> str:
    """Path to a table's partition folder for one quarter."""
    return os.path.join(lake_root, "bronze", SOURCE_NAME, table, f"quarter={quarter}")


def manifest_path(lake_root: str, quarter: str) -> str:
    """Path to a quarter's manifest JSON file."""
    return os.path.join(lake_root, "bronze", SOURCE_NAME, "_manifests", f"{quarter}.json")


def landing_zip_path(lake_root: str, quarter: str) -> str:
    """Path where a quarter's raw downloaded ZIP is kept for rebuilds."""
    return os.path.join(lake_root, "landing", SOURCE_NAME, f"{quarter}.zip")
