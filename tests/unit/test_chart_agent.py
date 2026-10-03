"""
Tests for src/agents/chart_agent.py's Phase 4 redesign — deterministic
chart-data prep and rendering, driven by a small LLM-chosen spec instead of
LLM-authored matplotlib code (see chart_agent.py's docstring and
docs/BUGS_FOUND.md #8 for why).

Covers:
- prepare_chart_data(): aggregation correctness, the "none but has
  duplicates -> self-correct to sum" fix, top-N truncation, and edge cases
  (empty data, single category, all-identical values).
- Every renderer (bar/line/pie/scatter/histogram): each actually produces a
  matplotlib figure with a title, and bar/line/scatter get x/y axis labels.
- ChartAgent.run() end-to-end via a fake client, including the chart-type
  fallback when the LLM returns something outside the known enum.
"""
import json

import matplotlib.pyplot as plt
import pandas as pd
import pytest

from src.agents.chart_agent import (
    ChartAgent,
    MAX_CATEGORIES,
    prepare_chart_data,
    render_bar,
    render_chart,
    render_histogram,
    render_line,
    render_pie,
    render_scatter,
)


@pytest.fixture(autouse=True)
def _close_figures_after_each_test():
    """Every render_* call opens a matplotlib figure via plt.subplots() but
    never closes it (ChartAgent.run() closes it after saving, but tests
    calling renderers directly don't go through run()) — close everything
    after each test so figures don't accumulate across the whole suite."""
    yield
    plt.close("all")


# ---------------------------------------------------------------------------
# prepare_chart_data()
# ---------------------------------------------------------------------------

def test_sum_aggregation_groups_correctly():
    df = pd.DataFrame({"region": ["North", "South", "North"], "revenue": [100, 50, 200]})
    labels, values, truncated, total_count = prepare_chart_data(df, "region", "revenue", "sum")

    assert dict(zip(labels, values)) == {"North": 300, "South": 50}
    assert not truncated
    assert total_count == 2


def test_mean_aggregation():
    df = pd.DataFrame({"region": ["North", "North"], "revenue": [100, 300]})
    labels, values, truncated, total_count = prepare_chart_data(df, "region", "revenue", "mean")

    assert values[0] == 200


def test_count_aggregation():
    df = pd.DataFrame({"region": ["North", "North", "South"], "revenue": [1, 2, 3]})
    labels, values, truncated, total_count = prepare_chart_data(df, "region", "revenue", "count")

    assert dict(zip(labels, values)) == {"North": 2, "South": 1}


def test_invented_y_column_falls_back_to_counting_rows():
    """Regression test for Bug A (docs/BUGS_FOUND.md): the LLM has no
    reliable way to name a "count" column for a count-shaped question and
    invents placeholder names ("count", "employee_count", ...) that don't
    exist in the raw dataframe. Rather than special-case specific guessed
    names (which doesn't converge — a retry can invent a different one
    each time), any y_column that isn't a real column falls back to
    counting rows per x_column group, without ever dereferencing the
    invented name."""
    df = pd.DataFrame({"city": ["Mumbai", "Mumbai", "Delhi"]})
    labels, values, truncated, total_count = prepare_chart_data(
        df, "city", "some_totally_invented_column_name", "none"
    )

    assert dict(zip(labels, values)) == {"Mumbai": 2, "Delhi": 1}


def test_none_aggregation_when_x_column_genuinely_unique():
    """When x_column has no duplicates, "none" is honored literally — one
    row per x value already, nothing to group."""
    df = pd.DataFrame({"date": ["2024-01-01", "2024-01-02"], "revenue": [100, 200]})
    labels, values, truncated, total_count = prepare_chart_data(df, "date", "revenue", "none")

    assert list(values) == [100, 200]
    assert total_count == 2


def test_none_aggregation_self_corrects_to_sum_when_x_column_has_duplicates():
    """Regression test for the real bug found via manual testing on
    large_sales.csv: the LLM chose aggregation="none" for a column that
    actually had duplicates in the raw file, which silently ranked
    individual rows instead of per-category totals. See
    docs/BUGS_FOUND.md and chart_agent.py's prepare_chart_data docstring."""
    df = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-01", "2024-01-02"],
        "revenue": [100, 50, 10],
    })
    labels, values, truncated, total_count = prepare_chart_data(df, "date", "revenue", "none")

    # Correct: 2024-01-01 sums to 150 (100+50), not shown as two separate
    # rows of 100 and 50.
    assert total_count == 2
    assert dict(zip(labels, values))["2024-01-01"] == 150


def test_explicit_aggregation_is_not_overridden_by_the_self_correction():
    """The self-correction only kicks in for "none" — an explicit "sum" (or
    any other real aggregation) should never be silently changed."""
    df = pd.DataFrame({"region": ["North", "North"], "revenue": [100, 300]})
    labels, values, truncated, total_count = prepare_chart_data(df, "region", "revenue", "max")

    assert values[0] == 300  # max, not overridden to sum


