"""Tests for lakehouse/config.py: defaults, environment overrides and path helpers.

No Spark is started here. They sit in tests/spark/ so the Spark CI job (which
measures coverage of lakehouse/) runs them, and tests/spark/conftest.py skips
this folder on a machine without PySpark.
"""

import os

import pytest

from lakehouse import config

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Remove every LAKEHOUSE_* variable so each test starts from the defaults,
    whatever the machine running it has set."""
    for name in (
        "LAKEHOUSE_LAKE_ROOT",
        "LAKEHOUSE_SPARK_MASTER",
        "LAKEHOUSE_SHUFFLE_PARTITIONS",
        "LAKEHOUSE_DRIVER_MEMORY",
        "LAKEHOUSE_SPARK_UI",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    assert config.get_lake_root() == "data/lake"
    assert config.get_spark_master() == "local[*]"
    assert config.get_shuffle_partitions() == 16
    assert config.get_driver_memory() == "4g"
    assert config.get_spark_ui_enabled() is True


def test_environment_overrides_defaults(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_LAKE_ROOT", "/somewhere/else")
    monkeypatch.setenv("LAKEHOUSE_SPARK_MASTER", "local[2]")
    monkeypatch.setenv("LAKEHOUSE_SHUFFLE_PARTITIONS", "8")
    monkeypatch.setenv("LAKEHOUSE_DRIVER_MEMORY", "2g")

    assert config.get_lake_root() == "/somewhere/else"
    assert config.get_spark_master() == "local[2]"
    assert config.get_shuffle_partitions() == 8
    assert config.get_driver_memory() == "2g"


@pytest.mark.parametrize("value", ["true", "TRUE", " 1 ", "yes", "Yes"])
def test_spark_ui_truthy_values(monkeypatch, value):
    monkeypatch.setenv("LAKEHOUSE_SPARK_UI", value)
    assert config.get_spark_ui_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "off"])
def test_spark_ui_other_values_are_false(monkeypatch, value):
    monkeypatch.setenv("LAKEHOUSE_SPARK_UI", value)
    assert config.get_spark_ui_enabled() is False


def test_bronze_path_is_the_table_folder_not_a_quarter_file(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_LAKE_ROOT", "lake")
    # The table folder, so Spark reads quarter=<q> subfolder names as a column.
    assert config.bronze_path("num") == os.path.join("lake", "bronze", "sec_fsds", "num")


def test_silver_and_gold_paths_follow_the_lake_root(monkeypatch):
    monkeypatch.setenv("LAKEHOUSE_LAKE_ROOT", "lake")
    assert config.silver_path("financials") == os.path.join("lake", "silver", "financials")
    assert config.gold_path("fct_financials") == os.path.join("lake", "gold", "fct_financials")


def test_invalid_shuffle_partitions_fails_loudly(monkeypatch):
    # A typo in an operator-set variable should stop the run, not be ignored.
    monkeypatch.setenv("LAKEHOUSE_SHUFFLE_PARTITIONS", "sixteen")
    with pytest.raises(ValueError):
        config.get_shuffle_partitions()
