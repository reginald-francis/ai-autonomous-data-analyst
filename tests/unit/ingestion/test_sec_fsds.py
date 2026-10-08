"""Tests for ingestion.sources.sec_fsds — downloading and change detection.

All HTTP calls are mocked; this suite never touches the network.
"""

import hashlib
import json
import os

import pytest
import requests

from ingestion import storage
from ingestion.sources import sec_fsds


class TestParseQuarter:
    def test_parses_valid_quarter(self):
        assert sec_fsds.parse_quarter("2026q2") == (2026, 2)

    @pytest.mark.parametrize("bad", ["2026-q2", "26q2", "2026q5", "2026", "q2 2026"])
    def test_rejects_invalid_quarter(self, bad):
        with pytest.raises(sec_fsds.QuarterFormatError):
            sec_fsds.parse_quarter(bad)


class TestQuarterZipUrl:
    def test_builds_the_confirmed_sec_url_pattern(self):
        assert sec_fsds.quarter_zip_url("2026q2") == (
            "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q2.zip"
        )

    def test_rejects_invalid_quarter(self):
        with pytest.raises(sec_fsds.QuarterFormatError):
            sec_fsds.quarter_zip_url("not-a-quarter")


class FakeStreamedResponse:
    """A minimal stand-in for requests.Response used as a context manager,
    since download_quarter() uses `with requests.get(...) as response:`.
    """

    def __init__(self, content: bytes, status_code: int = 200):
        self._content = content
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            # response=self mirrors how real requests.Response.raise_for_status()
            # attaches itself to the error, so _is_transient() can inspect
            # exc.response.status_code the same way it would for a real response.
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def iter_content(self, chunk_size=1024 * 1024):
        yield self._content