def test_top_n_truncation_when_more_than_max_categories():
    df = pd.DataFrame({
        "id": [f"item_{i}" for i in range(20)],
        "value": list(range(20)),
    })
    labels, values, truncated, total_count = prepare_chart_data(df, "id", "value", "sum")

    assert truncated is True
    assert total_count == 20
    assert len(labels) == MAX_CATEGORIES
    # nlargest — the highest 15 values (5..19) should be kept, not the first 15
    assert min(values) == 5


def test_no_truncation_when_categories_fit():
    df = pd.DataFrame({"id": [f"item_{i}" for i in range(10)], "value": list(range(10))})
    labels, values, truncated, total_count = prepare_chart_data(df, "id", "value", "sum")

    assert truncated is False
    assert len(labels) == 10


def test_single_category():
    df = pd.DataFrame({"region": ["North", "North", "North"], "revenue": [10, 20, 30]})
    labels, values, truncated, total_count = prepare_chart_data(df, "region", "revenue", "sum")

    assert labels == ["North"]
    assert values[0] == 60
    assert not truncated


def test_all_identical_values():
    """Every category has the same total — shouldn't crash on a zero value
    range (this is exactly the kind of edge case a fixed-value-range
    computation like "5% of max-min" would divide by zero on if not
    guarded, even though that specific guard was removed from the final
    simplified version — prepare_chart_data itself has no such division,
    but this proves the whole pipeline tolerates it regardless)."""
    df = pd.DataFrame({"region": ["North", "South", "East"], "revenue": [100, 100, 100]})
    labels, values, truncated, total_count = prepare_chart_data(df, "region", "revenue", "sum")

    assert list(values) == [100, 100, 100]


def test_empty_dataframe():
    df = pd.DataFrame({"region": [], "revenue": []})
    labels, values, truncated, total_count = prepare_chart_data(df, "region", "revenue", "sum")

    assert labels == []
    assert total_count == 0
    assert not truncated


# ---------------------------------------------------------------------------
# Renderers — each should produce a figure with the expected elements
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "renderer",
    [render_bar, render_line, render_scatter],
)
def test_renderer_sets_title_and_axis_labels(renderer):
    fig_before = plt.get_fignums()
    renderer(["A", "B"], [10, 20], "My Title", False, 2, x_label="Category", y_label="Value")
    ax = plt.gca()

    assert ax.get_title() == "My Title"
    assert ax.get_xlabel() == "Category"
    assert ax.get_ylabel() == "Value"
    assert len(plt.get_fignums()) > len(fig_before)


def test_render_bar_shows_truncation_in_title():
    render_bar(["A", "B"], [10, 20], "Revenue", True, 50, x_label="x", y_label="y")
    ax = plt.gca()

    assert f"Top {MAX_CATEGORIES} of 50" in ax.get_title()


def test_render_bar_adds_value_labels_when_within_max_categories():
    render_bar(["A", "B"], [10, 20], "Revenue", False, 2, x_label="x", y_label="y")
    ax = plt.gca()

    # annotate() calls show up as Text artists beyond the axis tick labels
    assert len(ax.texts) == 2


def test_render_bar_skips_value_labels_when_too_many_bars():
    labels = [f"c{i}" for i in range(MAX_CATEGORIES + 1)]
    values = list(range(MAX_CATEGORIES + 1))
    render_bar(labels, values, "Revenue", True, MAX_CATEGORIES + 1, x_label="x", y_label="y")
    ax = plt.gca()

    assert len(ax.texts) == 0


def test_render_pie_uses_labels_in_legend_not_on_wedges():
    render_pie(["A", "B"], [30, 70], "Split", False, 2)
    ax = plt.gca()

    assert ax.get_title() == "Split"
    assert len(ax.patches) == 2  # two wedges
    legend = ax.get_legend()
    assert legend is not None
    legend_labels = [text.get_text() for text in legend.get_texts()]
    assert legend_labels == ["A", "B"]


def test_render_pie_wedges_have_white_border():
    render_pie(["A", "B"], [30, 70], "Split", False, 2)
    ax = plt.gca()

    for wedge in ax.patches:
        assert wedge.get_edgecolor()[:3] == (1.0, 1.0, 1.0)  # white, RGB (ignore alpha)


def test_render_histogram_uses_value_column_as_x_label():
    render_histogram([], [1, 2, 2, 3, 3, 3, 4], "Distribution", False, 7, x_label="category", y_label="revenue")
    ax = plt.gca()

    assert ax.get_xlabel() == "revenue"
    assert ax.get_ylabel() == "Frequency"


def test_render_histogram_bin_count_does_not_crash_on_tiny_input():
    """min(20, max(5, len(values)//2 or 1)) — with 1 value, len//2 is 0,
    which the "or 1" must catch, otherwise np.histogram(bins=0) raises."""
    render_histogram([], [42], "One value", False, 1, x_label="x", y_label="y")
    # no exception is the assertion here


