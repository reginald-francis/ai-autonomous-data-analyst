"""Enables `python -m ingestion`."""

import sys

from ingestion.cli import main

if __name__ == "__main__":
    sys.exit(main())
