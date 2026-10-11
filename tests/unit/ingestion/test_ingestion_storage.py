"""Tests for ingestion.storage — bronze paths and atomic manifest I/O."""

import os

from ingestion import storage


def test_bronze_table_file_uses_hive_style_quarter_partition(tmp_path):
    path = storage.bronze_table_file(str(tmp_path), "num", "2026q2")
    assert path == os.path.join(
        str(tmp_path), "bronze", "sec_fsds", "num", "quarter=2026q2", "part-0.parquet"
    )


def test_staging_and_manifests_sit_beside_the_table_folders(tmp_path):
    root = str(tmp_path)
    assert storage.bronze_staging_dir(root, "2026q2") == os.path.join(
        root, "bronze", "sec_fsds", "_staging", "2026q2"
    )
    assert storage.manifest_path(root, "2026q2") == os.path.join(
        root, "bronze", "sec_fsds", "_manifests", "2026q2.json"
    )


def test_read_manifest_returns_none_when_missing(tmp_path):
    assert storage.read_manifest(str(tmp_path), "2026q2") is None


def test_write_then_read_manifest_round_trips(tmp_path):
    manifest = {"quarter": "2026q2", "sha256": "abc", "row_counts": {"sub": 2}}
    storage.write_manifest(str(tmp_path), "2026q2", manifest)
    assert storage.read_manifest(str(tmp_path), "2026q2") == manifest


def test_write_manifest_is_atomic_and_overwrites(tmp_path):
    storage.write_manifest(str(tmp_path), "2026q2", {"sha256": "old"})
    path = storage.write_manifest(str(tmp_path), "2026q2", {"sha256": "new"})

    assert storage.read_manifest(str(tmp_path), "2026q2") == {"sha256": "new"}
    assert not os.path.exists(path + ".tmp")
