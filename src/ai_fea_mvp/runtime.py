from __future__ import annotations

"""Runtime path resolution.

Resolves the project/bundled root and the CalculiX solver location for both
source-tree use (``python run_gui.py``) and frozen PyInstaller EXE deployment
(``sys.frozen`` / ``_MEIPASS``). Used by the packaging spec and by solver
launch so the same code runs from source and from ``dist/AI-FEA-Valve-Gripper.exe``.
"""

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def bundled_root() -> Path:
    """Root directory of the bundled app (PyInstaller _MEIPASS when frozen, repo root in source)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return PROJECT_ROOT


def writable_root() -> Path:
    """User-writable base directory for run outputs when frozen (LOCALAPPDATA), repo root in source."""
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / "AI-FEA-Assistant"
    return PROJECT_ROOT


def find_calculix() -> Path:
    """Locate the ccx solver binary checking bundled runtime and tools paths; raises if not found."""
    candidates = [
        bundled_root() / "runtime" / "ccx" / "ccx.exe",
        bundled_root()
        / "tools"
        / "CalculiX-2.22.0-win-x64"
        / "CalculiX-2.22.0-win-x64"
        / "bin"
        / "ccx.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "未找到 CalculiX。请将 ccx.exe 放入 runtime\\ccx，或使用源码目录中的 tools\\CalculiX...\\bin。"
    )
