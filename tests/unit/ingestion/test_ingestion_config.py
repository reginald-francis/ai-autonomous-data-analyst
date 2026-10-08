"""Tests for ingestion.config — environment-variable reading and defaults."""

import pytest

from ingestion import config


class TestGetSecUserAgent:
    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
        with pytest.raises(config.ConfigError):
            config.get_sec_user_agent()

    def test_returns_configured_value(self, monkeypatch):
        monkeypatch.setenv("SEC_USER_AGENT", "Test Agent test@example.com")
        assert config.get_sec_user_agent() == "Test Agent test@example.com"


class TestGetLakeRoot:
    def test_defaults_to_data_lake(self, monkeypatch):
        monkeypatch.delenv("INGESTION_LAKE_ROOT", raising=False)
        assert config.get_lake_root() == "data/lake"

    def test_returns_configured_value(self, monkeypatch):
        monkeypatch.setenv("INGESTION_LAKE_ROOT", "gs://example-bucket/lake")
        assert config.get_lake_root() == "gs://example-bucket/lake"


class TestGetMaxDownloadAttempts:
    def test_defaults_to_four(self, monkeypatch):
        monkeypatch.delenv("INGESTION_MAX_DOWNLOAD_ATTEMPTS", raising=False)
        assert config.get_max_download_attempts() == 4

    def test_returns_configured_value(self, monkeypatch):
        monkeypatch.setenv("INGESTION_MAX_DOWNLOAD_ATTEMPTS", "6")
        assert config.get_max_download_attempts() == 6


class TestGetRetryBackoffSeconds:
    def test_defaults_to_widened_backoff(self, monkeypatch):
        monkeypatch.delenv("INGESTION_RETRY_BACKOFF_SECONDS", raising=False)
        assert config.get_retry_backoff_seconds() == (2, 8, 20)

    def test_parses_configured_comma_separated_value(self, monkeypatch):
        monkeypatch.setenv("INGESTION_RETRY_BACKOFF_SECONDS", "1, 5, 15")
        assert config.get_retry_backoff_seconds() == (1, 5, 15)
