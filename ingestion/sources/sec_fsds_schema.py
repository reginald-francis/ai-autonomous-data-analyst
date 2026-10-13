"""Bronze schemas for the SEC Financial Statement Data Sets — one per table.

Transcribed from the SEC's own field definitions (docs/sec_fsds_readme_2026q2.htm,
Figures 2-5) and confirmed against a real 2026q2 ZIP's header rows. Each field
keeps the SEC's published metadata alongside its Arrow type, so this module is
the single source of truth for: Step 5's bronze reader (types), Phase 7's MERGE
(keys), and Phase 10's quality checks (nullability, max size).

Type mapping from the SEC's Format column (see docs/PHASE6_DESIGN.md section 5):
  ALPHANUMERIC            -> string
  NUMERIC                 -> int32, except identifier/code fields (cik, sic, ein),
                             kept as string so a leading zero in the source text
                             can never be silently dropped - revisit after Step 6
                             checks real values
  NUMERIC(28,4)           -> decimal128(28, 4), exact - no float rounding
  DATE (yyyymmdd)         -> date32
  DATETIME                -> timestamp(ms)
  YEAR (yyyy)             -> int32
  BOOLEAN (1/0)           -> bool

Every Arrow field is nullable: bronze lands data exactly as published, and the
SEC's own "May be NULL" rule is recorded here (FieldSpec.nullable) for Phase 10
to enforce, rather than failing a whole quarter at ingestion time.
"""

from dataclasses import dataclass

import pyarrow as pa

# Bump whenever any arrow_type below changes. Every manifest records the
# version its quarter was built with; a mismatch makes the bronze writer
# rebuild that quarter from its saved landing ZIP - no re-download needed.
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FieldSpec:
    """One column: its bronze Arrow type, plus the SEC's published metadata."""

    name: str
    arrow_type: pa.DataType
    sec_format: str
    max_size: int | None  # None where the SEC lists no maximum (tag.doc)
    nullable: bool  # the SEC's "May be NULL" column - enforced in Phase 10, not here
    key: bool  # part of the SEC's documented unique key for the table
    optional_in_source: bool = False  # may be absent from the file (filled with nulls)


_S = pa.string()
_I32 = pa.int32()
_BOOL = pa.bool_()
_DATE = pa.date32()
_TS = pa.timestamp("ms")
_VALUE = pa.decimal128(28, 4)

SUB_FIELDS = (
    FieldSpec("adsh", _S, "ALPHANUMERIC (nnnnnnnnnn-nn-nnnnnn)", 20, False, True),
    FieldSpec("cik", _S, "NUMERIC", 10, False, False),
    FieldSpec("name", _S, "ALPHANUMERIC", 150, False, False),
    FieldSpec("sic", _S, "NUMERIC", 4, True, False),
    FieldSpec("countryba", _S, "ALPHANUMERIC", 2, True, False),
    FieldSpec("stprba", _S, "ALPHANUMERIC", 2, True, False),
    FieldSpec("cityba", _S, "ALPHANUMERIC", 30, True, False),
    FieldSpec("zipba", _S, "ALPHANUMERIC", 10, True, False),
    FieldSpec("bas1", _S, "ALPHANUMERIC", 40, True, False),
    FieldSpec("bas2", _S, "ALPHANUMERIC", 40, True, False),
    FieldSpec("baph", _S, "ALPHANUMERIC", 20, True, False),
    FieldSpec("countryma", _S, "ALPHANUMERIC", 2, True, False),
    FieldSpec("stprma", _S, "ALPHANUMERIC", 2, True, False),
    FieldSpec("cityma", _S, "ALPHANUMERIC", 30, True, False),
    FieldSpec("zipma", _S, "ALPHANUMERIC", 10, True, False),
    FieldSpec("mas1", _S, "ALPHANUMERIC", 40, True, False),
    FieldSpec("mas2", _S, "ALPHANUMERIC", 40, True, False),
    FieldSpec("countryinc", _S, "ALPHANUMERIC", 3, True, False),
    FieldSpec("stprinc", _S, "ALPHANUMERIC", 2, True, False),
    FieldSpec("ein", _S, "NUMERIC", 10, True, False),
    FieldSpec("former", _S, "ALPHANUMERIC", 150, True, False),
    # Documented as ALPHANUMERIC, unlike period/filed (DATE) - kept a string so
    # a value that isn't a valid date can't fail ingestion; silver can parse it.
    FieldSpec("changed", _S, "ALPHANUMERIC", 8, True, False),
    FieldSpec("afs", _S, "ALPHANUMERIC", 5, True, False),
    FieldSpec("wksi", _BOOL, "BOOLEAN (1 if true and 0 if false)", 1, False, False),
    # Month+day only (e.g. "0630") - an integer would turn "0630" into 630.
    FieldSpec("fye", _S, "ALPHANUMERIC (mmdd)", 4, True, False),
    FieldSpec("form", _S, "ALPHANUMERIC", 10, False, False),
    FieldSpec("period", _DATE, "DATE (yyyymmdd)", 8, False, False),
    FieldSpec("fy", _I32, "YEAR (yyyy)", 4, True, False),
    FieldSpec("fp", _S, "ALPHANUMERIC (FY, Q1, Q2, Q3, Q4)", 2, True, False),
    FieldSpec("filed", _DATE, "DATE (yyyymmdd)", 8, False, False),
    FieldSpec("accepted", _TS, "DATETIME (yyyy-mm-dd hh:mm:ss)", 19, False, False),
    FieldSpec("prevrpt", _BOOL, "BOOLEAN (1 if true and 0 if false)", 1, False, False),
    FieldSpec("detail", _BOOL, "BOOLEAN (1 if true and 0 if false)", 1, False, False),
    FieldSpec("instance", _S, "ALPHANUMERIC (e.g. abcd-yyyymmdd.xml)", 40, False, False),
    FieldSpec("nciks", _I32, "NUMERIC", 4, False, False),
    FieldSpec("aciks", _S, "ALPHANUMERIC (space delimited)", 120, True, False),
)

