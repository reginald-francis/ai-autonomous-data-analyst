"""Bronze-layer paths and manifest I/O for the SEC FSDS source.

Layout decided in docs/PHASE6_DESIGN.md section 5:
  <lake_root>/bronze/sec_fsds/<table>/quarter=<quarter>/part-0.parquet
  <lake_root>/bronze/sec_fsds/_manifests/<quarter>.json
  <lake_root>/bronze/sec_fsds/_staging/<quarter>/   (in-progress writes)
  <lake_root>/landing/sec_fsds/<quarter>.zip   (raw ZIP retention, item 9)

The underscore-prefixed folders sit beside the table folders, never inside
them, and Spark's partition discovery skips names starting with "_" anyway -
so a half-finished write in _staging can never be read as data.
"""

import json
import os

SOURCE_NAME = "sec_fsds"
TABLES = ("sub", "num", "tag", "pre")


def bronze_table_dir(lake_root: str, table: str, quarter: str) -> str:
    """Path to a table's partition folder for one quarter."""
    return os.path.join(lake_root, "bronze", SOURCE_NAME, table, f"quarter={quarter}")


def bronze_table_file(lake_root: str, table: str, quarter: str) -> str:
    """Path to the single Parquet file holding one table's data for one quarter."""
    return os.path.join(bronze_table_dir(lake_root, table, quarter), "part-0.parquet")


def bronze_staging_dir(lake_root: str, quarter: str) -> str:
    """Scratch folder where a quarter's Parquet files are written before being
    moved into place, so readers never see a partially written quarter.
    """
    return os.path.join(lake_root, "bronze", SOURCE_NAME, "_staging", quarter)


def manifest_path(lake_root: str, quarter: str) -> str:
    """Path to a quarter's manifest JSON file."""
    return os.path.join(lake_root, "bronze", SOURCE_NAME, "_manifests", f"{quarter}.json")


def landing_zip_path(lake_root: str, quarter: str) -> str:
    """Path where a quarter's raw downloaded ZIP is kept for rebuilds."""
    return os.path.join(lake_root, "landing", SOURCE_NAME, f"{quarter}.zip")


def read_manifest(lake_root: str, quarter: str) -> dict | None:
    """Return a quarter's existing manifest dict, or None if none exists yet."""
    path = manifest_path(lake_root, quarter)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_manifest(lake_root: str, quarter: str, manifest: dict) -> str:
    """Write a quarter's manifest atomically (.tmp, then os.replace).

    The bronze writer calls this last, after all four tables are in place:
    the manifest is the commit marker. If anything fails before it's written,
    the old manifest (or none) still describes the quarter, so the next run
    sees it as changed and rebuilds it.
    """
    path = manifest_path(lake_root, quarter)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)
    return path
