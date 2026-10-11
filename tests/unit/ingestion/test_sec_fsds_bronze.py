"""Tests for ingestion.sources.sec_fsds_bronze — ZIP to bronze Parquet.

Each test builds a tiny synthetic quarter ZIP (fake companies, never real SEC
data) from the Python rows below, so the tab-separated content is exact and
readable here rather than hidden in a binary fixture file.
"""

import datetime
import decimal
import logging
import os
import zipfile

import pyarrow.parquet as pq
import pytest

from ingestion import storage
from ingestion.sources import sec_fsds, sec_fsds_bronze, sec_fsds_schema

QUARTER = "2025q3"

SUB_ROWS = [
    {
        "adsh": "0000000001-25-000001", "cik": "1001", "name": "EXAMPLE CORP",
        "sic": "0100", "countryba": "NA", "cityba": "WINDHOEK", "ein": "012345678",
        "wksi": "0", "fye": "0630", "form": "10-K", "period": "20250630", "fy": "2025",
        "fp": "FY", "filed": "20250815", "accepted": "2025-08-15 16:30:00.0",
        "prevrpt": "0", "detail": "1", "instance": "example-20250630_htm.xml", "nciks": "1",
    },
    {
        "adsh": "0000000002-25-000002", "cik": "1002", "name": "SAMPLE HOLDINGS INC",
        "sic": "7372", "countryba": "US", "stprba": "CA", "wksi": "1", "fye": "1231",
        "form": "10-Q", "period": "20250630", "fy": "2025", "fp": "Q2", "filed": "20250805",
        "accepted": "2025-08-05 09:15:00.0", "prevrpt": "0", "detail": "1",
        "instance": "sample-20250630_htm.xml", "nciks": "1",
    },
]

NUM_ROWS = [
    {
        "adsh": "0000000001-25-000001", "tag": "Revenues", "version": "us-gaap/2025",
        "ddate": "20250630", "qtrs": "4", "uom": "USD", "value": "391035000000.0000",
    },
    {
        "adsh": "0000000001-25-000001", "tag": "NetIncomeLoss", "version": "us-gaap/2025",
        "ddate": "20250630", "qtrs": "4", "uom": "USD", "segments": "BusinessSegments=Services;",
        "value": "-1234.5678",
    },
    {
        "adsh": "0000000002-25-000002", "tag": "Assets", "version": "us-gaap/2025",
        "ddate": "20250630", "qtrs": "0", "uom": "USD", "value": "",
    },
]

TAG_ROWS = [
    {
        "tag": "Revenues", "version": "us-gaap/2025", "custom": "0", "abstract": "0",
        "datatype": "monetary", "iord": "D", "crdr": "C", "tlabel": "Revenues",
        "doc": 'Amount of revenue, "including" literal quote characters.',
    },
]

PRE_ROWS = [
    {
        "adsh": "0000000001-25-000001", "report": "2", "line": "3", "stmt": "IS",
        "inpth": "0", "rfile": "H", "tag": "Revenues", "version": "us-gaap/2025",
        "plabel": "Net sales", "negating": "0",
    },
]


def _tsv(table, rows, header=None):
    header = header or [f.name for f in sec_fsds_schema.fields_for(table)]
    lines = ["\t".join(header)] + ["\t".join(row.get(col, "") for col in header) for row in rows]
    return "\n".join(lines) + "\n"


def _default_members():
    return {
        "sub.txt": _tsv("sub", SUB_ROWS),
        "num.txt": _tsv("num", NUM_ROWS),
        "tag.txt": _tsv("tag", TAG_ROWS),
        "pre.txt": _tsv("pre", PRE_ROWS),
        "readme.htm": "<html>SEC documentation, not data</html>",
    }


def _write_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return str(path)


@pytest.fixture
def lake_root(tmp_path):
    return str(tmp_path / "lake")


@pytest.fixture
def zip_path(tmp_path):
    return _write_zip(tmp_path / f"{QUARTER}.zip", _default_members())


def _read(lake_root, table):
    return pq.read_table(storage.bronze_table_file(lake_root, table, QUARTER))


