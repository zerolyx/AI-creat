from __future__ import annotations

"""Test helper for locating the CalculiX (ccx) solver.

The solver runtime is intentionally not committed to the repository
(see scripts/fetch-ccx.ps1). These helpers let solver-dependent tests
skip cleanly when no solver is present, and run for real when one is.

Resolution order:
  1. $CCX_PATH  — explicit override (CI or developer)
  2. runtime/ccx/ccx.exe — bundled Windows runtime
  3. ``ccx`` on PATH  — e.g. Ubuntu ``apt install calculix-ccx`` (/usr/bin/ccx)
"""

import os
import shutil
import unittest
from functools import wraps
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def find_ccx() -> Path | None:
    explicit = os.environ.get("CCX_PATH")
    if explicit:
        candidate = Path(explicit)
        if candidate.exists():
            return candidate
    bundled = PROJECT_ROOT / "runtime" / "ccx" / "ccx.exe"
    if bundled.exists():
        return bundled
    on_path = shutil.which("ccx")
    if on_path:
        return Path(on_path)
    return None


def requires_solver(test_func):
    """Skip a test when no CalculiX solver binary is available."""
    reason = (
        "CalculiX solver not found (set $CCX_PATH, run scripts/fetch-ccx.ps1,"
        " or install ccx on PATH)"
    )

    @wraps(test_func)
    def wrapper(*args, **kwargs):
        if find_ccx() is None:
            raise unittest.SkipTest(reason)
        return test_func(*args, **kwargs)

    return wrapper
