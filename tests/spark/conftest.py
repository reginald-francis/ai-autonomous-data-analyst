"""Shared fixtures for the Spark tests (lakehouse/), which run in their own CI job.

Spark tests need PySpark and Java, which only the Spark environment has (WSL on
the personal laptop, and the `spark` CI job). On a machine without PySpark - the
corporate laptop running the ordinary suite - the test files in this folder are
skipped at collection instead of failing on `import pyspark`.

Run them with the flag below. tests/conftest.py is the app's and imports fastapi
and src.main; the Spark environment (WSL, the `spark` CI job) does not install
those, so --confcutdir stops pytest loading any conftest.py above this folder:

    python -m pytest tests/spark --confcutdir=tests/spark -v
"""

import importlib.util

import pytest

# collect_ignore_glob is a pytest convention: any file matching a pattern listed
# here is not collected. Checking with find_spec avoids importing pyspark just to
# find out whether it is installed.
if importlib.util.find_spec("pyspark") is None:
    collect_ignore_glob = ["test_*.py"]


@pytest.fixture(scope="session")
def spark():
    """One Delta-enabled SparkSession shared by every Spark test.

    Starting Spark launches a Java process and takes several seconds, so it is
    created once per test run (scope="session") and stopped after the last test.
    Small settings keep it light: two worker threads, few shuffle partitions, no
    web UI. The environment variables are set only while the session is built,
    because lakehouse/config.py reads them at that moment.
    """
    # Imported here, not at the top, so this file can load without pyspark.
    from lakehouse.spark_session import build_spark_session

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LAKEHOUSE_SPARK_MASTER", "local[2]")
        patch.setenv("LAKEHOUSE_SHUFFLE_PARTITIONS", "4")
        patch.setenv("LAKEHOUSE_DRIVER_MEMORY", "1g")
        patch.setenv("LAKEHOUSE_SPARK_UI", "false")
        session = build_spark_session("lakehouse-tests")

    yield session
    session.stop()


@pytest.fixture
def lake_root(tmp_path, monkeypatch):
    """A throwaway lake folder, so a test never writes into the real data/lake.

    Points LAKEHOUSE_LAKE_ROOT at pytest's temporary folder for the duration of
    one test and returns its path.
    """
    monkeypatch.setenv("LAKEHOUSE_LAKE_ROOT", str(tmp_path))
    return str(tmp_path)
