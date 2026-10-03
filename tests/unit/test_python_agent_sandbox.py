"""
Tests for python_agent.py's exec() sandboxing (Phase 5 — PHASES.md risk #2,
docs/THREAT_MODEL.md). Covers the three layers added: a restricted
__builtins__ dict, an AST pre-check that rejects imports and dunder-attribute
access, and a best-effort wall-clock timeout. Each malicious/hanging
snippet is expected to raise, since execute_code() wraps failures as
"Execution error: ..." — the retry loop (tested elsewhere) is what turns
that into a fix_code() call, not this file's concern.
"""
import multiprocessing
import time

import pandas as pd
import pytest

from src.agents.python_agent import EXEC_TIMEOUT_SECONDS, PythonAgent


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "data.csv"
    pd.DataFrame({"revenue": [10, 20, 30], "region": ["A", "B", "C"]}).to_csv(path, index=False)
    return str(path)


@pytest.fixture
def agent():
    return PythonAgent(client=object())  # no LLM call happens in these tests


def test_legitimate_pandas_code_still_works(agent, csv_path):
    output = agent.execute_code("print(df['revenue'].sum())", csv_path)
    assert "60" in output


def test_pd_module_is_available(agent, csv_path):
    """generate_code()'s own prompt instructs the LLM to call
    pd.to_datetime()/pd.Grouper() for date-based grouping — found via
    manual edge-case testing (2026-09-15) that the sandboxed environment
    never actually included pd, so every date/trend question's generated
    code failed with "name 'pd' is not defined" regardless of this fix."""
    output = agent.execute_code(
        "df['revenue2'] = pd.to_numeric(df['revenue'])\nprint(df['revenue2'].sum())",
        csv_path,
    )
    assert "60" in output


def test_class_definitions_still_work(agent, csv_path):
    """A `class` statement's compiled bytecode calls __build_class__ and
    reads __name__ internally, even for an ordinary, benign class body —
    both are supplied by the real module namespace outside a sandbox, so
    restricting __builtins__ without also providing __build_class__ (and
    __name__ in the exec globals) broke this legitimate pattern. Found via
    manual edge-case testing (2026-09-15)."""
    output = agent.execute_code(
        "class Stats:\n"
        "    def __init__(self, data):\n"
        "        self.data = data\n"
        "    def total(self):\n"
        "        return self.data.sum()\n"
        "print(Stats(df['revenue']).total())",
        csv_path,
    )
    assert "60" in output


def test_blocks_open(agent, csv_path):
    with pytest.raises(Exception, match="Execution error"):
        agent.execute_code("open('anything.txt').read()\nprint('done')", csv_path)


def test_blocks_import_statement(agent, csv_path):
    with pytest.raises(Exception, match="import statements"):
        agent.execute_code("import os\nprint('done')", csv_path)


def test_blocks_dunder_import(agent, csv_path):
    with pytest.raises(Exception, match="Execution error"):
        agent.execute_code("__import__('os').system('echo hi')\nprint('done')", csv_path)


def test_blocks_dunder_attribute_class_walk(agent, csv_path):
    with pytest.raises(Exception, match="dunder attributes"):
        agent.execute_code(
            "print(().__class__.__bases__[0].__subclasses__())", csv_path
        )


def test_blocks_getattr_indirection(agent, csv_path):
    """getattr/setattr aren't in the allowlist, so a string-built attribute
    name (e.g. to dodge a literal '__class__' scan) still fails — via
    NameError on getattr itself, not the AST check."""
    with pytest.raises(Exception, match="Execution error"):
        agent.execute_code(
            "print(getattr(df, '__cla' + 'ss__'))", csv_path
        )


def test_rejects_syntax_error_before_exec(agent, csv_path):
    with pytest.raises(Exception, match="syntax error"):
        agent.execute_code("print(df['revenue'].sum(", csv_path)


def _run_hanging_code_and_report(csv_path, result_queue):
    """Runs in a child process (see test_timeout_on_hanging_code) so the
    zombie worker thread the timeout leaves behind — see EXEC_TIMEOUT_
    SECONDS's docstring in python_agent.py: CPython threads can't be
    forcibly killed, so "while True: pass" keeps spinning and burning a
    full CPU core even after execute_code() gives up waiting on it — dies
    with this child process instead of surviving for the rest of the test
    session. Reports the outcome back via a Queue since the parent can't
    just inspect a raised exception across the process boundary."""
    agent = PythonAgent(client=object())
    try:
        agent.execute_code("while True:\n    pass", csv_path)
        result_queue.put(("no_exception", None))
    except Exception as e:
        result_queue.put(("raised", str(e)))


def test_timeout_on_hanging_code(csv_path):
    result_queue = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_run_hanging_code_and_report, args=(csv_path, result_queue)
    )

    start = time.time()
    process.start()
    process.join(EXEC_TIMEOUT_SECONDS + 5)
    elapsed = time.time() - start

    assert elapsed < EXEC_TIMEOUT_SECONDS + 5, "execute_code()'s own timeout should fire first"

    outcome, detail = result_queue.get(timeout=1)
    process.terminate()  # the child's zombie worker thread dies with it
    process.join()

    assert outcome == "raised"
    assert "time limit" in detail
