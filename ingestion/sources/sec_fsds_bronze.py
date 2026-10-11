"""Convert one downloaded SEC FSDS quarter ZIP into bronze Parquet + manifest.

Flow for write_bronze(quarter, zip_path, lake_root):
  1. Skip if already up to date: the manifest's sha256 matches the ZIP, its
     schema_version matches sec_fsds_schema.SCHEMA_VERSION, and all four
     Parquet files exist. A re-run on unchanged input does nothing.
  2. For each of sub/num/tag/pre: check the header against the schema, stream
     the TSV in batches (memory stays bounded however big num.txt is), apply
     the explicit types, and write Parquet into a _staging folder.
  3. Check each table's row count against an independent count of the
     source file's data lines.
  4. Only once all four tables succeeded: move each staged file into its
     final partition (os.replace, atomic per file), then write the manifest
     LAST - it's the commit marker. A crash anywhere before that leaves the
     old manifest in place, so the next run sees the quarter as changed and
     rebuilds it.

readme.htm (SEC documentation, not data) is skipped by design.
"""

import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from ingestion import storage
from ingestion.sources import sec_fsds, sec_fsds_schema

logger = logging.getLogger(__name__)

# Members we expect in every quarter's ZIP that are deliberately not ingested.
_KNOWN_NON_DATA_MEMBERS = {"readme.htm"}


class BronzeError(Exception):
    """Base class for failures converting a quarter to bronze."""


class MissingTableError(BronzeError):
    """A table file (e.g. num.txt) is missing from the ZIP."""


class SchemaMismatchError(BronzeError):
    """A file's header doesn't match the expected schema - the SEC changed shape."""


class ConversionError(BronzeError):
    """A value couldn't be converted to its declared type."""


class RowCountMismatchError(BronzeError):
    """Rows written to Parquet don't match the source file's data lines."""


@dataclass
class BronzeResult:
    status: str  # "written" or "unchanged"
    manifest: dict