TAG_FIELDS = (
    FieldSpec("tag", _S, "ALPHANUMERIC", 256, False, True),
    FieldSpec("version", _S, "ALPHANUMERIC", 20, False, True),
    FieldSpec("custom", _BOOL, "BOOLEAN (1 if true and 0 if false)", 1, False, False),
    FieldSpec("abstract", _BOOL, "BOOLEAN (1 if true and 0 if false)", 1, False, False),
    FieldSpec("datatype", _S, "ALPHANUMERIC", 20, True, False),
    FieldSpec("iord", _S, "ALPHANUMERIC", 1, False, False),
    FieldSpec("crdr", _S, "ALPHANUMERIC (C or D)", 1, True, False),
    FieldSpec("tlabel", _S, "ALPHANUMERIC", 512, True, False),
    FieldSpec("doc", _S, "ALPHANUMERIC", None, True, False),
)

NUM_FIELDS = (
    FieldSpec("adsh", _S, "ALPHANUMERIC", 20, False, True),
    FieldSpec("tag", _S, "ALPHANUMERIC", 256, False, True),
    FieldSpec("version", _S, "ALPHANUMERIC", 20, False, True),
    FieldSpec("ddate", _DATE, "DATE (yyyymmdd)", 8, False, True),
    FieldSpec("qtrs", _I32, "NUMERIC", 8, False, True),
    FieldSpec("uom", _S, "ALPHANUMERIC", 20, False, True),
    # Added in the SEC's Dec 2024 republication. Part of the key but nullable -
    # Phase 7's MERGE needs null-safe matching on segments and coreg.
    FieldSpec("segments", _S, "ALPHANUMERIC", 1024, True, True, optional_in_source=True),
    FieldSpec("coreg", _S, "ALPHANUMERIC", 256, True, True),
    FieldSpec("value", _VALUE, "NUMERIC(28,4)", 16, True, False),
    FieldSpec("footnote", _S, "ALPHANUMERIC", 512, True, False),
)

PRE_FIELDS = (
    FieldSpec("adsh", _S, "ALPHANUMERIC", 20, False, True),
    FieldSpec("report", _I32, "NUMERIC", 6, False, True),
    FieldSpec("line", _I32, "NUMERIC", 6, False, True),
    FieldSpec("stmt", _S, "ALPHANUMERIC (BS, IS, CF, EQ, CI, SI, UN)", 2, False, False),
    FieldSpec("inpth", _BOOL, "BOOLEAN (1 if true and 0 if false)", 1, False, False),
    FieldSpec("rfile", _S, "ALPHANUMERIC", 1, False, False),
    FieldSpec("tag", _S, "ALPHANUMERIC", 256, False, False),
    FieldSpec("version", _S, "ALPHANUMERIC", 20, False, False),
    FieldSpec("plabel", _S, "ALPHANUMERIC", 512, False, False),
    FieldSpec("negating", _BOOL, "BOOLEAN (1 if true and 0 if false)", 1, False, False),
)

TABLE_FIELDS = {
    "sub": SUB_FIELDS,
    "num": NUM_FIELDS,
    "tag": TAG_FIELDS,
    "pre": PRE_FIELDS,
}


def fields_for(table: str) -> tuple[FieldSpec, ...]:
    """The FieldSpecs for one table, in the SEC's documented column order."""
    return TABLE_FIELDS[table]


def arrow_schema(table: str) -> pa.Schema:
    """The bronze Parquet schema for one table. Every field nullable - see the
    module docstring for why nullability is recorded here but not enforced.
    """
    return pa.schema([pa.field(f.name, f.arrow_type, nullable=True) for f in fields_for(table)])


def key_columns(table: str) -> tuple[str, ...]:
    """The SEC's documented unique key for one table."""
    return tuple(f.name for f in fields_for(table) if f.key)