def _nothing_written(lake_root):
    return (
        storage.read_manifest(lake_root, QUARTER) is None
        and not any(
            os.path.exists(storage.bronze_table_file(lake_root, t, QUARTER)) for t in storage.TABLES
        )
        and not os.path.exists(storage.bronze_staging_dir(lake_root, QUARTER))
    )


class TestSuccessfulWrite:
    def test_writes_all_four_tables_and_the_manifest(self, zip_path, lake_root):
        result = sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root, etag='"abc"')

        assert result.status == "written"
        for table in storage.TABLES:
            assert os.path.exists(storage.bronze_table_file(lake_root, table, QUARTER))
        manifest = storage.read_manifest(lake_root, QUARTER)
        assert manifest == result.manifest
        assert manifest["row_counts"] == {"sub": 2, "num": 3, "tag": 1, "pre": 1}
        assert manifest["sha256"] == sec_fsds.sha256_of_file(zip_path)
        assert manifest["schema_version"] == sec_fsds_schema.SCHEMA_VERSION
        assert manifest["etag"] == '"abc"'
        assert not os.path.exists(storage.bronze_staging_dir(lake_root, QUARTER))

    def test_parquet_files_carry_the_declared_schemas(self, zip_path, lake_root):
        sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        for table in storage.TABLES:
            assert _read(lake_root, table).schema.equals(sec_fsds_schema.arrow_schema(table))

    def test_sub_values_are_typed_and_leading_zeros_survive(self, zip_path, lake_root):
        sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        first = _read(lake_root, "sub").to_pylist()[0]

        assert first["fye"] == "0630"
        assert first["sic"] == "0100"
        assert first["ein"] == "012345678"
        assert first["countryba"] == "NA"  # Namibia - not mistaken for a NULL
        assert first["stprba"] is None  # an empty field is NULL
        assert first["period"] == datetime.date(2025, 6, 30)
        assert first["accepted"] == datetime.datetime(2025, 8, 15, 16, 30)
        assert first["wksi"] is False
        assert first["detail"] is True
        assert first["fy"] == 2025

    def test_num_values_are_exact_decimals(self, zip_path, lake_root):
        sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        rows = _read(lake_root, "num").to_pylist()

        assert [r["value"] for r in rows] == [
            decimal.Decimal("391035000000.0000"), decimal.Decimal("-1234.5678"), None,
        ]
        assert rows[0]["ddate"] == datetime.date(2025, 6, 30)
        assert rows[0]["qtrs"] == 4
        assert rows[1]["segments"] == "BusinessSegments=Services;"

    def test_quote_characters_are_kept_as_plain_text(self, zip_path, lake_root):
        sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        doc = _read(lake_root, "tag").to_pylist()[0]["doc"]
        assert doc == 'Amount of revenue, "including" literal quote characters.'

    def test_unexpected_zip_member_is_ignored_with_a_warning(self, tmp_path, lake_root, caplog):
        members = _default_members()
        members["surprise.txt"] = "not part of the data set"
        path = _write_zip(tmp_path / "with_extra.zip", members)

        with caplog.at_level(logging.WARNING, logger="ingestion.sources.sec_fsds_bronze"):
            result = sec_fsds_bronze.write_bronze(QUARTER, path, lake_root)

        assert result.status == "written"
        assert "surprise.txt" in caplog.text
        assert "readme.htm" not in caplog.text  # expected, so skipped silently

    def test_missing_optional_segments_column_is_filled_with_nulls(self, tmp_path, lake_root):
        members = _default_members()
        header = [f.name for f in sec_fsds_schema.fields_for("num") if f.name != "segments"]
        members["num.txt"] = _tsv("num", NUM_ROWS, header=header)
        path = _write_zip(tmp_path / "no_segments.zip", members)

        sec_fsds_bronze.write_bronze(QUARTER, path, lake_root)

        assert _read(lake_root, "num").column("segments").to_pylist() == [None, None, None]