class TestDownloadQuarter:
    def test_downloads_with_the_required_sec_header(self, tmp_path, mocker):
        captured = {}

        def fake_get(url, headers=None, stream=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeStreamedResponse(b"fake zip bytes")

        mocker.patch("ingestion.sources.sec_fsds.requests.get", side_effect=fake_get)

        dest = sec_fsds.download_quarter("2026q2", str(tmp_path), "Test Agent test@example.com")

        assert captured["url"] == (
            "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q2.zip"
        )
        assert captured["headers"] == {"User-Agent": "Test Agent test@example.com"}
        assert os.path.exists(dest)
        with open(dest, "rb") as f:
            assert f.read() == b"fake zip bytes"
        # Atomic write: no leftover .tmp file once the rename has happened
        assert not os.path.exists(dest + ".tmp")

    def test_raises_on_http_error_and_leaves_no_partial_file(self, tmp_path, mocker):
        mocker.patch(
            "ingestion.sources.sec_fsds.requests.get",
            return_value=FakeStreamedResponse(b"", status_code=404),
        )
        with pytest.raises(requests.HTTPError):
            sec_fsds.download_quarter("2026q2", str(tmp_path), "Test Agent test@example.com")

        dest = storage.landing_zip_path(str(tmp_path), "2026q2")
        assert not os.path.exists(dest)
        assert not os.path.exists(dest + ".tmp")


class TestDownloadQuarterRetry:
    """Pins max_attempts/backoff via mocking rather than relying on
    ingestion/config.py's real defaults, so these tests don't need updating
    every time the production retry policy gets tuned.
    """

    def _pin_retry_config(self, mocker, max_attempts=3, backoff=(1, 2)):
        mocker.patch("ingestion.config.get_max_download_attempts", return_value=max_attempts)
        mocker.patch("ingestion.config.get_retry_backoff_seconds", return_value=backoff)

    def test_retries_on_transient_connection_error_then_succeeds(self, tmp_path, mocker):
        mocker.patch("ingestion.sources.sec_fsds.time.sleep")  # don't actually wait in tests
        self._pin_retry_config(mocker)
        attempts = {"count": 0}

        def flaky_get(url, headers=None, stream=None, timeout=None):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise requests.exceptions.ConnectionError("simulated drop")
            return FakeStreamedResponse(b"fake zip bytes")

        mocker.patch("ingestion.sources.sec_fsds.requests.get", side_effect=flaky_get)

        dest = sec_fsds.download_quarter("2026q2", str(tmp_path), "Test Agent test@example.com")

        assert attempts["count"] == 2
        with open(dest, "rb") as f:
            assert f.read() == b"fake zip bytes"

    def test_retries_on_5xx_then_gives_up_after_max_attempts(self, tmp_path, mocker):
        mocker.patch("ingestion.sources.sec_fsds.time.sleep")
        self._pin_retry_config(mocker, max_attempts=3, backoff=(1, 2))
        mock_get = mocker.patch(
            "ingestion.sources.sec_fsds.requests.get",
            return_value=FakeStreamedResponse(b"", status_code=503),
        )
        with pytest.raises(requests.HTTPError):
            sec_fsds.download_quarter("2026q2", str(tmp_path), "Test Agent test@example.com")

        assert mock_get.call_count == 3

    def test_does_not_retry_on_a_4xx_client_error(self, tmp_path, mocker):
        mocker.patch("ingestion.sources.sec_fsds.time.sleep")
        self._pin_retry_config(mocker)
        mock_get = mocker.patch(
            "ingestion.sources.sec_fsds.requests.get",
            return_value=FakeStreamedResponse(b"", status_code=404),
        )
        with pytest.raises(requests.HTTPError):
            sec_fsds.download_quarter("2026q2", str(tmp_path), "Test Agent test@example.com")

        assert mock_get.call_count == 1


class TestGetRemoteMetadata:
    class _FakeHeadResponse:
        def __init__(self, headers):
            self.headers = headers

        def raise_for_status(self):
            pass

    def test_returns_headers_from_head_request(self, mocker):
        captured = {}

        def fake_head(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            return self._FakeHeadResponse(
                {
                    "ETag": '"abc123"',
                    "Last-Modified": "Mon, 01 Jan 2026 00:00:00 GMT",
                    "Content-Length": "1000",
                }
            )

        mocker.patch("ingestion.sources.sec_fsds.requests.head", side_effect=fake_head)

        meta = sec_fsds.get_remote_metadata("2026q2", "Test Agent test@example.com")

        assert captured["url"] == (
            "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q2.zip"
        )
        assert captured["headers"] == {"User-Agent": "Test Agent test@example.com"}
        assert meta == {
            "etag": '"abc123"',
            "last_modified": "Mon, 01 Jan 2026 00:00:00 GMT",
            "content_length": "1000",
        }

    def test_missing_headers_become_none(self, mocker):
        mocker.patch(
            "ingestion.sources.sec_fsds.requests.head",
            return_value=self._FakeHeadResponse({}),
        )
        meta = sec_fsds.get_remote_metadata("2026q2", "Test Agent test@example.com")
        assert meta == {"etag": None, "last_modified": None, "content_length": None}


class TestNeedsDownload:
    def test_true_when_no_manifest_exists_yet(self, tmp_path):
        assert sec_fsds.needs_download("2026q2", str(tmp_path), "Test Agent test@example.com") is True

    def _write_manifest(self, lake_root, quarter, contents):
        manifest_path = storage.manifest_path(lake_root, quarter)
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(contents, f)

    def test_false_when_etag_matches(self, tmp_path, mocker):
        self._write_manifest(str(tmp_path), "2026q2", {"etag": '"abc123"'})
        mocker.patch(
            "ingestion.sources.sec_fsds.get_remote_metadata",
            return_value={"etag": '"abc123"', "last_modified": None, "content_length": "1000"},
        )
        assert sec_fsds.needs_download("2026q2", str(tmp_path), "Test Agent test@example.com") is False

    def test_true_when_etag_differs(self, tmp_path, mocker):
        self._write_manifest(str(tmp_path), "2026q2", {"etag": '"old"'})
        mocker.patch(
            "ingestion.sources.sec_fsds.get_remote_metadata",
            return_value={"etag": '"new"', "last_modified": None, "content_length": "1000"},
        )
        assert sec_fsds.needs_download("2026q2", str(tmp_path), "Test Agent test@example.com") is True

    def test_true_when_neither_side_has_enough_info(self, tmp_path, mocker):
        # No etag/last_modified recorded yet - realistic until Step 5 starts writing them.
        self._write_manifest(str(tmp_path), "2026q2", {"sha256": "whatever"})
        mocker.patch(
            "ingestion.sources.sec_fsds.get_remote_metadata",
            return_value={"etag": None, "last_modified": None, "content_length": "1000"},
        )
        assert sec_fsds.needs_download("2026q2", str(tmp_path), "Test Agent test@example.com") is True

    def test_true_when_head_request_fails(self, tmp_path, mocker):
        self._write_manifest(str(tmp_path), "2026q2", {"etag": '"abc123"'})
        mocker.patch(
            "ingestion.sources.sec_fsds.get_remote_metadata",
            side_effect=requests.exceptions.ConnectionError("down"),
        )
        assert sec_fsds.needs_download("2026q2", str(tmp_path), "Test Agent test@example.com") is True


class TestSha256OfFile:
    def test_matches_known_hash(self, tmp_path):
        path = tmp_path / "sample.txt"
        path.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert sec_fsds.sha256_of_file(str(path)) == expected


class TestHasQuarterChanged:
    def test_true_when_no_manifest_exists_yet(self, tmp_path):
        zip_path = tmp_path / "2026q2.zip"
        zip_path.write_bytes(b"content")
        assert sec_fsds.has_quarter_changed("2026q2", str(zip_path), str(tmp_path)) is True

    def test_false_when_hash_matches_existing_manifest(self, tmp_path):
        zip_path = tmp_path / "2026q2.zip"
        zip_path.write_bytes(b"content")
        existing_hash = sec_fsds.sha256_of_file(str(zip_path))

        manifest_path = storage.manifest_path(str(tmp_path), "2026q2")
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({"sha256": existing_hash}, f)

        assert sec_fsds.has_quarter_changed("2026q2", str(zip_path), str(tmp_path)) is False

    def test_true_when_hash_differs_from_existing_manifest(self, tmp_path):
        zip_path = tmp_path / "2026q2.zip"
        zip_path.write_bytes(b"new content")

        manifest_path = storage.manifest_path(str(tmp_path), "2026q2")
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({"sha256": "deadbeef"}, f)

        assert sec_fsds.has_quarter_changed("2026q2", str(zip_path), str(tmp_path)) is True
