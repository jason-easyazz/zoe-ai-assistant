#!/usr/bin/env python3
"""Record review evidence for a Zoe engineering pipeline run."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))

from pipeline_evidence_commands import main


if __name__ == "__main__":
    raise SystemExit(main(["mark-reviewed", *sys.argv[1:]]))
