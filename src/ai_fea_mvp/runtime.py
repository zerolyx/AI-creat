from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def bundled_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return PROJECT_ROOT


def writable_root() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / "AI-FEA-Assistant"
    return PROJECT_ROOT


def find_calculix() -> Path:
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
