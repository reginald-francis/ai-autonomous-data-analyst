"""Silver layer: turns bronze `num` and `sub` into one clean row per company, metric and period.

Built as small functions that each take DataFrames and return a DataFrame, so every
step can be tested on a few hand-made rows without reading any files. Nothing runs
until a result is asked for (Spark is lazy). See docs/PHASE7_DESIGN.md D15-D22 for
the rules these functions implement.
"""

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
    """
    return sub.filter(F.col("form").isin(IN_SCOPE_FORMS)).select(
        "adsh", "cik", "form", "fye", "filed", "accepted"
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
