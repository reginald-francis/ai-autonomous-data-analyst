"""Checks that the two seed files in lakehouse/seeds/ are well formed and agree.

The concept map matches SEC tag names exactly, so a typo or a metric name missing
from metrics.csv would silently map nothing. These tests catch that before the
silver job ever runs. Pure Python (the csv module): no Spark is started.
"""

import csv
from collections import defaultdict
from pathlib import Path

import pytest

import lakehouse

pytestmark = pytest.mark.unit

SEEDS_DIR = Path(lakehouse.__file__).parent / "seeds"

METRIC_COLUMNS = ["metric", "description", "statement", "period_type", "resolution_rule"]
CONCEPT_COLUMNS = ["metric", "taxonomy", "tag", "priority"]
STATEMENTS = {"IS", "BS", "CF"}
PERIOD_TYPES = {"flow", "instant"}
RESOLUTION_RULES = {"largest", "priority"}
TAXONOMIES = {"us-gaap", "ifrs"}


def read_seed(name):
    """Return (column names, rows as dicts) for one seed CSV."""
    with open(SEEDS_DIR / name, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


@pytest.fixture(scope="module")
def metrics():
    return read_seed("metrics.csv")[1]


@pytest.fixture(scope="module")
def concept_map():
    return read_seed("concept_map.csv")[1]


def test_metrics_has_the_expected_columns():
    assert read_seed("metrics.csv")[0] == METRIC_COLUMNS


def test_concept_map_has_the_expected_columns():
    assert read_seed("concept_map.csv")[0] == CONCEPT_COLUMNS


def test_metric_names_are_unique(metrics):
    names = [row["metric"] for row in metrics]
    assert len(names) == len(set(names))


def test_metrics_use_only_allowed_values(metrics):
    for row in metrics:
        assert row["statement"] in STATEMENTS, row
        assert row["period_type"] in PERIOD_TYPES, row
        assert row["resolution_rule"] in RESOLUTION_RULES, row


def test_balance_sheet_metrics_are_instant_and_the_rest_are_flows(metrics):
    for row in metrics:
        expected = "instant" if row["statement"] == "BS" else "flow"
        assert row["period_type"] == expected, row


def test_no_value_has_stray_whitespace(metrics, concept_map):
    for row in metrics + concept_map:
        for value in row.values():
            assert value == value.strip(), row


def test_every_concept_map_metric_is_defined_in_metrics(metrics, concept_map):
    defined = {row["metric"] for row in metrics}
    for row in concept_map:
        assert row["metric"] in defined, row


def test_every_metric_has_at_least_one_tag(metrics, concept_map):
    mapped = {row["metric"] for row in concept_map}
    for row in metrics:
        assert row["metric"] in mapped, row


def test_taxonomies_are_allowed(concept_map):
    for row in concept_map:
        assert row["taxonomy"] in TAXONOMIES, row


def test_a_taxonomy_and_tag_pair_maps_to_one_metric_only(concept_map):
    pairs = [(row["taxonomy"], row["tag"]) for row in concept_map]
    assert len(pairs) == len(set(pairs))


def test_priorities_run_from_one_without_gaps_per_metric_and_taxonomy(concept_map):
    groups = defaultdict(list)
    for row in concept_map:
        groups[(row["metric"], row["taxonomy"])].append(int(row["priority"]))
    for group, priorities in groups.items():
        assert sorted(priorities) == list(range(1, len(priorities) + 1)), group
