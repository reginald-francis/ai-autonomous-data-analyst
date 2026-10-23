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
from pyspark.testing import assertDataFrameEqual

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
        StructField("segments", StringType()),
        StructField("coreg", StringType()),
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
        (adsh, "Revenues", "us-gaap/2025", period_end, 1, "USD", None, None, Decimal("1000.0000"), "2026q2"),
        (adsh, "Assets", "us-gaap/2025", period_end, 0, "USD", None, None, Decimal("5000.0000"), "2026q2"),
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


def fact_row(
    tag="Revenues",
    version="us-gaap/2025",
    qtrs=1,
    segments=None,
    coreg=None,
    value=Decimal("1.0000"),
):
    """One figure of filing A. Every field defaults to a keep-me value, so a test
    changes only the one thing it is about."""
    return ("A", tag, version, date(2026, 3, 31), qtrs, "USD", segments, coreg, value, "2026q2")


def filtered(spark, *rows):
    """Run whole_company_standard_facts on a DataFrame built from these rows."""
    return silver.whole_company_standard_facts(spark.createDataFrame(list(rows), NUM_SCHEMA))


def test_whole_company_standard_facts_keeps_a_us_gaap_figure_and_adds_taxonomy(spark):
    result = filtered(spark, fact_row()).collect()

    assert len(result) == 1
    assert result[0]["taxonomy"] == "us-gaap"


def test_whole_company_standard_facts_keeps_an_ifrs_figure(spark):
    result = filtered(spark, fact_row(version="ifrs/2025")).collect()

    assert [row["taxonomy"] for row in result] == ["ifrs"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"segments": "BusinessSegments=Cloud;"},  # a business-unit slice
        {"coreg": "SubsidiaryCo"},  # a co-registrant slice
        {"version": "0001628280-26-027688"},  # a company-invented tag
        {"version": "us-gaap-ebp/2026"},  # the employee-benefit-plan rulebook
        {"value": None},  # no value reported
    ],
)
def test_whole_company_standard_facts_drops_rows_that_should_not_reach_silver(spark, overrides):
    assert filtered(spark, fact_row(**overrides)).count() == 0


def test_whole_company_standard_facts_keeps_qtrs_0_to_4_only(spark):
    rows = [fact_row(qtrs=qtrs) for qtrs in (0, 1, 2, 3, 4, 5, 113)]

    result = filtered(spark, *rows)

    assert sorted(row["qtrs"] for row in result.collect()) == [0, 1, 2, 3, 4]


def test_filtering_before_or_after_the_join_gives_the_same_result(spark, sub, num):
    slice_row = fact_row(segments="BusinessSegments=Cloud;")
    mixed = num.unionByName(spark.createDataFrame([slice_row], NUM_SCHEMA))
    filings = silver.in_scope_filings(sub)

    filter_first = silver.attach_filing(silver.whole_company_standard_facts(mixed), filings)
    join_first = silver.whole_company_standard_facts(silver.attach_filing(mixed, filings))

    columns = sorted(filter_first.columns)
    assertDataFrameEqual(filter_first.select(columns), join_first.select(columns))
    # The in-scope whole-company figures only: A, B and C with two each.
    assert filter_first.count() == 6


def mapped(spark, *rows):
    """Filter these rows to whole-company standard figures, then map them to metrics
    using the real seed files."""
    return silver.map_to_metrics(
        filtered(spark, *rows), silver.read_concept_map(spark), silver.read_metrics(spark)
    )


def test_read_concept_map_has_the_columns_and_a_known_row(spark):
    concept_map = silver.read_concept_map(spark)

    assert concept_map.columns == ["metric", "taxonomy", "tag", "priority"]
    known = concept_map.filter(
        "metric = 'net_income' AND taxonomy = 'ifrs' "
        "AND tag = 'ProfitLossAttributableToOwnersOfParent'"
    ).collect()
    assert [row["priority"] for row in known] == [1]


def test_read_metrics_has_the_columns_and_the_revenue_rule(spark):
    metrics = silver.read_metrics(spark)

    assert metrics.columns == [
        "metric",
        "description",
        "statement",
        "period_type",
        "resolution_rule",
    ]
    revenue = metrics.filter("metric = 'revenue'").collect()[0]
    assert revenue["resolution_rule"] == "largest"
    assert revenue["period_type"] == "flow"


def test_map_to_metrics_names_the_metric_and_attaches_its_rules(spark):
    row = mapped(spark, fact_row(tag="Revenues")).collect()[0]

    assert row["metric"] == "revenue"
    assert row["priority"] == 2
    assert row["period_type"] == "flow"
    assert row["resolution_rule"] == "largest"


def test_map_to_metrics_drops_tags_that_are_not_in_the_concept_map(spark):
    assert mapped(spark, fact_row(tag="SomeCompanySpecificDetail")).count() == 0


def test_map_to_metrics_does_not_map_us_gaap_profit_loss(spark):
    # Decision D19: US net income is the parent-only NetIncomeLoss, so the including-
    # minority ProfitLoss is deliberately unmapped.
    assert mapped(spark, fact_row(tag="ProfitLoss", version="us-gaap/2025")).count() == 0


def test_map_to_metrics_matches_on_taxonomy_and_tag_together(spark):
    ifrs = mapped(spark, fact_row(tag="ProfitLoss", version="ifrs/2025")).collect()
    # The same tag name is net income's IFRS fallback, with priority 2 ...
    assert [(row["metric"], row["priority"]) for row in ifrs] == [("net_income", 2)]
    # ... and an IFRS-only tag name under us-gaap maps to nothing.
    assert mapped(spark, fact_row(tag="Revenue", version="us-gaap/2025")).count() == 0


def test_map_to_metrics_never_adds_rows(spark):
    rows = [
        fact_row(tag="Assets", version="us-gaap/2025", qtrs=0),
        fact_row(tag="Assets", version="ifrs/2025", qtrs=0),
    ]

    assert mapped(spark, *rows).count() == 2
