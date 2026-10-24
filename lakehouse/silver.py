"""Silver layer: turns bronze `num` and `sub` into one clean row per company, metric and period.

Built as small functions that each take DataFrames and return a DataFrame, so every
step can be tested on a few hand-made rows without reading any files. Nothing runs
until a result is asked for (Spark is lazy). See docs/PHASE7_DESIGN.md D15-D22 for
the rules these functions implement.
"""

import csv
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from lakehouse import config

# The report types silver uses (docs/PHASE7_DESIGN.md D16): annual and quarterly
# reports of US and foreign filers, plus their amendments (/A), which carry
# restatements. Everything else (6-K, 11-K, S-1, ...) is out of version 1.
IN_SCOPE_FORMS = [
    "10-K",
    "10-K/A",
    "10-Q",
    "10-Q/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
]


# The small reference files that say which tag is which metric (D18). Read with
# Python and turned into DataFrames, not read by Spark: they are a handful of rows.
SEEDS_DIR = Path(__file__).parent / "seeds"
CONCEPT_MAP_SCHEMA = "metric string, taxonomy string, tag string, priority int"
METRICS_SCHEMA = (
    "metric string, description string, statement string, "
    "period_type string, resolution_rule string"
)

# The two rulebooks (taxonomies) the concept map covers. `us-gaap-ebp` (employee
# benefit plans) and company-invented tags are left out (D16).
TAXONOMIES = ["us-gaap", "ifrs"]


def read_bronze(spark: SparkSession, table: str) -> DataFrame:
    """Read one whole bronze table (sub, num, tag or pre), all quarters at once.

    Points at the table folder, so Spark adds a `quarter` column built from the
    quarter=<q> folder names (the filing window, not the fiscal quarter).
    """
    return spark.read.parquet(config.bronze_path(table))


def in_scope_filings(sub: DataFrame) -> DataFrame:
    """The filings silver uses, reduced to the columns later steps need.

    Keeps only the in-scope forms. Selecting just these columns keeps the table
    small enough to broadcast in the join below, and leaves out `quarter` so the
    joined result does not end up with two columns of that name.

    Also drops a filing with no cik, filed date or accepted time: they identify the
    company and decide which filing is newest, and a NULL would silently group with
    other NULLs. None exist in the real data today (measured 2026-10-24), so this is
    insurance against a future SEC change. `fye` is deliberately allowed to be NULL
    (91 of 40,794 filings): the figures are still valid, only the fiscal labels are
    missing.
    """
    return (
        sub.filter(F.col("form").isin(IN_SCOPE_FORMS))
        .filter(F.col("cik").isNotNull() & F.col("filed").isNotNull() & F.col("accepted").isNotNull())
        .select("adsh", "cik", "form", "fye", "filed", "accepted")
    )


def attach_filing(num: DataFrame, filings: DataFrame) -> DataFrame:
    """Attach each figure's filing (company, form, fiscal year end, filing dates).

    An inner join on adsh, so a figure whose filing is not in `filings` (an
    out-of-scope form) is dropped. adsh is unique in sub, so a figure matches at
    most one filing and the row count can never grow. The filings table is small
    next to num, so it is broadcast: copied to every worker instead of shuffling
    the large table across the machine.
    """
    return num.join(F.broadcast(filings), on="adsh", how="inner")


def whole_company_standard_facts(facts: DataFrame) -> DataFrame:
    """Keep the whole-company figures that use a standard tag, and add `taxonomy`.

    - segments and coreg both NULL: the company's own total, not a business unit,
      region or co-registrant slice (D16, section 3.3 of the design doc).
    - `taxonomy` is the text before the "/" in `version` ("us-gaap/2025" gives
      "us-gaap"). A custom tag's version is an accession number with no "/", so it
      never equals a known rulebook and is dropped, as is us-gaap-ebp.
    - qtrs 0 to 4: the 2 and 3 (six and nine months to date) are kept because later
      steps use them to derive quarters; the odd 5-to-113 tail is dropped.
    - value not NULL: the SEC allows NULL values (908,255 of 21.55M rows, 4.2%, in
      the six quarters measured 2026-10-24), and a NULL from a later filing must not
      win "latest filed" over a real value from an earlier one.
    - ddate and uom not NULL: they are part of the silver key, and a NULL would
      silently group unrelated figures together. None exist in the real data today;
      this is insurance against a future SEC change.
    """
    return (
        facts.filter(F.col("segments").isNull() & F.col("coreg").isNull())
        .withColumn("taxonomy", F.split(F.col("version"), "/").getItem(0))
        .filter(F.col("taxonomy").isin(TAXONOMIES))
        .filter(F.col("qtrs").between(0, 4))
        .filter(F.col("value").isNotNull())
        .filter(F.col("ddate").isNotNull() & F.col("uom").isNotNull())
    )


