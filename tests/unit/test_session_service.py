"""
Tests for src/services/session_service.py — the in-memory session store and
the file-cleanup logic fixed in Phase 1b (safe delete + startup .db sweep).

No Groq calls, no network, no real 'data/' folder involved — everything here
either uses tmp_path or talks only to the module's in-memory _sessions dict.
"""
from datetime import datetime, timedelta

import pytest

from src.config import get_settings
from src.services import session_service


@pytest.fixture(autouse=True)
def reset_sessions():
    """
    Runs automatically before AND after every test in this file (autouse=True
    means you don't have to ask for it by name). session_service._sessions is
    a plain module-level dict shared by the whole process, so without this,
    a session created in one test would still be visible in the next one.
    """
    session_service._sessions.clear()
    yield
    session_service._sessions.clear()


def test_create_session_stores_expected_fields():
    session_service.create_session("s1", "data/uploads/s1.csv", "sample_data.csv")

    session = session_service._sessions["s1"]
    assert session["file_path"] == "data/uploads/s1.csv"
    assert session["original_filename"] == "sample_data.csv"
    assert "created_at" in session
    assert "last_accessed" in session


def test_get_session_returns_none_for_unknown_id():
    assert session_service.get_session("does-not-exist") is None


def test_get_session_returns_the_session_you_created():
    session_service.create_session("s1", "data/uploads/s1.csv", "sample_data.csv")

    session = session_service.get_session("s1")

    assert session is not None
    assert session["original_filename"] == "sample_data.csv"


def test_get_session_updates_last_accessed(monkeypatch):
    session_service.create_session("s1", "data/uploads/s1.csv", "sample_data.csv")
    original_time = session_service._sessions["s1"]["last_accessed"]

    # Force time to visibly move forward so the "last_accessed updated" check
    # can't pass by accident just because two datetime.now() calls landed in
    # the same microsecond.
    later = original_time + timedelta(minutes=1)
    monkeypatch.setattr(session_service, "datetime", _FixedDatetime(later))

    session_service.get_session("s1")

    assert session_service._sessions["s1"]["last_accessed"] == later


def test_get_session_expires_and_removes_old_session(monkeypatch, tmp_data_dir):
    session_service.create_session("s1", "data/uploads/s1.csv", "sample_data.csv")

    # Backdate last_accessed well past the TTL instead of sleeping for real.
    too_old = datetime.now() - timedelta(minutes=get_settings().session_ttl_minutes + 5)
    session_service._sessions["s1"]["last_accessed"] = too_old

    result = session_service.get_session("s1")

    assert result is None
    assert "s1" not in session_service._sessions


def test_get_session_within_ttl_is_not_expired():
    session_service.create_session("s1", "data/uploads/s1.csv", "sample_data.csv")

    just_under_ttl = datetime.now() - timedelta(
        minutes=get_settings().session_ttl_minutes - 1
    )
    session_service._sessions["s1"]["last_accessed"] = just_under_ttl

    assert session_service.get_session("s1") is not None


def test_session_ttl_env_override_is_honored(monkeypatch, tmp_data_dir):
    """SESSION_TTL_MINUTES must actually change expiry behavior — the TTL
    used to be a hardcoded module constant that silently ignored Settings."""
    monkeypatch.setenv("SESSION_TTL_MINUTES", "1")
    get_settings.cache_clear()
    try:
        session_service.create_session("s1", "data/uploads/s1.csv", "sample_data.csv")
        session_service._sessions["s1"]["last_accessed"] = datetime.now() - timedelta(minutes=2)

        assert session_service.get_session("s1") is None
    finally:
        get_settings.cache_clear()


def test_cleanup_expired_sessions_removes_only_expired_ones():
    session_service.create_session("fresh", "data/uploads/fresh.csv", "fresh.csv")
    session_service.create_session("stale", "data/uploads/stale.csv", "stale.csv")

    too_old = datetime.now() - timedelta(minutes=get_settings().session_ttl_minutes + 1)
    session_service._sessions["stale"]["last_accessed"] = too_old

    session_service.cleanup_expired_sessions()

    assert "fresh" in session_service._sessions
    assert "stale" not in session_service._sessions


def test_delete_session_files_removes_uploaded_csv(tmp_data_dir):
    csv_path = tmp_data_dir / "data" / "uploads" / "s1.csv"
    csv_path.write_text("date,revenue\n2024-01-01,100\n")

    session = {"file_path": str(csv_path), "original_filename": "s1.csv"}
    session_service._delete_session_files("s1", session)

    assert not csv_path.exists()


def test_delete_session_files_removes_matching_db_files(tmp_data_dir):
    (tmp_data_dir / "data" / "s1_sample_data.db").write_text("")
    (tmp_data_dir / "data" / "other_session_sample_data.db").write_text("")

    session = {"file_path": "", "original_filename": ""}
    session_service._delete_session_files("s1", session)

    assert not (tmp_data_dir / "data" / "s1_sample_data.db").exists()
    assert (tmp_data_dir / "data" / "other_session_sample_data.db").exists()


def test_delete_session_files_tolerates_missing_file(tmp_data_dir):
    session = {"file_path": "data/uploads/never-existed.csv", "original_filename": "x.csv"}

    # Should not raise even though the file was never created.
    session_service._delete_session_files("s1", session)


def test_safe_remove_returns_false_on_permission_error(monkeypatch, tmp_data_dir):
    def raise_permission_error(path):
        raise PermissionError("file is locked")

    monkeypatch.setattr(session_service.os, "remove", raise_permission_error)

    result = session_service._safe_remove("data/uploads/locked.csv")

    assert result is False


def test_safe_remove_returns_true_on_success(tmp_data_dir):
    target = tmp_data_dir / "data" / "uploads" / "deleteme.csv"
    target.write_text("x")

    assert session_service._safe_remove(str(target)) is True
    assert not target.exists()


def test_cleanup_orphaned_files_sweeps_uploads_charts_and_db(tmp_data_dir):
    uploads_file = tmp_data_dir / "data" / "uploads" / "orphan.csv"
    charts_file = tmp_data_dir / "data" / "charts" / "orphan.png"
    db_file = tmp_data_dir / "data" / "orphan_sample_data.db"
    gitkeep = tmp_data_dir / "data" / "uploads" / ".gitkeep"

    uploads_file.write_text("x")
    charts_file.write_text("x")
    db_file.write_text("x")
    gitkeep.write_text("")

    session_service.cleanup_orphaned_files()

    assert not uploads_file.exists()
    assert not charts_file.exists()
    assert not db_file.exists()
    assert gitkeep.exists()  # .gitkeep must survive the sweep


class _FixedDatetime:
    """A stand-in for the datetime module that always returns a fixed 'now'."""

    def __init__(self, fixed_now):
        self._fixed_now = fixed_now

    def now(self):
        return self._fixed_now
