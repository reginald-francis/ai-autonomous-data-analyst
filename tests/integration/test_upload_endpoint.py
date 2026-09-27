"""
Integration tests for POST /upload, using FastAPI's TestClient (real HTTP
routing, validation, and response handling — no real server process). These
port the manually-run scenarios from tests/TEST_RESULTS.md's V5 section
(unreproducible today since those models are dead — see PHASES.md Phase 1a)
into automated tests, using FakeGroqClient (conftest.py) instead of a real
Groq call.

Every test needs tmp_data_dir so uploaded files land in a throwaway temp
folder instead of the real data/ directory, and needs to patch
analyst_service.build_agents so the request's LLM calls hit the fake client
instead of attempting a real network call.
"""
from unittest.mock import patch

import numpy as np
import pytest

from src.services import analyst_service
from src.services.rag_service import RagIndex
from tests.conftest import FakeGroqClient

# Phase 4b's context-document tests build a real per-session RagIndex, which
# would otherwise load the real ~35s SentenceTransformer on first use (see
# PHASES.md risk #8 / Phase 2's whole reason for lazy-loading it). This tiny
# deterministic bag-of-words encoder stands in for it — good enough for
# these tests, which only check whether specific words made it into a
# retrieved chunk, not real embedding quality.
_CONTEXT_TEST_VOCAB = ["won", "closed-deal", "session", "revenue", "fiscal"]


class _FakeEmbeddingModel:
    def encode(self, texts):
        return np.array(
            [[text.lower().count(word) for word in _CONTEXT_TEST_VOCAB] for text in texts],
            dtype="float32",
        )


@pytest.fixture(autouse=True)
def fake_embedding_model(monkeypatch):
    monkeypatch.setattr(RagIndex, "_get_model", lambda self: _FakeEmbeddingModel())


def _with_fake_client(fake_client):
    """Context manager that patches build_agents for the duration of one
    request, exactly as tests/integration/test_analyst_orchestration.py
    does — captures the original before patching to avoid the
    self-referencing infinite recursion hit there originally."""
    original_build_agents = analyst_service.build_agents
    return patch.object(
        analyst_service, "build_agents", lambda client=None: original_build_agents(client=fake_client)
    )


