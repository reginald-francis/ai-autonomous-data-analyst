import os
import glob
import logging
import threading
from datetime import datetime, timedelta

from src.config import get_settings
from src.services import rag_service

logger = logging.getLogger(__name__)

# In-memory session store: session_id -> session data
_sessions: dict = {}
_lock = threading.Lock()  # prevents race conditions when multiple requests hit at once


def _safe_remove(path: str) -> bool:
    """Delete a file, tolerating Windows file locks instead of crashing the
    caller. A file still locked by another process is left for the next
    cleanup cycle to retry. Returns True if the file was actually deleted."""
    try:
        os.remove(path)
        return True
    except (PermissionError, OSError) as e:
        logger.warning(f"Could not delete {path} (will retry later): {e}")
        return False


def cleanup_orphaned_files() -> None:
    """Delete all files in uploads, charts, and generated DB files on startup.
    These are orphans — server restarted and session memory was wiped, so
    there is no session that will ever clean them up."""
    settings = get_settings()
    dirs = [settings.uploads_dir, settings.charts_dir]
    total = 0
    for directory in dirs:
        for f in glob.glob(f"{directory}/*"):
            if os.path.basename(f) == ".gitkeep":
                continue
            if _safe_remove(f):
                total += 1

    # Session-scoped SQLite databases (data/{session_id}_{table_name}.db) are
    # generated the same way — with no session left in memory, nothing else
    # will ever clean them up.
    for db_file in glob.glob(f"{settings.db_dir}/*.db"):
        if _safe_remove(db_file):
            total += 1

    if total:
        logger.info(f"Startup cleanup: removed {total} orphaned file(s)")


def create_session(session_id: str, file_path: str, original_filename: str) -> None:
    """Register a session with a pre-generated session_id."""
    with _lock:
        _sessions[session_id] = {
            "file_path": file_path,
            "original_filename": original_filename,
            "created_at": datetime.now(),
            "last_accessed": datetime.now(),
        }
    logger.info(f"Session created: {session_id} | file: {original_filename}")


def get_session(session_id: str) -> dict | None:
    """Return session data and update last_accessed, or None if not found/expired."""
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return None
        # Check if session has expired
        age = datetime.now() - session["last_accessed"]
        if age > timedelta(minutes=get_settings().session_ttl_minutes):
            logger.info(f"Session expired on access: {session_id}")
            _delete_session_files(session_id, session)
            del _sessions[session_id]
            return None
        session["last_accessed"] = datetime.now()
        return session


def cleanup_expired_sessions() -> None:
    """Delete all sessions whose last_accessed time exceeds the TTL. Called by background task."""
    now = datetime.now()
    ttl = timedelta(minutes=get_settings().session_ttl_minutes)
    expired = []
    with _lock:
        for session_id, session in _sessions.items():
            age = now - session["last_accessed"]
            if age > ttl:
                expired.append((session_id, session))
        for session_id, session in expired:
            _delete_session_files(session_id, session)
            del _sessions[session_id]

    if expired:
        logger.info(f"Cleaned up {len(expired)} expired session(s)")


def _delete_session_files(session_id: str, session: dict) -> None:
    """Delete the uploaded CSV and any generated DB files for this session."""
    # Delete uploaded CSV
    file_path = session.get("file_path", "")
    if file_path and os.path.exists(file_path):
        if _safe_remove(file_path):
            logger.info(f"Deleted session file: {file_path}")

    # Delete any generated DB files for this session (pattern: data/{session_id}_*.db)
    for db_file in glob.glob(f"{get_settings().db_dir}/{session_id}_*.db"):
        if _safe_remove(db_file):
            logger.info(f"Deleted session DB: {db_file}")

    # Discard this session's RAG index (if it uploaded a context document) —
    # no-op if it never did
    rag_service.drop_session(session_id)
