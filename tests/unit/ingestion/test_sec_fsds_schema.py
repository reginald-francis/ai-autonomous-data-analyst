"""Tests for ingestion.sources.sec_fsds_schema.

The expected headers below are copied from a real 2026q2 ZIP's header rows
(confirmed 2026-10-08), deliberately typed out here rather than derived from
the schema module - otherwise a mistake in the schema would just be repeated
in the test and pass.
"""

import pyarrow as pa
import pytest

from ingestion.sources import sec_fsds_schema as schema

REAL_HEADERS = {
    "sub": [
        "adsh", "cik", "name", "sic", "countryba", "stprba", "cityba", "zipba",
        "bas1", "bas2", "baph", "countryma", "stprma", "cityma", "zipma", "mas1",
        "mas2", "countryinc", "stprinc", "ein", "former", "changed", "afs", "wksi",
        "fye", "form", "period", "fy", "fp", "filed", "accepted", "prevrpt",
        "detail", "instance", "nciks", "aciks",
    ],
    "num": [
        "adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg",
        "value", "footnote",
    ],
    "tag": [
        "tag", "version", "custom", "abstract", "datatype", "iord", "crdr",
        "tlabel", "doc",
    ],
    "pre": [
        "adsh", "report", "line", "stmt", "inpth", "rfile", "tag", "version",
        "plabel", "negating",
    ],
}


@pytest.mark.parametrize("table", ["sub", "num", "tag", "pre"])
def test_field_names_match_the_real_file_headers(table):
    assert [f.name for f in schema.fields_for(table)] == REAL_HEADERS[table]


def test_key_columns_match_the_sec_spec():
    assert schema.key_columns("sub") == ("adsh",)
    assert schema.key_columns("tag") == ("tag", "version")
    assert schema.key_columns("num") == (
        "adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg",
    )
    assert schema.key_columns("pre") == ("adsh", "report", "line")


def _type_of(table, column):
    return schema.arrow_schema(table).field(column).type


def test_value_is_exact_decimal_not_float():
    assert _type_of("num", "value") == pa.decimal128(28, 4)


@pytest.mark.parametrize(
    "table, column",
    [("sub", "fye"), ("sub", "cik"), ("sub", "sic"), ("sub", "ein"), ("sub", "changed")],
)
def test_leading_zero_risk_and_non_date_fields_stay_strings(table, column):
    assert _type_of(table, column) == pa.string()


@pytest.mark.parametrize(
    "table, column",
    [
        ("sub", "wksi"), ("sub", "prevrpt"), ("sub", "detail"), ("tag", "custom"),
        ("tag", "abstract"), ("pre", "inpth"), ("pre", "negating"),
    ],
)
def test_sec_boolean_flags_are_bool(table, column):
    assert _type_of(table, column) == pa.bool_()


@pytest.mark.parametrize("table, column", [("sub", "period"), ("sub", "filed"), ("num", "ddate")])
def test_yyyymmdd_fields_are_dates(table, column):
    assert _type_of(table, column) == pa.date32()


def test_accepted_is_a_timestamp():
    assert _type_of("sub", "accepted") == pa.timestamp("ms")


@pytest.mark.parametrize("table", ["sub", "num", "tag", "pre"])
def test_every_bronze_field_is_nullable(table):
    # Nullability is recorded in FieldSpec for Phase 10, not enforced in bronze.
    assert all(field.nullable for field in schema.arrow_schema(table))


def test_sec_nullability_is_recorded_for_phase_10():
    by_name = {f.name: f for f in schema.fields_for("sub")}
    assert by_name["adsh"].nullable is False
    assert by_name["sic"].nullable is True


def test_only_segments_may_be_absent_from_the_source():
    optional = [
        (table, f.name)
        for table in schema.TABLE_FIELDS
        for f in schema.fields_for(table)
        if f.optional_in_source
    ]
    assert optional == [("num", "segments")]


def test_schema_version_is_a_positive_int():
    assert isinstance(schema.SCHEMA_VERSION, int) and schema.SCHEMA_VERSION >= 1
