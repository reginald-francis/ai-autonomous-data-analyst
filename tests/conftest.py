"""
Shared pytest fixtures. Anything defined here is automatically available to
every test file under tests/ without needing an import — pytest finds this
file by its special name and injects fixtures by matching argument names.
"""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DATA_DIR = REPO_ROOT / "tests" / "data"


@pytest.fixture
def sample_csv_path() -> Path:
    """Path to the 12-row TechMart sample CSV at the repo root."""
    return REPO_ROOT / "sample_data.csv"


@pytest.fixture
def empty_csv_path() -> Path:
    """Path to a CSV with a header row but zero data rows."""
    return TEST_DATA_DIR / "empty.csv"


@pytest.fixture
def special_chars_csv_path() -> Path:
    """Path to a CSV with spaces, parens, and a slash in its headers."""
    return TEST_DATA_DIR / "special_chars.csv"


@pytest.fixture
def large_sales_csv_path() -> Path:
    """Path to the 1000-row CSV used to exercise the high-complexity tier."""
    return TEST_DATA_DIR / "large_sales.csv"


@pytest.fixture
def missing_values_csv_path() -> Path:
    """Path to a CSV with null values scattered across multiple columns."""
    return TEST_DATA_DIR / "missing_values.csv"


@pytest.fixture
def employees_csv_path() -> Path:
    """Path to a non-sales CSV (name/age/department/salary/hire_date/city) —
    used to prove the app works on datasets that aren't TechMart sales data."""
    return TEST_DATA_DIR / "employees.csv"


@pytest.fixture
def stocks_csv_path() -> Path:
    """Path to a non-sales CSV (date/ticker/open/close/volume) — used to
    prove the app works on datasets that aren't TechMart sales data."""
    return TEST_DATA_DIR / "stocks.csv"


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Redirects the app's file operations into a throwaway temp folder instead
    of the real data/ directory, and switches the process's working
    directory there for the duration of the test. Session/database code
    builds paths like "data/uploads/..." relative to cwd, so changing cwd is
    what makes those relative paths land in the temp folder instead of the
    real project.
    """
    (tmp_path / "data" / "uploads").mkdir(parents=True)
    (tmp_path / "data" / "charts").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def tmp_csv_file(tmp_path, sample_csv_path):
    """A writable copy of sample_data.csv inside the test's temp folder."""
    dest = tmp_path / "sample_data.csv"
    shutil.copy(sample_csv_path, dest)
    return dest


class FakeGroqClient:
    """
    Stands in for a real Groq client in integration tests, via
    build_agents(client=...) — the DI seam added in Phase 2. A single
    request can trigger up to four distinct LLM calls in sequence (routing
    plan, complexity classification, python/sql code generation, chart code
    generation), each needing a different canned response — so instead of a
    brittle ordered list of return values, this inspects the outgoing
    prompt's content to decide which "kind" of call it's answering.

    Configure per-test behavior via the `plan`, `complexity`, `code`, and
    `sql` constructor args; anything not overridden falls back to a
    reasonable default.
    """

    def __init__(self, plan=None, complexity="low", code=None, sql=None, chart_spec=None):
        self.plan = plan or {
            "task_type": "analysis",
            "agents": ["python"],
            "reasoning": "fake plan for testing",
        }
        self.complexity = complexity
        self.code = code or "print(df['revenue'].sum())"
        self.sql = sql or "SELECT * FROM sample_data;"
        # chart_agent.py's redesign (Phase 4) has the LLM pick a small JSON
        # spec, not write matplotlib code — see ChartAgent._get_chart_spec.
        self.chart_spec = chart_spec or json.dumps({
            "chart_type": "bar",
            "x_column": "region",
            "y_column": "revenue",
            "aggregation": "sum",
            "title": "Revenue by Region",
        })
        self.call_count = 0
        self.chat = self  # so fake_client.chat.completions.create(...) resolves to self.completions
        self.completions = self

    def _prompt_text(self, messages) -> str:
        return " ".join(m.get("content", "") for m in messages)

    def create(self, model, messages, temperature=0.1, **kwargs):
        self.call_count += 1
        text = self._prompt_text(messages)

        if "complexity classifier" in text.lower():
            content = json.dumps({"complexity": self.complexity})
        elif "planner for a data analysis system" in text.lower():
            content = json.dumps(self.plan)
        elif "sql expert" in text.lower():
            content = self.sql
        elif "visualization expert" in text.lower():
            content = self.chart_spec
        else:
            # python/sql code-generation prompts all mention pandas or "data analyst"
            content = self.code

        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


@pytest.fixture
def fake_groq_client():
    """Default-configured FakeGroqClient — routes to the python agent with a
    low-complexity plan and returns a simple revenue-sum answer. Use
    FakeGroqClient(...) directly in a test when you need different plan,
    complexity, code, or sql values."""
    return FakeGroqClient()


@pytest.fixture
def api_client():
    """
    A FastAPI TestClient that does NOT trigger main.py's lifespan handler —
    constructed without the `with TestClient(app) as client:` context
    manager form, so startup/shutdown events never fire. Since Phase 4b,
    lifespan no longer builds any RAG index at all (RAG is per-session, built
    on demand only when a session actually uploads a context document via
    /upload), so this mostly just skips the session-cleanup background task.

    Routes work fine without it: rag_service.retrieve_session_context()
    already returns "" for any session with no uploaded context document
    (see rag_service.py), and format_context_block() (Phase 5 follow-up)
    turns that into "" too — no "Additional business context" section
    appears in the prompt at all in that case, not an empty one.
    """
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_sessions():
    """
    session_service keeps sessions in a plain module-level dict
    (`_sessions`), not reset between tests. rag_service similarly keeps
    per-session RAG indexes in a module-level dict (`_session_indexes`,
    Phase 4b). Autouse means this runs for every single test automatically
    (no need to request it by name), clearing both before and after each
    test so state from one test can never leak into another — e.g. a
    session_id created in one upload test being unexpectedly still valid,
    or still carrying a RAG index, in a later test.
    """
    from src.services import session_service, rag_service
    session_service._sessions.clear()
    rag_service._session_indexes.clear()
    yield
    session_service._sessions.clear()
    rag_service._session_indexes.clear()