def test_render_chart_dispatches_to_correct_renderer():
    render_chart("pie", ["A", "B"], [1, 2], "T", False, 2)
    ax = plt.gca()
    assert len(ax.patches) == 2  # pie wedges, not bars


def test_render_chart_falls_back_to_bar_for_unknown_type():
    render_chart("not_a_real_type", ["A"], [1], "T", False, 1)
    ax = plt.gca()
    assert len(ax.patches) == 1  # a bar, not a crash


# ---------------------------------------------------------------------------
# ChartAgent.run() end-to-end via a fake client
# ---------------------------------------------------------------------------

class _FakeChartClient:
    """Minimal stand-in for tests in this file only — returns a fixed chart
    spec regardless of prompt content, unlike the shared FakeGroqClient in
    conftest.py which content-sniffs across all four agents."""

    def __init__(self, spec: dict):
        self._spec = spec
        self.chat = self
        self.completions = self

    def create(self, model, messages, temperature=0.1, **kwargs):
        content = json.dumps(self._spec)
        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


def test_chart_agent_run_produces_a_png(tmp_path, sample_csv_path):
    import shutil
    csv_path = tmp_path / "sample.csv"
    shutil.copy(sample_csv_path, csv_path)

    spec = {"chart_type": "bar", "x_column": "region", "y_column": "revenue", "aggregation": "sum", "title": "Revenue by Region"}
    agent = ChartAgent(client=_FakeChartClient(spec), settings=type("S", (), {"charts_dir": str(tmp_path)})())

    chart_path = agent.run("Show revenue by region", "irrelevant printed result", str(csv_path))

    assert (tmp_path / chart_path.split("/")[-1]).exists()


def test_chart_agent_run_falls_back_to_bar_for_invalid_chart_type(tmp_path, sample_csv_path):
    import shutil
    csv_path = tmp_path / "sample.csv"
    shutil.copy(sample_csv_path, csv_path)

    spec = {"chart_type": "pyramid_scheme", "x_column": "region", "y_column": "revenue", "aggregation": "sum", "title": "T"}
    agent = ChartAgent(client=_FakeChartClient(spec), settings=type("S", (), {"charts_dir": str(tmp_path)})())

    # _get_chart_spec() itself coerces unknown chart_type to "bar", so this
    # should succeed rather than raise or silently produce nothing.
    chart_path = agent.run("Show revenue by region", "irrelevant printed result", str(csv_path))

    assert (tmp_path / chart_path.split("/")[-1]).exists()


def test_chart_agent_run_raises_on_missing_column(tmp_path, sample_csv_path):
    import shutil
    csv_path = tmp_path / "sample.csv"
    shutil.copy(sample_csv_path, csv_path)

    spec = {"chart_type": "bar", "x_column": "this_column_does_not_exist", "y_column": "revenue", "aggregation": "sum", "title": "T"}
    agent = ChartAgent(client=_FakeChartClient(spec), settings=type("S", (), {"charts_dir": str(tmp_path)})())

    with pytest.raises(Exception, match="Chart agent failed"):
        agent.run("Show revenue by region", "irrelevant printed result", str(csv_path))


def test_chart_filename_never_contains_session_id(tmp_path, sample_csv_path):
    """Chart files are served over HTTP (main.py's /charts mount) —
    naming one after session_id would leak that session's access key to
    anyone who saw the chart's URL. See docs/THREAT_MODEL.md."""
    import shutil
    csv_path = tmp_path / "sample.csv"
    shutil.copy(sample_csv_path, csv_path)

    session_id = "s3cr3t-session-id"
    spec = {"chart_type": "bar", "x_column": "region", "y_column": "revenue", "aggregation": "sum", "title": "T"}
    agent = ChartAgent(client=_FakeChartClient(spec), settings=type("S", (), {"charts_dir": str(tmp_path)})())

    chart_path = agent.run(
        "Show revenue by region", "irrelevant printed result", str(csv_path), session_id=session_id
    )

    assert session_id not in chart_path


def test_two_chart_runs_in_the_same_session_get_different_filenames(tmp_path, sample_csv_path):
    """Chart filenames used to be derived from session_id, so a follow-up
    chart in the same session silently overwrote the previous one."""
    import shutil
    csv_path = tmp_path / "sample.csv"
    shutil.copy(sample_csv_path, csv_path)

    spec = {"chart_type": "bar", "x_column": "region", "y_column": "revenue", "aggregation": "sum", "title": "T"}
    agent = ChartAgent(client=_FakeChartClient(spec), settings=type("S", (), {"charts_dir": str(tmp_path)})())

    first = agent.run("Show revenue by region", "result", str(csv_path), session_id="same-session")
    second = agent.run("Show revenue by region again", "result", str(csv_path), session_id="same-session")

    assert first != second
