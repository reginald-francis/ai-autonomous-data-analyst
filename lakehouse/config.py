"""Lakehouse configuration: where the lake is and how Spark is set up.

Same idea as ingestion/config.py (one small function per setting, an environment
variable overriding a default), but a separate copy on purpose: lakehouse/ imports
nothing from ingestion/ or src/ (docs/PHASE7_DESIGN.md D12), and the two are
connected only through the lake folder on disk.

Unlike ingestion/config.py this module does not call load_dotenv(): nothing here
is a secret, and python-dotenv is not in requirements-spark.txt, so a future
Spark-only image would not have it. Set LAKEHOUSE_* variables in the shell if a
default needs changing.
"""

import os

# The bronze layout is ingestion's output contract (docs/PHASE6_DESIGN.md section
# 5): <lake_root>/bronze/sec_fsds/<table>/quarter=<q>/part-0.parquet. The name is
# repeated here rather than imported from ingestion/storage.py for the reason above.
SOURCE_NAME = "sec_fsds"


def get_lake_root() -> str:
    """Root folder of the data lake: bronze (written by ingestion) plus the
    silver and gold layers this package writes.

    Local disk only for now; a gs:// root needs the write-locally-then-upload
    step tracked as PHASES.md risk #28.
    """
    return os.environ.get("LAKEHOUSE_LAKE_ROOT", "data/lake")


def bronze_path(table: str) -> str:
    """Folder holding every quarter of one bronze table (sub, num, tag or pre).

    Deliberately the table folder, not one quarter's file: when Spark reads the
    parent folder it turns the quarter=<q> subfolder names into a real `quarter`
    column (Hive-style partitioning), which reading a single file does not give.
    """
    return os.path.join(get_lake_root(), "bronze", SOURCE_NAME, table)


def silver_path(table: str) -> str:
    """Folder of one silver Delta table, e.g. financials or company."""
    return os.path.join(get_lake_root(), "silver", table)


def gold_path(table: str) -> str:
    """Folder of one gold Delta table, e.g. fct_financials."""
    return os.path.join(get_lake_root(), "gold", table)


def get_spark_master() -> str:
    """Where Spark runs its work. local[*] means this one machine, one worker
    thread per visible core (16 on the personal laptop's WSL). Tests override it
    with something small such as local[2].
    """
    return os.environ.get("LAKEHOUSE_SPARK_MASTER", "local[*]")


def get_shuffle_partitions() -> int:
    """How many pieces Spark splits data into after a shuffle (a join or a
    regroup). Spark's own default is 200, which on one machine means 200 tiny
    tasks; 16 is about one per core. A starting value to tune against the real
    backfill, not a measured optimum.
    """
    return int(os.environ.get("LAKEHOUSE_SHUFFLE_PARTITIONS", "16"))


def get_driver_memory() -> str:
    """Memory for Spark's driver. In local mode the driver also does all the
    work, so this is the whole memory budget. Spark's own default is 1g, far too
    little for regrouping the full history. WSL currently sees about 7.4 GiB, so
    4g leaves room for the OS; revisit after raising WSL's limit with .wslconfig
    and before the full backfill (a starting guess, not measured).
    """
    return os.environ.get("LAKEHOUSE_DRIVER_MEMORY", "4g")


def get_spark_ui_enabled() -> bool:
    """Whether to serve Spark's web UI (http://localhost:4040 while a session is
    running). On by default because it is the best way to learn what Spark is
    doing; tests and CI turn it off.
    """
    value = os.environ.get("LAKEHOUSE_SPARK_UI", "true")
    return value.strip().lower() in ("1", "true", "yes")
