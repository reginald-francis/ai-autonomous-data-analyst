import json
import logging
from uuid import uuid4
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
from src.services.llm_service import get_llm_client, get_model_for_complexity
from src.config import get_settings

logger = logging.getLogger(__name__)

# Sequential-hue "600" step from the project's chart color reference — a
# deeper, validated dark blue for single-series fills (darker than the
# "500" step used before, still inside the validated ramp rather than an
# arbitrary hex). See dataviz skill's references/palette.md.
BAR_COLOR = "#184f95"
LINE_COLOR = "#184f95"

# A chart with more categories than this is unreadable regardless of figure
# size — cap at the top N by value instead of plotting everything.
MAX_CATEGORIES = 15

CHART_TYPES = ("bar", "line", "pie", "scatter", "histogram")
AGGREGATIONS = ("sum", "mean", "count", "max", "min", "none")


class ChartAgent:
    """
    Redesigned (Phase 4) from "LLM writes a full matplotlib program" to "LLM
    picks a small chart spec; deterministic code renders it." The original
    approach put unbounded surface area for bugs in front of the model —
    every added prompt rule (top-N selection, label-collision avoidance,
    rotation) was itself a new way for the model to write something
    creative-but-broken; see docs/BUGS_FOUND.md #8 for the concrete crash
    (the model reparsing its own already-usable input via a removed pandas
    kwarg) that motivated this redesign.

    Now the LLM only chooses a handful of enum/column values (chart_type,
    x_column, y_column, aggregation, title) — see _get_chart_spec(). All the
    fragile mechanics (top-N truncation, label-collision detection, per-type
    rendering) are hand-written, deterministic, and unit-testable, the same
    way data_context_service.py is. They never regenerate per-request, so
    they can only be as broken as the last time they were tested.
    """

    def __init__(self, client=None, settings=None):
        self.client = client if client is not None else get_llm_client()
        self.model = get_model_for_complexity("medium")
        self.charts_dir = (settings if settings is not None else get_settings()).charts_dir

    def _get_chart_spec(self, question: str, data: str, data_context: str = "") -> dict:
        """The one LLM call this agent makes: pick a chart type and which
        columns to use, from a small closed vocabulary. This is a
        constrained decision (a handful of enum/column choices), not a
        program — deliberately, to keep the model's failure surface small."""
        prompt = f"""
        You are choosing how to visualize already-computed data. You are NOT writing code.

        The following data has already been computed and is the final result:
        {data}

        For reference, the underlying dataset looks like this:
        {data_context}

        The user is asking: {question}

        Respond ONLY with a JSON object choosing:
        - chart_type: one of {list(CHART_TYPES)}
        - x_column: the column name to use for categories/x-axis (from the computed data above).
          For a histogram, there is no grouping column — set x_column to the same value as
          y_column.
        - y_column: the column name to use for values/y-axis (from the computed data above).
          For a histogram, this is the single numeric column whose distribution is being shown.
        - aggregation: one of {list(AGGREGATIONS)} — how to combine rows that share the same
          x_column value. Use "none" if the computed data already has one row per x value, or
          for a histogram (a histogram bins raw values, it never groups by a category). Use
          "count" to count rows per x_column group (e.g. "how many employees per department") —
          when using "count", y_column is not used, so set it to the same value as x_column.
        - title: a short, clear chart title

        Respond with a single JSON object, nothing else:
        {{"chart_type": "...", "x_column": "...", "y_column": "...", "aggregation": "...", "title": "..."}}
        """

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": "You are a data visualization expert who responds only with a single JSON object."},
                {"role": "user", "content": prompt}
            ]
        )

        result = response.choices[0].message.content.replace("```json", "").replace("```", "").strip()
        spec = json.loads(result)

        if spec.get("chart_type") not in CHART_TYPES:
            spec["chart_type"] = "bar"
        if spec.get("aggregation") not in AGGREGATIONS:
            spec["aggregation"] = "none"

        return spec

    def run(self, question: str, data: str, file_path: str, complexity: str = "medium", session_id: str = None, data_context: str = "") -> str:
        self.model = get_model_for_complexity(complexity)
        logger.info(f"Chart agent running for question: {question} | complexity={complexity} | model={self.model}")

        # Always a fresh random name, never session_id — chart_path is served
        # over HTTP (see main.py's /charts mount), and session_id doubles as
        # the caller's access key to that session's data. Naming a chart
        # file after it would leak that key to anyone who saw the chart's
        # URL. See docs/THREAT_MODEL.md's "Chart URL exposure" section.
        chart_path = f"{self.charts_dir}/{uuid4().hex}.png"

        try:
            spec = self._get_chart_spec(question, data, data_context)
            logger.info(f"Chart spec: {spec}")

            df = pd.read_csv(file_path)

            if spec["x_column"] not in df.columns:
                raise Exception(
                    f"chart spec named x_column={spec['x_column']!r}, which is not a real "
                    f"column in the data (available: {list(df.columns)})"
                )

            if spec["chart_type"] == "histogram":
                # A histogram bins one column's raw value distribution —
                # there's no grouping column, so prepare_chart_data's
                # groupby-shaped pipeline doesn't apply here at all.
                raw_values = df[spec["y_column"]].dropna().tolist()
                labels, values, truncated, total_count = [], raw_values, False, len(raw_values)
            else:
                labels, values, truncated, total_count = prepare_chart_data(
                    df, spec["x_column"], spec["y_column"], spec["aggregation"]
                )

            render_chart(
                spec["chart_type"], labels, values, spec["title"], truncated, total_count,
                x_label=spec["x_column"], y_label=spec["y_column"],
            )
            plt.tight_layout()
            plt.savefig(chart_path)
            plt.close()

            logger.info(f"Chart saved to {chart_path}")
            return chart_path

        except Exception as e:
            plt.close()
            logger.error(f"Chart generation failed: {str(e)}")
            raise Exception(f"Chart agent failed: {str(e)}")


