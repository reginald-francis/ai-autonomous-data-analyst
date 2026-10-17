"""Tests for lakehouse/spark_session.py using a real local SparkSession.

These start Spark (a Java process), so they only run where PySpark and Java are
installed: WSL on the personal laptop and the `spark` CI job. They use tiny
in-memory data and a temporary lake folder, never the real data/lake.
"""

import os

import pytest
from pyspark.testing import assertDataFrameEqual

from lakehouse import config

pytestmark = pytest.mark.integration


def test_session_uses_the_settings_the_fixture_asked_for(spark):
    # The `spark` fixture in conftest.py sets these through LAKEHOUSE_* variables.
    assert spark.sparkContext.master == "local[2]"
    assert spark.conf.get("spark.sql.shuffle.partitions") == "4"
    assert spark.conf.get("spark.ui.enabled") == "false"
    assert spark.conf.get("spark.sql.session.timeZone") == "UTC"


def test_delta_extension_and_catalog_are_configured(spark):
    assert "DeltaSparkSessionExtension" in spark.conf.get("spark.sql.extensions")
    assert "DeltaCatalog" in spark.conf.get("spark.sql.catalog.spark_catalog")


def test_delta_table_round_trip_writes_a_transaction_log(spark, lake_root):
    path = config.silver_path("round_trip")
    rows = [(1, "alpha"), (2, "beta")]
    df = spark.createDataFrame(rows, ["id", "label"])

    df.write.format("delta").save(path)
    result = spark.read.format("delta").load(path)

    assertDataFrameEqual(result, df)
    # A Delta table is Parquet files plus a _delta_log folder of JSON commits.
    assert os.path.isdir(os.path.join(path, "_delta_log"))


def test_reading_a_table_folder_turns_quarter_folders_into_a_column(spark, lake_root):
    path = config.bronze_path("sub")
    rows = [("0001", "2026q2"), ("0002", "2026q3")]
    df = spark.createDataFrame(rows, ["adsh", "quarter"])

    # partitionBy writes quarter=<value> folders and leaves the column out of the files.
    df.write.partitionBy("quarter").parquet(path)
    result = spark.read.parquet(path)

    assert "quarter" in result.columns
    assertDataFrameEqual(result, df)