def test_first_upload_returns_answer_and_session_id(api_client, tmp_data_dir, sample_csv_path):
    fake_client = FakeGroqClient(
        plan={"task_type": "analysis", "agents": ["python"], "reasoning": "total revenue"},
        code="print(df['revenue'].sum())",
    )

    with _with_fake_client(fake_client):
        with open(sample_csv_path, "rb") as f:
            response = api_client.post(
                "/upload",
                data={"question": "What is total revenue?"},
                files={"file": ("sample_data.csv", f, "text/csv")},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["session_id"]
    assert body["file_name"] == "sample_data.csv"  # original filename, not the UUID-saved path


def test_followup_question_reuses_session(api_client, tmp_data_dir, sample_csv_path):
    fake_client = FakeGroqClient(plan={"task_type": "query", "agents": ["sql"], "reasoning": "filter"})

    with _with_fake_client(fake_client):
        with open(sample_csv_path, "rb") as f:
            first = api_client.post(
                "/upload",
                data={"question": "What is total revenue?"},
                files={"file": ("sample_data.csv", f, "text/csv")},
            )
        session_id = first.json()["session_id"]

        second = api_client.post(
            "/upload",
            data={"question": "Show sales over 10000", "session_id": session_id},
        )

    assert second.status_code == 200
    assert second.json()["session_id"] == session_id
    assert second.json()["file_name"] == "sample_data.csv"


def test_chart_question_returns_chart_path(api_client, tmp_data_dir, sample_csv_path):
    fake_client = FakeGroqClient(
        plan={"task_type": "visualization", "agents": ["python", "chart"], "reasoning": "bar chart"},
    )

    with _with_fake_client(fake_client):
        with open(sample_csv_path, "rb") as f:
            response = api_client.post(
                "/upload",
                data={"question": "Show me a bar chart of revenue by product"},
                files={"file": ("sample_data.csv", f, "text/csv")},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["chart_path"] is not None
    assert "chart" in body["agents_used"]


def test_non_csv_file_rejected(api_client, tmp_data_dir):
    response = api_client.post(
        "/upload",
        data={"question": "What is this?"},
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 400
    assert "CSV" in response.json()["detail"]


def test_empty_csv_rejected(api_client, tmp_data_dir, empty_csv_path):
    """empty.csv has a header row and zero data rows — non-empty bytes, so
    it passes routes/ask.py's "uploaded file is empty" byte-count check and
    the pd.read_csv(nrows=0) parseability check. It's actually
    analyst_service.analyse()'s row_count == 0 guard that rejects it,
    confirmed by running this exact request and reading the real response."""
    with open(empty_csv_path, "rb") as f:
        response = api_client.post(
            "/upload",
            data={"question": "How many rows?"},
            files={"file": ("empty.csv", f, "text/csv")},
        )

    assert response.status_code == 400
    assert "no data rows" in response.json()["detail"]


def test_corrupt_file_with_csv_extension_rejected(api_client, tmp_data_dir):
    # PNG magic bytes, not valid CSV text
    response = api_client.post(
        "/upload",
        data={"question": "What is this?"},
        files={"file": ("fake.csv", b"\x89PNG\r\n\x1a\n\x00\x00\x00", "text/csv")},
    )

    assert response.status_code == 400
    assert "valid CSV" in response.json()["detail"]


def test_invalid_session_id_returns_404(api_client, tmp_data_dir):
    response = api_client.post(
        "/upload",
        data={"question": "Anything", "session_id": "00000000-0000-0000-0000-000000000000"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_no_file_and_no_session_id_returns_400(api_client, tmp_data_dir):
    response = api_client.post("/upload", data={"question": "Anything"})

    assert response.status_code == 400
    assert "No file uploaded" in response.json()["detail"]


def test_session_id_takes_priority_over_file(api_client, tmp_data_dir, sample_csv_path):
    """Matches TEST_RESULTS.md scenario 10: when both session_id and file are
    sent, the session is reused and the new file is ignored."""
    fake_client = FakeGroqClient(plan={"task_type": "analysis", "agents": ["python"]})

    with _with_fake_client(fake_client):
        with open(sample_csv_path, "rb") as f:
            first = api_client.post(
                "/upload",
                data={"question": "Total revenue?"},
                files={"file": ("sample_data.csv", f, "text/csv")},
            )
        session_id = first.json()["session_id"]

        with open(sample_csv_path, "rb") as f:
            second = api_client.post(
                "/upload",
                data={"question": "Total revenue again?", "session_id": session_id},
                files={"file": ("different_name.csv", f, "text/csv")},
            )

    assert second.status_code == 200
    # file_name still reflects the ORIGINAL session's filename, not the
    # ignored "different_name.csv" from this request
    assert second.json()["file_name"] == "sample_data.csv"


def test_oversized_file_rejected(api_client, tmp_data_dir, monkeypatch):
    """Exercises the Settings.max_file_size guard in routes/ask.py without
    actually uploading 10MB — overrides the limit down to a few bytes via
    the MAX_FILE_SIZE env var for this test only."""
    from src.config import get_settings
    monkeypatch.setenv("MAX_FILE_SIZE", "5")
    get_settings.cache_clear()
    try:
        response = api_client.post(
            "/upload",
            data={"question": "Anything"},
            files={"file": ("sample_data.csv", b"date,revenue\n2024-01-01,100\n", "text/csv")},
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 400
    assert "size" in response.json()["detail"].lower()


class _PromptSpyClient(FakeGroqClient):
    """Records every prompt this client is asked to complete, so tests can
    assert on what actually reached the LLM — specifically, whether the
    "Additional business context" section carried the uploaded context
    document's content (Phase 4b)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompts = []

    def create(self, model, messages, temperature=0.1, **kwargs):
        self.prompts.append(self._prompt_text(messages))
        return super().create(model, messages, temperature, **kwargs)


def test_context_document_is_retrieved_into_agent_prompt(api_client, tmp_data_dir, sample_csv_path):
    """A session that uploads a context document alongside its CSV gets that
    document's content threaded into the python agent's prompt for a
    relevant question — the core Phase 4b behavior."""
    spy_client = _PromptSpyClient(
        plan={"task_type": "analysis", "agents": ["python"], "reasoning": "revenue"},
        code="print(df['revenue'].sum())",
    )

    with _with_fake_client(spy_client):
        with open(sample_csv_path, "rb") as f:
            response = api_client.post(
                "/upload",
                data={"question": "What does won mean in our data?"},
                files={
                    "file": ("sample_data.csv", f, "text/csv"),
                    "context_file": (
                        "context.txt",
                        b"In our pipeline, won means a closed-deal, not just a signed contract.",
                        "text/plain",
                    ),
                },
            )

    assert response.status_code == 200
    code_gen_prompts = [p for p in spy_client.prompts if "data analyst" in p.lower()]
    assert any("closed-deal" in p for p in code_gen_prompts)


def test_context_document_is_retrieved_into_planner_prompt(api_client, tmp_data_dir, sample_csv_path):
    """Phase 5 follow-up to 4b: the planner runs before python/sql and can
    reject a question as out_of_scope before either agent ever sees the
    uploaded context document — found via live manual testing (2026-09-16)
    when a glossary defining "senior employee" didn't stop the planner
    from rejecting a question phrased in exactly that term. The planner's
    own routing prompt must now also receive the session's RAG context."""
    spy_client = _PromptSpyClient(
        plan={"task_type": "analysis", "agents": ["python"], "reasoning": "revenue"},
        code="print(df['revenue'].sum())",
    )

    with _with_fake_client(spy_client):
        with open(sample_csv_path, "rb") as f:
            response = api_client.post(
                "/upload",
                data={"question": "What does won mean in our data?"},
                files={
                    "file": ("sample_data.csv", f, "text/csv"),
                    "context_file": (
                        "context.txt",
                        b"In our pipeline, won means a closed-deal, not just a signed contract.",
                        "text/plain",
                    ),
                },
            )

    assert response.status_code == 200
    planner_prompts = [p for p in spy_client.prompts if "planner for a data analysis system" in p.lower()]
    assert planner_prompts  # sanity: the planner did run
    assert any("closed-deal" in p for p in planner_prompts)


def test_no_context_document_means_no_business_context_added(api_client, tmp_data_dir, sample_csv_path):
    """Regression: a session that never uploads a context_file must behave
    exactly as before Phase 4b — no business context text in the prompt."""
    spy_client = _PromptSpyClient(
        plan={"task_type": "analysis", "agents": ["python"], "reasoning": "revenue"},
        code="print(df['revenue'].sum())",
    )

    with _with_fake_client(spy_client):
        with open(sample_csv_path, "rb") as f:
            response = api_client.post(
                "/upload",
                data={"question": "What is total revenue?"},
                files={"file": ("sample_data.csv", f, "text/csv")},
            )

    assert response.status_code == 200
    code_gen_prompts = [p for p in spy_client.prompts if "data analyst" in p.lower()]
    assert code_gen_prompts  # sanity: the python agent did run
    assert not any("closed-deal" in p for p in code_gen_prompts)


def test_context_document_isolated_to_its_own_session(api_client, tmp_data_dir, sample_csv_path):
    """Session A's uploaded context document must never leak into session
    B's answers, even when both sessions are active at once."""
    spy_client = _PromptSpyClient(
        plan={"task_type": "analysis", "agents": ["python"], "reasoning": "revenue"},
        code="print(df['revenue'].sum())",
    )

    with _with_fake_client(spy_client):
        with open(sample_csv_path, "rb") as f:
            api_client.post(
                "/upload",
                data={"question": "What does won mean?"},
                files={
                    "file": ("sample_data.csv", f, "text/csv"),
                    "context_file": (
                        "context.txt",
                        b"In our pipeline, won means a closed-deal for session A only.",
                        "text/plain",
                    ),
                },
            )

        with open(sample_csv_path, "rb") as f:
            api_client.post(
                "/upload",
                data={"question": "What is total revenue?"},
                files={"file": ("sample_data.csv", f, "text/csv")},
            )

    code_gen_prompts = [p for p in spy_client.prompts if "data analyst" in p.lower()]
    assert not any("session A only" in p for p in code_gen_prompts[1:])


def test_context_document_rejects_non_utf8_content(api_client, tmp_data_dir, sample_csv_path):
    with open(sample_csv_path, "rb") as f:
        response = api_client.post(
            "/upload",
            data={"question": "Anything"},
            files={
                "file": ("sample_data.csv", f, "text/csv"),
                "context_file": ("image.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00", "image/png"),
            },
        )

    assert response.status_code == 400
    assert "plain text" in response.json()["detail"].lower()


def test_context_document_rejects_empty_file(api_client, tmp_data_dir, sample_csv_path):
    with open(sample_csv_path, "rb") as f:
        response = api_client.post(
            "/upload",
            data={"question": "Anything"},
            files={
                "file": ("sample_data.csv", f, "text/csv"),
                "context_file": ("context.txt", b"", "text/plain"),
            },
        )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_context_document_rejects_oversized_file(api_client, tmp_data_dir, sample_csv_path, monkeypatch):
    """Exercises Settings.max_context_doc_size without actually uploading a
    multi-MB file — monkeypatches the cap down to a few bytes."""
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "max_context_doc_size", 5)

    with open(sample_csv_path, "rb") as f:
        response = api_client.post(
            "/upload",
            data={"question": "Anything"},
            files={
                "file": ("sample_data.csv", f, "text/csv"),
                "context_file": ("context.txt", b"This context document is longer than 5 bytes.", "text/plain"),
            },
        )

    assert response.status_code == 400
    assert "exceeds" in response.json()["detail"].lower()


def test_two_context_files_under_same_field_rejected(api_client, tmp_data_dir, sample_csv_path):
    with open(sample_csv_path, "rb") as f:
        response = api_client.post(
            "/upload",
            data={"question": "Anything"},
            files=[
                ("file", ("sample_data.csv", f, "text/csv")),
                ("context_file", ("a.txt", b"First context document with enough words.", "text/plain")),
                ("context_file", ("b.txt", b"Second context document with enough words.", "text/plain")),
            ],
        )

    assert response.status_code == 400
    assert "one context document" in response.json()["detail"].lower()


def test_two_files_under_same_field_rejected(api_client, tmp_data_dir, sample_csv_path, employees_csv_path):
    """Regression test for docs/BUGS_FOUND.md #7: sending two files under
    the same "file" form field (something Postman allows, though a normal
    browser file-input can't) used to make FastAPI/Starlette silently bind
    only one of them and discard the other with no error — the response
    still came back 200 success processing whichever file happened to win,
    giving no signal that a file was dropped. Now rejected outright with a
    clear 400 instead of silently discarding data."""
    with open(sample_csv_path, "rb") as f1, open(employees_csv_path, "rb") as f2:
        response = api_client.post(
            "/upload",
            data={"question": "How many rows?"},
            files=[
                ("file", ("sample_data.csv", f1, "text/csv")),
                ("file", ("employees.csv", f2, "text/csv")),
            ],
        )

    assert response.status_code == 400
    assert "one file" in response.json()["detail"].lower()