def write_bronze(
    quarter: str,
    zip_path: str,
    lake_root: str,
    etag: str | None = None,
    last_modified: str | None = None,
    force: bool = False,
) -> BronzeResult:
    """Convert one quarter's ZIP into bronze Parquet, idempotently.

    etag/last_modified are the HEAD-request headers from
    sec_fsds.get_remote_metadata(), recorded in the manifest so the next
    run's needs_download() pre-check has something to compare against.
    force=True rebuilds even if the quarter looks up to date.
    """
    sec_fsds.parse_quarter(quarter)  # raises QuarterFormatError on a bad quarter string
    zip_sha256 = sec_fsds.sha256_of_file(zip_path)
    existing = storage.read_manifest(lake_root, quarter)

    if not force and _is_up_to_date(existing, zip_sha256, lake_root, quarter):
        logger.info(f"Bronze already up to date: {quarter}")
        return BronzeResult(status="unchanged", manifest=existing)

    staging_dir = storage.bronze_staging_dir(lake_root, quarter)
    shutil.rmtree(staging_dir, ignore_errors=True)  # leftovers from a crashed run
    os.makedirs(staging_dir)

    try:
        row_counts = {}
        with zipfile.ZipFile(zip_path) as zf:
            _check_members(zf, quarter)
            for table in storage.TABLES:
                staged_file = os.path.join(staging_dir, f"{table}.parquet")
                row_counts[table] = _convert_table(zf, table, staged_file)

        # Every table converted and checked - now move them into place.
        for table in storage.TABLES:
            final_file = storage.bronze_table_file(lake_root, table, quarter)
            os.makedirs(os.path.dirname(final_file), exist_ok=True)
            os.replace(os.path.join(staging_dir, f"{table}.parquet"), final_file)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    manifest = {
        "source": storage.SOURCE_NAME,
        "quarter": quarter,
        "source_url": sec_fsds.quarter_zip_url(quarter),
        "sha256": zip_sha256,
        "zip_size_bytes": os.path.getsize(zip_path),
        "etag": etag,
        "last_modified": last_modified,
        "schema_version": sec_fsds_schema.SCHEMA_VERSION,
        "row_counts": row_counts,
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    storage.write_manifest(lake_root, quarter, manifest)  # commit marker - always last
    logger.info(f"Bronze written: {quarter} | rows: {row_counts}")
    return BronzeResult(status="written", manifest=manifest)


def _is_up_to_date(existing: dict | None, zip_sha256: str, lake_root: str, quarter: str) -> bool:
    if existing is None:
        return False
    if existing.get("sha256") != zip_sha256:
        return False
    if existing.get("schema_version") != sec_fsds_schema.SCHEMA_VERSION:
        return False
    return all(
        os.path.exists(storage.bronze_table_file(lake_root, table, quarter))
        for table in storage.TABLES
    )


def _check_members(zf: zipfile.ZipFile, quarter: str) -> None:
    names = set(zf.namelist())
    missing = [f"{t}.txt" for t in storage.TABLES if f"{t}.txt" not in names]
    if missing:
        raise MissingTableError(f"{quarter}: ZIP is missing {missing}")

    expected = {f"{t}.txt" for t in storage.TABLES} | _KNOWN_NON_DATA_MEMBERS
    unexpected = sorted(names - expected)
    if unexpected:
        # Not fatal - only the four known tables are ingested - but worth
        # seeing, since a new file can mean the SEC changed the data set.
        logger.warning(f"{quarter}: ignoring unexpected ZIP members {unexpected}")


def _read_header(zf: zipfile.ZipFile, member: str) -> list[str]:
    with zf.open(member) as f:
        first_line = f.readline().decode("utf-8")
    # strip() guards against stray trailing whitespace on the header line.
    return [name.strip() for name in first_line.rstrip("\r\n").split("\t")]


def _validate_header(table: str, header: list[str]) -> None:
    fields = sec_fsds_schema.fields_for(table)
    expected = [f.name for f in fields]
    required = [f.name for f in fields if not f.optional_in_source]

    duplicates = sorted({c for c in header if header.count(c) > 1})
    unexpected = [c for c in header if c not in expected]
    missing = [c for c in required if c not in header]
    if duplicates or unexpected or missing:
        raise SchemaMismatchError(
            f"{table}.txt header doesn't match the schema - "
            f"unexpected: {unexpected}, missing: {missing}, duplicated: {duplicates}. "
            "If the SEC changed the data set, update sec_fsds_schema.py and bump SCHEMA_VERSION."
        )


def _count_data_rows(zf: zipfile.ZipFile, member: str) -> int:
    """Count the source file's non-empty lines minus the header - an
    independent check on the parser, which also skips empty lines.
    """
    count = 0
    with zf.open(member) as f:
        for line in f:
            if line.strip(b"\r\n"):
                count += 1
    return max(count - 1, 0)


def _convert_table(zf: zipfile.ZipFile, table: str, staged_file: str) -> int:
    member = f"{table}.txt"
    header = _read_header(zf, member)
    _validate_header(table, header)
    fields = sec_fsds_schema.fields_for(table)
    schema = sec_fsds_schema.arrow_schema(table)

    read_options = pacsv.ReadOptions(column_names=header, skip_rows=1)
    # The SEC's files are tab-delimited and unquoted (see the readme's "File
    # Formats" section), so quote characters are ordinary text, not quoting.
    parse_options = pacsv.ParseOptions(delimiter="\t", quote_char=False)
    convert_options = pacsv.ConvertOptions(
        # Read everything as text first; _convert_batch applies the real types.
        column_types={name: pa.string() for name in header},
        strings_can_be_null=True,
        # Only an empty field is NULL. pyarrow's defaults would also null out
        # "NA", "NULL", "nan" etc. - and "NA" is Namibia's ISO country code.
        null_values=[""],
    )

    rows_written = 0
    try:
        with zf.open(member) as source, pq.ParquetWriter(staged_file, schema, compression="zstd") as writer:
            reader = pacsv.open_csv(source, read_options, parse_options, convert_options)
            for batch in reader:
                writer.write_batch(_convert_batch(table, batch, fields, schema))
                rows_written += batch.num_rows
    except pa.ArrowInvalid as e:
        # Raised while parsing the file itself (wrong field count on a row,
        # invalid UTF-8) - _convert_column's own errors are ConversionErrors.
        raise ConversionError(f"{table}.txt: couldn't parse the file: {e}") from e

    expected_rows = _count_data_rows(zf, member)
    if rows_written != expected_rows:
        raise RowCountMismatchError(
            f"{table}.txt: wrote {rows_written} rows but the file has {expected_rows} data lines"
        )
    return rows_written


def _convert_batch(table: str, batch: pa.RecordBatch, fields, schema: pa.Schema) -> pa.RecordBatch:
    arrays = []
    for field in fields:
        if field.name in batch.schema.names:
            raw = batch.column(batch.schema.get_field_index(field.name))
            arrays.append(_convert_column(table, field, raw))
        else:
            # Only reachable for optional_in_source fields - _validate_header
            # already rejected any other missing column.
            arrays.append(pa.nulls(batch.num_rows, type=field.arrow_type))
    return pa.RecordBatch.from_arrays(arrays, schema=schema)


def _convert_column(table: str, field, raw: pa.Array) -> pa.Array:
    target = field.arrow_type
    if pa.types.is_string(target):
        return raw  # text is kept exactly as published - no trimming
    try:
        # Non-text values can't legitimately contain surrounding whitespace,
        # so trimming before conversion only removes formatting noise.
        trimmed = pc.utf8_trim_whitespace(raw)
        if pa.types.is_date32(target):
            return _parse_yyyymmdd(trimmed)
        return pc.cast(trimmed, target)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
        raise ConversionError(f"{table}.{field.name}: can't convert to {target}: {e}") from e


def _parse_yyyymmdd(values: pa.Array) -> pa.Array:
    """Turn "20250331" into a date32. pyarrow can't parse this compact form
    directly, so build "2025-03-31" first - a standard ISO date it can.
    """
    bad_length = pc.and_(pc.is_valid(values), pc.not_equal(pc.utf8_length(values), 8))
    if pc.any(bad_length).as_py():
        raise pa.ArrowInvalid("expected 8-character yyyymmdd dates")
    iso = pc.binary_join_element_wise(
        pc.utf8_slice_codeunits(values, 0, 4),
        pc.utf8_slice_codeunits(values, 4, 6),
        pc.utf8_slice_codeunits(values, 6, 8),
        "-",
    )
    return pc.cast(pc.cast(iso, pa.timestamp("s")), pa.date32())