class TestIdempotency:
    def test_rerun_on_an_unchanged_zip_does_nothing(self, zip_path, lake_root):
        sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        sub_file = storage.bronze_table_file(lake_root, "sub", QUARTER)
        first_mtime = os.path.getmtime(sub_file)

        result = sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)

        assert result.status == "unchanged"
        assert os.path.getmtime(sub_file) == first_mtime

    def test_changed_zip_is_rebuilt(self, tmp_path, zip_path, lake_root):
        sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        members = _default_members()
        members["num.txt"] = _tsv("num", NUM_ROWS + [NUM_ROWS[0]])
        _write_zip(zip_path, members)  # same path, new content - like an SEC republication

        result = sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)

        assert result.status == "written"
        assert result.manifest["row_counts"]["num"] == 4

    def test_schema_version_bump_triggers_a_rebuild(self, zip_path, lake_root, monkeypatch):
        sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        monkeypatch.setattr(sec_fsds_schema, "SCHEMA_VERSION", sec_fsds_schema.SCHEMA_VERSION + 1)

        result = sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)

        assert result.status == "written"
        assert result.manifest["schema_version"] == sec_fsds_schema.SCHEMA_VERSION

    def test_missing_parquet_file_triggers_a_rebuild(self, zip_path, lake_root):
        sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        os.remove(storage.bronze_table_file(lake_root, "pre", QUARTER))

        result = sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)

        assert result.status == "written"
        assert os.path.exists(storage.bronze_table_file(lake_root, "pre", QUARTER))

    def test_force_rebuilds_an_up_to_date_quarter(self, zip_path, lake_root):
        sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        result = sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root, force=True)
        assert result.status == "written"


class TestFailures:
    def test_missing_table_raises_and_writes_nothing(self, tmp_path, lake_root):
        members = _default_members()
        del members["pre.txt"]
        path = _write_zip(tmp_path / "no_pre.zip", members)

        with pytest.raises(sec_fsds_bronze.MissingTableError):
            sec_fsds_bronze.write_bronze(QUARTER, path, lake_root)
        assert _nothing_written(lake_root)

    def test_unexpected_column_raises_schema_mismatch(self, tmp_path, lake_root):
        members = _default_members()
        header = [f.name for f in sec_fsds_schema.fields_for("tag")] + ["brand_new_column"]
        members["tag.txt"] = _tsv("tag", TAG_ROWS, header=header)
        path = _write_zip(tmp_path / "drifted.zip", members)

        with pytest.raises(sec_fsds_bronze.SchemaMismatchError, match="brand_new_column"):
            sec_fsds_bronze.write_bronze(QUARTER, path, lake_root)
        assert _nothing_written(lake_root)

    def test_bad_value_raises_and_leaves_nothing_behind(self, tmp_path, lake_root):
        members = _default_members()
        members["num.txt"] = _tsv("num", [dict(NUM_ROWS[0], qtrs="four")])
        path = _write_zip(tmp_path / "bad_value.zip", members)

        with pytest.raises(sec_fsds_bronze.ConversionError, match="num.qtrs"):
            sec_fsds_bronze.write_bronze(QUARTER, path, lake_root)
        assert _nothing_written(lake_root)

    def test_invalid_date_raises(self, tmp_path, lake_root):
        members = _default_members()
        members["sub.txt"] = _tsv("sub", [dict(SUB_ROWS[0], period="2025-06-30")])
        path = _write_zip(tmp_path / "bad_date.zip", members)

        with pytest.raises(sec_fsds_bronze.ConversionError, match="sub.period"):
            sec_fsds_bronze.write_bronze(QUARTER, path, lake_root)

    def test_failed_rebuild_keeps_the_previous_good_quarter(self, tmp_path, zip_path, lake_root):
        first = sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)
        members = _default_members()
        members["num.txt"] = _tsv("num", [dict(NUM_ROWS[0], value="not-a-number")])
        _write_zip(zip_path, members)

        with pytest.raises(sec_fsds_bronze.ConversionError):
            sec_fsds_bronze.write_bronze(QUARTER, zip_path, lake_root)

        # The manifest is only ever written last, so it still describes the
        # last good build - and that build's files are untouched.
        assert storage.read_manifest(lake_root, QUARTER) == first.manifest
        assert _read(lake_root, "num").num_rows == 3


def test_count_data_rows_skips_the_header_and_blank_lines(tmp_path):
    path = _write_zip(tmp_path / "count.zip", {"x.txt": "header\nrow1\n\nrow2\n"})
    with zipfile.ZipFile(path) as zf:
        assert sec_fsds_bronze._count_data_rows(zf, "x.txt") == 2
