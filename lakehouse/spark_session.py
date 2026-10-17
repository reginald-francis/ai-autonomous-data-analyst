"""Builds the SparkSession every lakehouse job and test uses.

One place for the setup so a job never configures Spark ad hoc: local mode, the
Delta Lake extension and catalog, and the sizing settings from lakehouse/config.py.
"""

from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession

from lakehouse import config


def build_spark_session(app_name: str = "lakehouse") -> SparkSession:
    """Return a Delta-enabled SparkSession running in local mode.

    The first call starts a Java process (the JVM) that does the actual work;
    later calls in the same Python process return that same session. Settings
    that size the JVM, such as spark.driver.memory, only take effect when it
    starts, so they cannot be changed by calling this again.
    """
    builder = (
        SparkSession.builder.appName(app_name)
        .master(config.get_spark_master())
        # Local mode: the driver also does all the work, so this is the whole
        # memory budget (Spark's own default of 1g is far too small).
        .config("spark.driver.memory", config.get_driver_memory())
        # Pieces of data after a join or regroup; Spark's default of 200 is too
        # many tiny tasks on one machine.
        .config("spark.sql.shuffle.partitions", str(config.get_shuffle_partitions()))
        # The web UI on http://localhost:4040 while a session runs; off in tests.
        .config("spark.ui.enabled", str(config.get_spark_ui_enabled()).lower())
        # Fix the session time zone so timestamps (the SEC's `accepted` column,
        # which decides which filing is newest) mean the same on every machine.
        .config("spark.sql.session.timeZone", "UTC")
        # The two settings that make Spark understand Delta tables.
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
    )
    # Adds the matching Delta jar (delta-spark_4.2_2.13:4.4.1) to Spark's package
    # list. Spark downloads it on first use and caches it (an "Ivy" cache), which
    # is why the first run is slow and why CI caches that folder.
    return configure_spark_with_delta_pip(builder).getOrCreate()
