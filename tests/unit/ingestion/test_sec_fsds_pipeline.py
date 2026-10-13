"""Tests for ingestion.sources.sec_fsds_pipeline - the decisions about what a
quarter needs, not the downloading or converting themselves (those have their
own tests).

The network calls and write_bronze() are replaced with mocks, so each test can
state exactly what the SEC "said" and check which steps the pipeline chose to
run. Manifests and bronze files are real, on disk in tmp_path, because the
pipeline's decisions depend on what's actually there.
"""

import os
import types

import pytest
import requests

from ingestion import storage
from ingestion.sources import sec_fsds_bronze, sec_fsds_pipeline, sec_fsds_schema

QUARTER = "2026q2"
UA = "Test Agent test@example.com"
ROW_COUNTS = {"sub": 1, "num": 2, "tag": 1, "pre": 1}
FAKE_ZIP = "/fake/2026q2.zip"


@pytest.fixture
def lake_root(tmp_path):
    return str(tmp_path / "lake")


@pytest.fixture
def mocks(mocker):
    """Stand-ins for everything that touches the network or converts a ZIP.
    The defaults describe a quarter the SEC has changed; tests override as needed.
    """
    return types.SimpleNamespace(
        needs_download=mocker.patch("ingestion.sources.sec_fsds.needs_download", return_value=True),
        download=mocker.patch("ingestion.sources.sec_fsds.download_quarter", return_value=FAKE_ZIP),
        remote=mocker.patch(
            "ingestion.sources.sec_fsds.get_remote_metadata",
            return_value={"etag": '"new"', "last_modified": "Tue", "content_length": "1"},
        ),
        write=mocker.patch(
            "ingestion.sources.sec_fsds_bronze.write_bronze",
            return_value=sec_fsds_bronze.BronzeResult("written", {"row_counts": ROW_COUNTS}),
        ),
    )


def _seed(lake_root, *, schema_ok=True, files=True, landing_zip=False):
    """Put a previously ingested quarter on disk: a manifest, and optionally the
    four bronze files and the saved landing ZIP. Returns the manifest.
    """
    current = sec_fsds_schema.SCHEMA_VERSION
    manifest = {
        "sha256": "abc",
        "etag": '"old"',
        "last_modified": "Mon",
        "schema_version": current if schema_ok else current - 1,
        "row_counts": ROW_COUNTS,
    }
    storage.write_manifest(lake_root, QUARTER, manifest)
    paths = []
    if files:
        paths += [storage.bronze_table_file(lake_root, t, QUARTER) for t in storage.TABLES]
    if landing_zip:
        paths.append(storage.landing_zip_path(lake_root, QUARTER))
    for path in paths:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()
    return manifest


class TestIngestQuarter:
    def test_new_quarter_downloads_and_writes_bronze(self, lake_root, mocks):
        outcome = sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "ingested"
        assert outcome.row_counts == ROW_COUNTS
        mocks.needs_download.assert_not_called()  # no manifest, so nothing to compare against
        mocks.download.assert_called_once_with(QUARTER, lake_root, UA)
        mocks.write.assert_called_once_with(
            QUARTER, FAKE_ZIP, lake_root, etag='"new"', last_modified="Tue", force=False
        )

    def test_up_to_date_quarter_downloads_nothing(self, lake_root, mocks):
        _seed(lake_root)
        mocks.needs_download.return_value = False

        outcome = sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "unchanged"
        assert "nothing downloaded" in outcome.detail
        assert outcome.row_counts == ROW_COUNTS
        mocks.download.assert_not_called()
        mocks.write.assert_not_called()

    def test_changed_headers_download_again(self, lake_root, mocks):
        _seed(lake_root)  # needs_download defaults to True: the SEC's headers moved

        outcome = sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "ingested"
        mocks.needs_download.assert_called_once()
        mocks.download.assert_called_once()

    @pytest.mark.parametrize(
        "schema_ok, files",
        [(False, True), (True, False)],
        ids=["older-schema", "missing-files"],
    )
    def test_stale_bronze_rebuilds_from_the_saved_zip_without_downloading(
        self, lake_root, mocks, schema_ok, files
    ):
        _seed(lake_root, schema_ok=schema_ok, files=files, landing_zip=True)
        mocks.needs_download.return_value = False  # the SEC's file hasn't changed

        outcome = sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "ingested"
        mocks.download.assert_not_called()
        mocks.write.assert_called_once_with(
            QUARTER, storage.landing_zip_path(lake_root, QUARTER), lake_root,
            etag='"old"', last_modified="Mon", force=False,  # the manifest's own, not re-fetched
        )

    def test_stale_bronze_with_no_saved_zip_downloads_again(self, lake_root, mocks):
        _seed(lake_root, schema_ok=False, landing_zip=False)
        mocks.needs_download.return_value = False

        sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA)

        mocks.download.assert_called_once()

    def test_force_skips_every_shortcut(self, lake_root, mocks):
        _seed(lake_root, landing_zip=True)
        mocks.needs_download.return_value = False

        sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA, force=True)

        mocks.needs_download.assert_not_called()
        mocks.download.assert_called_once()
        assert mocks.write.call_args.kwargs["force"] is True


