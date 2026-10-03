"""
Integration tests for POST /ask — the original JSON, file_path-based
endpoint kept for backward compatibility after /upload shipped in V5 (see
CLAUDE.md's Request Flow diagram). /upload is the real interface now;
/ask is a local dev/testing-only endpoint (Phase 5: disabled by default via
Settings.enable_ask_endpoint, no auth). These tests cover only the
security-relevant behavior — everything else (success path, session-id
generation, 404/400 on bad files) is already exercised by
test_upload_endpoint.py and would just be redundant here.
"""
import pytest

from src.config import get_settings


@pytest.fixture
def enable_ask(monkeypatch):
    """Flips Settings.enable_ask_endpoint on for one test, bypassing the
    lru_cache so get_settings() reflects the override."""
    monkeypatch.setenv("ENABLE_ASK_ENDPOINT", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_ask_disabled_by_default(api_client, tmp_data_dir):
    """No env override — the endpoint must be unreachable out of the box."""
    get_settings.cache_clear()
    response = api_client.post(
        "/ask",
        json={"question": "Anything", "file_path": "sample_data.csv"},
    )

    assert response.status_code == 404
    get_settings.cache_clear()


def test_ask_rejects_path_traversal(api_client, tmp_data_dir, enable_ask):
    """PHASES.md risk #3 — caller-supplied file_path used to be read with no
    validation at all (confirmed live in Phase 3: read a real .env,
    including the live GROQ_API_KEY, which was rotated afterward). Phase 5
    resolves file_path against the project root and rejects any escape."""
    response = api_client.post(
        "/ask",
        json={"question": "Anything", "file_path": "../../../etc/passwd"},
    )

    assert response.status_code == 403


def test_ask_rejects_absolute_path_outside_project(api_client, tmp_data_dir, enable_ask):
    """An absolute path elsewhere on disk must be rejected regardless of OS.
    Built from a sibling of the project root (tmp_data_dir's parent) rather
    than a hardcoded OS-specific string like "C:/Windows/..." — that string
    is only meaningfully "outside the project" on Windows; on Linux CI,
    os.path.join treats it as a relative segment that lands back inside the
    root, so the test would silently stop testing what it claims to."""
    outside_path = tmp_data_dir.parent / "outside.csv"

    response = api_client.post(
        "/ask",
        json={"question": "Anything", "file_path": str(outside_path)},
    )

    assert response.status_code == 403


def test_ask_allows_project_relative_path(api_client, tmp_data_dir, tmp_csv_file, enable_ask):
    """A legitimate project-relative path (the intended use case) must still
    resolve — proves the fix rejects escapes without breaking normal use."""
    from unittest.mock import patch

    from src.services import analyst_service
    from tests.conftest import FakeGroqClient

    fake_client = FakeGroqClient(
        plan={"task_type": "analysis", "agents": ["python"], "reasoning": "total revenue"},
        code="print(df['revenue'].sum())",
    )
    original_build_agents = analyst_service.build_agents

    with patch.object(
        analyst_service, "build_agents", lambda client=None: original_build_agents(client=fake_client)
    ):
        response = api_client.post(
            "/ask",
            json={"question": "What is total revenue?", "file_path": tmp_csv_file.name},
        )

    assert response.status_code == 200
