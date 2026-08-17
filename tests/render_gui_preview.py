from __future__ import annotations

import sys
import os
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_fea_mvp.gui import MainWindow
from ai_fea_mvp.cli import load_run_summary


app = QApplication([])
window = MainWindow()
window.resize(1440, 900)
window.show()
source = os.environ.get("AI_FEA_P7")
if source:
    window.step_path.setText(source)
    window._auto_fill_from_step(Path(source))
summary_path = os.environ.get("AI_FEA_SUMMARY")
if summary_path:
    window._analysis_finished(load_run_summary(Path(summary_path)))
if os.environ.get("AI_FEA_FIELD") == "displacement":
    window._set_field_mode("displacement")
if os.environ.get("AI_FEA_THEME") == "light":
    window._toggle_theme()
if os.environ.get("AI_FEA_RUNNING") == "1":
    window.canvas.set_running(True)
    window.automation_track.set_stage(2)
output = Path(os.environ.get("AI_FEA_SCREENSHOT", ROOT / "docs" / "gui_preview.png"))
delay = int(os.environ.get("AI_FEA_SCREENSHOT_DELAY", "1100" if summary_path else "180"))
QTimer.singleShot(delay, lambda: (window.grab().save(str(output)), app.quit()))
app.exec()
print(output)
