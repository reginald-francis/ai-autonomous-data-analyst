"""
Tests for src/config.py — the pydantic-settings Settings class that
centralizes the ~18 values previously hardcoded across llm_service,
session_service, rag_service, database_service, the agents, and
routes/ask.py.

Settings() reads GROQ_API_KEY and friends from the real process environment
and .env by default. Every test here uses monkeypatch.setenv/delenv to
control exactly what Settings() sees, so these tests are hermetic regardless
of what's actually in the developer's .env file.
"""
import pytest

from src.config import Settings, get_settings


def test_defaults_load_without_env_file(monkeypatch, tmp_path):
    """Settings() must not require a .env file or any env vars to construct —
    every field has a default matching current hardcoded behavior.

    monkeypatch.delenv clears GROQ_API_KEY from the real OS environment too —
    without it, a developer with GROQ_API_KEY exported in their shell (not
    just in .env) would see this test fail on their machine but pass in CI,
    since chdir alone only hides .env, not real env vars."""
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    settings = Settings()

    assert settings.groq_api_key == ""
    assert settings.default_model == "openai/gpt-oss-120b"
    assert settings.session_ttl_minutes == 30
    assert settings.max_file_size == 10 * 1024 * 1024
    assert settings.uploads_dir == "data/uploads"
    assert settings.charts_dir == "data/charts"
    assert settings.embedding_model == "all-MiniLM-L6-v2"
    assert settings.embedding_model_path == "models/all-MiniLM-L6-v2-onnx"
    assert settings.public_base_url == ""


def test_env_var_overrides_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-key-123")
    monkeypatch.setenv("SESSION_TTL_MINUTES", "60")

    settings = Settings()

    assert settings.groq_api_key == "test-key-123"
    assert settings.session_ttl_minutes == 60


def test_env_var_type_coercion_for_int_field(monkeypatch, tmp_path):
    """pydantic-settings should coerce the env var string to the field's
    declared type (int), not leave it as a literal string."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RETRY_BUDGET_HIGH", "7")

    settings = Settings()

    assert settings.retry_budget_high == 7
    assert isinstance(settings.retry_budget_high, int)


def test_invalid_int_env_var_raises_validation_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAX_FILE_SIZE", "not-a-number")

    with pytest.raises(Exception):
        Settings()


@pytest.mark.parametrize(
    "complexity, expected_model",
    [
        ("low", "openai/gpt-oss-20b"),
        ("medium", "openai/gpt-oss-120b"),
        ("high", "openai/gpt-oss-120b"),
    ],
)
def test_model_routing_property_matches_individual_fields(monkeypatch, tmp_path, complexity, expected_model):
    monkeypatch.chdir(tmp_path)
    settings = Settings()

    assert settings.model_routing[complexity] == expected_model


def test_retry_budget_property_matches_individual_fields(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings()

    assert settings.retry_budget == {"low": 2, "medium": 3, "high": 5}


def test_get_settings_returns_same_cached_instance():
    """lru_cache means get_settings() constructs Settings() once per process
    and hands back the identical object on every later call."""
    first = get_settings()
    second = get_settings()

    assert first is second


def test_get_settings_reflects_env_at_first_call_only(monkeypatch):
    """Documents the caching tradeoff: once get_settings() has been called
    once in a process, later env var changes have no effect until the cache
    is explicitly cleared. This guards against someone assuming
    get_settings() is always live."""
    get_settings()  # prime the cache (may already be cached from another test)
    monkeypatch.setenv("SESSION_TTL_MINUTES", "999")

    settings = get_settings()

    assert settings.session_ttl_minutes != 999

    get_settings.cache_clear()
    fresh = get_settings()
    assert fresh.session_ttl_minutes == 999
    get_settings.cache_clear()  # leave the cache clean for tests that follow
