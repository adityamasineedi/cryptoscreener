#!/usr/bin/env python3
"""Back-compat wrapper — prefer scripts/select_combo02_candidates.py."""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "select_combo02_candidates.py"
    runpy.run_path(str(target), run_name="__main__")