def _read_seed_rows(name: str) -> list[dict]:
    """The rows of one seed CSV in lakehouse/seeds/, as dicts keyed by column name."""
    with open(SEEDS_DIR / name, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_concept_map(spark: SparkSession) -> DataFrame:
    """The concept map as a DataFrame: which (taxonomy, tag) belongs to which metric."""
    rows = [
        (r["metric"], r["taxonomy"], r["tag"], int(r["priority"]))
        for r in _read_seed_rows("concept_map.csv")
    ]
    return spark.createDataFrame(rows, CONCEPT_MAP_SCHEMA)


def read_metrics(spark: SparkSession) -> DataFrame:
    """The metric definitions as a DataFrame: statement, period type, conflict rule."""
    rows = [
        (r["metric"], r["description"], r["statement"], r["period_type"], r["resolution_rule"])
        for r in _read_seed_rows("metrics.csv")
    ]
    return spark.createDataFrame(rows, METRICS_SCHEMA)


def map_to_metrics(facts: DataFrame, concept_map: DataFrame, metrics: DataFrame) -> DataFrame:
    """Give each figure its standard metric name, and attach that metric's rules.

    Two inner joins, both against tiny tables so both are broadcast:
    - facts to the concept map on (taxonomy, tag): adds `metric` and `priority`. A
      figure whose tag is not in the map is dropped - this is where thousands of SEC
      tags shrink to the eight metrics we track. (taxonomy, tag) is unique in the
      concept map (tests/spark/test_lakehouse_seeds.py), so rows cannot multiply.
    - then to the metric definitions on `metric`: adds `period_type` (flow or
      instant) and `resolution_rule` (largest or priority), used by later steps.
    `facts` must already have a `taxonomy` column (whole_company_standard_facts).
    """
    definitions = metrics.select("metric", "period_type", "resolution_rule")
    return facts.join(F.broadcast(concept_map), on=["taxonomy", "tag"], how="inner").join(
        F.broadcast(definitions), on="metric", how="inner"
    )


def keep_valid_period_shapes(facts: DataFrame) -> DataFrame:
    """Keep figures whose period length fits their metric's type.

    An `instant` metric (a balance-sheet amount) is a single date, so qtrs must be
    0; a `flow` metric (income or cash flow) covers a span, so qtrs must not be 0.
    Anything else is a mislabelled figure and is dropped rather than trusted.
    """
    is_instant = F.col("period_type") == "instant"
    return facts.filter(is_instant == (F.col("qtrs") == 0))


def add_periods(facts: DataFrame) -> DataFrame:
    """Turn ddate and qtrs into exact dates, plus fiscal and calendar labels (D21).

    - period_end: the SEC's ddate (renamed), already rounded to a month end.
    - period_start: the first day of the month 3 x qtrs - 1 months before the end
      month; NULL for a single date (qtrs 0). Truncating to a month start means the
      month-end behaviour of date arithmetic does not matter.
    - fiscal_quarter: months into the fiscal year (end month minus the fiscal-year-
      end month from `fye`, wrapped to 1-12) divided by 3, rounded up; NULL for an
      annual row (qtrs 4), which covers all four quarters.
    - fiscal_year: the calendar year in which the fiscal year ends - the end date's
      year if its month is on or before the fiscal-year-end month, otherwise the
      next year. The filer's own fy/fp are deliberately not used.
    - calendar_year / calendar_quarter: simply those of the end date. For a company
      whose fiscal quarters do not line up with calendar quarters this is an
      approximation, documented in docs/DATA_MODEL.md.
    `fye` is text like "0630"; its first two characters are the month. If `fye` is
    NULL, both fiscal labels are NULL (the calendar labels still work).
    """
    qtrs = F.col("qtrs")
    ddate = F.col("ddate")
    end_month = F.month(ddate)
    fye_month = F.substring(F.col("fye"), 1, 2).cast("int")
    wrapped = F.pmod(end_month - fye_month, F.lit(12))
    months_into_year = F.when(wrapped == 0, 12).otherwise(wrapped)

    return (
        facts.withColumn("period_start", F.when(qtrs > 0, F.trunc(F.add_months(ddate, -(3 * qtrs - 1)), "month")))
        .withColumn("fiscal_quarter", F.when(qtrs != 4, F.ceil(months_into_year / 3).cast("int")))
        .withColumn(
            "fiscal_year",
            # Only when fye is known: a NULL comparison counts as false in a CASE
            # WHEN, so without this guard a missing fye would silently give year + 1.
            F.when(
                fye_month.isNotNull(),
                F.when(end_month <= fye_month, F.year(ddate)).otherwise(F.year(ddate) + 1),
            ),
        )
        .withColumn("calendar_year", F.year(ddate))
        .withColumn("calendar_quarter", F.quarter(ddate))
        .withColumnRenamed("ddate", "period_end")
    )