def prepare_chart_data(df: pd.DataFrame, x_column: str, y_column: str, aggregation: str):
    """The one data-prep pipeline every chart type shares — grouping and
    top-N truncation are identical regardless of whether the result gets
    drawn as a bar, line, or pie. Returns (labels, values, truncated,
    total_count) so a renderer never needs to know how truncation happened,
    only whether it did.

    This always operates on the raw file_path dataframe, not on the
    already-aggregated text the prior agent printed — the chart spec's
    aggregation choice is about *this* dataframe, which the LLM has no
    reliable way to know is already deduplicated by x_column or not. If it
    picked "none" but x_column actually has duplicates here, honoring that
    literally would silently rank individual rows instead of per-category
    totals while still labeling the chart as if it were properly grouped
    (found via manual testing on large_sales.csv: a "none" aggregation on a
    1000-row file with 652 unique dates produced a highest-15-transactions
    chart mislabeled as "Top 15 of 1000" dates). So "none" is only honored
    when x_column is genuinely already unique; otherwise fall back to sum,
    the most common real intent behind a groupby-shaped question.

    aggregation="count" is a special case: "count of rows per x_column
    group" needs no real y_column at all, but the LLM has no reliable way
    to name one — found via manual testing, "employee count by city"
    produced y_column="count", then a retry produced y_column=
    "employee_count" — different invented placeholders each time, neither
    a real column, both raising "Column not found: ...". Patching around
    one guessed name doesn't stop the model guessing another, so instead:
    if y_column isn't a real column in this dataframe at all, treat the
    request as count-shaped and never dereference the invented name."""
    if aggregation == "count" or y_column not in df.columns:
        grouped = df.groupby(x_column).size()
    else:
        if df[x_column].duplicated().any():
            aggregation = "sum" if aggregation == "none" else aggregation

        if aggregation != "none":
            grouped = df.groupby(x_column)[y_column].agg(aggregation)
        else:
            grouped = df.set_index(x_column)[y_column]

    total_count = len(grouped)
    truncated = total_count > MAX_CATEGORIES
    if truncated:
        grouped = grouped.nlargest(MAX_CATEGORIES)

    return grouped.index.astype(str).tolist(), grouped.values, truncated, total_count


def _compose_title(title: str, truncated: bool, total_count: int) -> str:
    if truncated:
        return f"{title} (Top {MAX_CATEGORIES} of {total_count})"
    return title


def _add_value_labels(ax, bars):
    for bar in bars:
        height = bar.get_height()
        ax.annotate(
            f"{height:,.0f}",
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
        )


def render_bar(labels, values, title, truncated, total_count, x_label="", y_label=""):
    fig, ax = plt.subplots(figsize=(12, 6))
    bars = ax.bar(labels, values, color=BAR_COLOR)

    ax.set_title(_compose_title(title, truncated, total_count))
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    plt.xticks(rotation=45, ha="right")

    if len(bars) <= MAX_CATEGORIES:
        _add_value_labels(ax, bars)


def render_line(labels, values, title, truncated, total_count, x_label="", y_label=""):
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(labels, values, color=LINE_COLOR, marker="o", linewidth=2)

    ax.set_title(_compose_title(title, truncated, total_count))
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    plt.xticks(rotation=45, ha="right")


def render_pie(labels, values, title, truncated, total_count, x_label="", y_label=""):
    fig, ax = plt.subplots(figsize=(9, 8))
    wedges, _, _ = ax.pie(
        values,
        autopct="%1.1f%%",
        wedgeprops={"edgecolor": "white", "linewidth": 1.5},
    )
    ax.set_title(_compose_title(title, truncated, total_count))
    ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))


def render_scatter(labels, values, title, truncated, total_count, x_label="", y_label=""):
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.scatter(labels, values, color=BAR_COLOR)
    ax.set_title(_compose_title(title, truncated, total_count))
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    plt.xticks(rotation=45, ha="right")


def render_histogram(labels, values, title, truncated, total_count, x_label="", y_label=""):
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(values, color=BAR_COLOR, bins=min(20, max(5, len(values) // 2 or 1)))
    ax.set_title(_compose_title(title, truncated, total_count))
    ax.set_xlabel(y_label)  # histogram bins the *value* column on the x-axis
    ax.set_ylabel("Frequency")


RENDERERS = {
    "bar": render_bar,
    "line": render_line,
    "pie": render_pie,
    "scatter": render_scatter,
    "histogram": render_histogram,
}


def render_chart(chart_type: str, labels, values, title, truncated, total_count, x_label="", y_label=""):
    renderer = RENDERERS.get(chart_type, render_bar)
    renderer(labels, values, title, truncated, total_count, x_label, y_label)
