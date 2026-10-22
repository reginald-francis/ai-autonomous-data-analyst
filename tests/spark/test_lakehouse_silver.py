"""Tests for lakehouse/silver.py, built from a few hand-made rows (no bronze files).

Each test makes tiny `sub` and `num` DataFrames with explicit schemas matching the
real bronze types, calls one silver function and checks the result by eye-sized
numbers. Needs a real SparkSession, so it runs in WSL and the `spark` CI job.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from pyspark.sql.types import (
    DateType,
    DecimalType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from lakehouse import silver

pytestmark = pytest.mark.integration

# A cut-down `sub`: the columns silver uses, plus `name` and `quarter` to prove they
# are dropped (the real table has many more address and phone columns).
SUB_SCHEMA = StructType(
    [
        StructField("adsh", StringType()),
        StructField("cik", StringType()),
        StructField("name", StringType()),
        StructField("form", StringType()),
        StructField("fye", StringType()),
        StructField("filed", DateType()),
        StructField("accepted", TimestampType()),
        StructField("quarter", StringType()),
    ]
)

NUM_SCHEMA = StructType(
    [
        StructField("adsh", StringType()),
        StructField("tag", StringType()),
        StructField("version", StringType()),
        StructField("ddate", DateType()),
        StructField("qtrs", IntegerType()),
        StructField("uom", StringType()),
        StructField("value", DecimalType(28, 4)),
        StructField("quarter", StringType()),
    ]
)

FILED = date(2026, 4, 29)
ACCEPTED = datetime(2026, 4, 29, 16, 5, 0)


def sub_row(adsh, cik, form):
    return (adsh, cik, "SOME CO", form, "0630", FILED, ACCEPTED, "2026q2")


def num_rows(adsh):
    """Two figures for one filing: a quarter of revenue and a balance-sheet asset."""
    period_end = date(2026, 3, 31)
    return [
        (adsh, "Revenues", "us-gaap/2025", period_end, 1, "USD", Decimal("1000.0000"), "2026q2"),
        (adsh, "Assets", "us-gaap/2025", period_end, 0, "USD", Decimal("5000.0000"), "2026q2"),
    ]


@pytest.fixture
def sub(spark):
    """Six filings: three in-scope forms (A, B, C) and three that are not (D, E, F)."""
    rows = [
        sub_row("A", "100", "10-K"),
        sub_row("B", "200", "10-Q/A"),
        sub_row("C", "300", "20-F"),
        sub_row("D", "400", "6-K"),
        sub_row("E", "500", "11-K"),
        sub_row("F", "600", "S-1"),
    ]
    return spark.createDataFrame(rows, SUB_SCHEMA)


@pytest.fixture
def num(spark):
    """Two figures for each of the six filings: 12 rows."""
    rows = [row for adsh in "ABCDEF" for row in num_rows(adsh)]
    return spark.createDataFrame(rows, NUM_SCHEMA)


def test_in_scope_filings_keeps_only_the_forms_silver_uses(sub):
    result = silver.in_scope_filings(sub)
    assert {row["adsh"] for row in result.collect()} == {"A", "B", "C"}


def test_in_scope_filings_selects_only_the_needed_columns(sub):
    result = silver.in_scope_filings(sub)
    assert result.columns == ["adsh", "cik", "form", "fye", "filed", "accepted"]


def test_attach_filing_adds_the_company_and_filing_columns(sub, num):
    joined = silver.attach_filing(num, silver.in_scope_filings(sub))

    row = joined.filter("adsh = 'A' AND tag = 'Revenues'").collect()[0]
    assert row["cik"] == "100"
    assert row["form"] == "10-K"
    assert row["fye"] == "0630"
    assert row["filed"] == FILED
    assert row["value"] == Decimal("1000.0000")


def test_attach_filing_drops_figures_from_out_of_scope_filings(sub, num):
    joined = silver.attach_filing(num, silver.in_scope_filings(sub))

    assert {row["adsh"] for row in joined.collect()} == {"A", "B", "C"}
    assert joined.count() == 6


def test_attach_filing_never_adds_rows(sub, num):
    joined = silver.attach_filing(num, silver.in_scope_filings(sub))

    counts = {row["adsh"]: row["count"] for row in joined.groupBy("adsh").count().collect()}
    # adsh is unique in sub, so each in-scope filing keeps exactly its two figures.
    assert counts == {"A": 2, "B": 2, "C": 2}


def test_attach_filing_leaves_a_single_quarter_column(sub, num):
    joined = silver.attach_filing(num, silver.in_scope_filings(sub))
    assert joined.columns.count("quarter") == 1


def test_attach_filing_broadcasts_the_filings_side(sub, num, capsys):
    joined = silver.attach_filing(num, silver.in_scope_filings(sub))

    joined.explain()
    assert "BroadcastHashJoin" in capsys.readouterr().out
