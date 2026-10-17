"""Phase 7 lakehouse: PySpark + Delta Lake jobs that turn bronze Parquet into silver and gold.

A separate deployable from src/ (the serving app) and ingestion/ (the SEC
downloader): its own requirements file (requirements-spark.txt), its own CI job,
and nothing imported from either of them - see docs/PHASE7_DESIGN.md D12. This
file is intentionally only a docstring: its presence is what makes `lakehouse` an
importable Python package.
"""