class TestHeaderRefresh:
    """When a download turns out identical, the manifest's headers are brought
    up to date - otherwise the cheap header check would flag the quarter forever.
    """

    def test_identical_download_with_new_headers_refreshes_the_manifest(self, lake_root, mocks):
        manifest = _seed(lake_root)
        mocks.write.return_value = sec_fsds_bronze.BronzeResult("unchanged", manifest)

        outcome = sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "unchanged"
        assert "identical" in outcome.detail
        saved = storage.read_manifest(lake_root, QUARTER)
        assert saved["etag"] == '"new"'
        assert saved["last_modified"] == "Tue"
        assert saved["sha256"] == "abc"  # everything else is preserved
        assert saved["row_counts"] == ROW_COUNTS

    def test_identical_download_with_same_headers_leaves_the_manifest_alone(
        self, lake_root, mocks, mocker
    ):
        manifest = _seed(lake_root)
        mocks.write.return_value = sec_fsds_bronze.BronzeResult("unchanged", manifest)
        mocks.remote.return_value = {"etag": '"old"', "last_modified": "Mon", "content_length": "1"}
        spy = mocker.spy(storage, "write_manifest")

        outcome = sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "unchanged"
        spy.assert_not_called()

    def test_failed_header_lookup_does_not_overwrite_good_headers(self, lake_root, mocks, mocker):
        manifest = _seed(lake_root)
        mocks.write.return_value = sec_fsds_bronze.BronzeResult("unchanged", manifest)
        mocks.remote.side_effect = requests.exceptions.ConnectionError("down")
        spy = mocker.spy(storage, "write_manifest")

        sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA)

        spy.assert_not_called()
        assert storage.read_manifest(lake_root, QUARTER)["etag"] == '"old"'

    def test_failed_header_lookup_on_a_new_quarter_records_unknown_headers(self, lake_root, mocks):
        mocks.remote.side_effect = requests.exceptions.ConnectionError("down")

        sec_fsds_pipeline.ingest_quarter(QUARTER, lake_root, UA)

        mocks.write.assert_called_once_with(
            QUARTER, FAKE_ZIP, lake_root, etag=None, last_modified=None, force=False
        )


class TestCheckQuarter:
    def test_never_ingested_quarter_would_be_ingested(self, lake_root, mocks):
        outcome = sec_fsds_pipeline.check_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "would_ingest"
        assert "never ingested" in outcome.detail
        mocks.download.assert_not_called()

    def test_changed_headers_would_be_ingested(self, lake_root, mocks):
        _seed(lake_root)  # needs_download defaults to True

        outcome = sec_fsds_pipeline.check_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "would_ingest"
        assert "headers" in outcome.detail
        mocks.download.assert_not_called()

    def test_incomplete_bronze_would_be_ingested(self, lake_root, mocks):
        _seed(lake_root, files=False)
        mocks.needs_download.return_value = False

        outcome = sec_fsds_pipeline.check_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "would_ingest"
        assert "incomplete" in outcome.detail

    def test_up_to_date_quarter(self, lake_root, mocks):
        _seed(lake_root)
        mocks.needs_download.return_value = False

        outcome = sec_fsds_pipeline.check_quarter(QUARTER, lake_root, UA)

        assert outcome.status == "up_to_date"
        assert outcome.row_counts == ROW_COUNTS
        mocks.download.assert_not_called()
        mocks.write.assert_not_called()


class TestRunIngest:
    @pytest.mark.parametrize(
        "error",
        [
            requests.HTTPError("404"),
            requests.exceptions.ConnectionError("down"),
            sec_fsds_bronze.SchemaMismatchError("new column"),
            sec_fsds_bronze.CorruptZipError("bad zip"),
        ],
        ids=["http-404", "connection", "schema-mismatch", "corrupt-zip"],
    )
    def test_expected_failure_is_recorded_and_the_loop_continues(self, lake_root, mocker, error):
        def fake_ingest(quarter, lake_root, user_agent, force=False):
            if quarter == "2026q3":
                raise error
            return sec_fsds_pipeline.QuarterOutcome(quarter, "ingested")

        mocker.patch("ingestion.sources.sec_fsds_pipeline.ingest_quarter", side_effect=fake_ingest)

        outcomes = sec_fsds_pipeline.run_ingest(["2026q2", "2026q3", "2026q4"], lake_root, UA)

        assert [o.status for o in outcomes] == ["ingested", "failed", "ingested"]
        assert outcomes[1].quarter == "2026q3"
        assert type(error).__name__ in outcomes[1].detail

    def test_unexpected_error_is_not_swallowed(self, lake_root, mocker):
        mocker.patch(
            "ingestion.sources.sec_fsds_pipeline.ingest_quarter", side_effect=TypeError("a bug")
        )
        with pytest.raises(TypeError):
            sec_fsds_pipeline.run_ingest([QUARTER], lake_root, UA)

    def test_force_is_passed_through(self, lake_root, mocker):
        mock = mocker.patch(
            "ingestion.sources.sec_fsds_pipeline.ingest_quarter",
            return_value=sec_fsds_pipeline.QuarterOutcome(QUARTER, "ingested"),
        )

        sec_fsds_pipeline.run_ingest([QUARTER], lake_root, UA, force=True)

        mock.assert_called_once_with(QUARTER, lake_root, UA, force=True)


class TestRunCheck:
    def test_returns_one_outcome_per_quarter_in_order(self, lake_root, mocks):
        outcomes = sec_fsds_pipeline.run_check(["2026q1", "2026q2"], lake_root, UA)

        assert [(o.quarter, o.status) for o in outcomes] == [
            ("2026q1", "would_ingest"),
            ("2026q2", "would_ingest"),
        ]
