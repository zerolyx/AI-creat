from __future__ import annotations

import sys
import os
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_fea_mvp.gui import MainWindow


def main() -> int:
    app = QApplication([])
    window = MainWindow()
    window.show()
    source = os.environ.get("AI_FEA_P7")
    if source:
        window.step_path.setText(source)
        window._auto_fill_from_step(Path(source))
    QTimer.singleShot(0, window._start_analysis)

    def poll() -> None:
        if window._process is None and (window._last_summary is not None or window.log.toPlainText()):
            app.quit()
        else:
            QTimer.singleShot(100, poll)

    QTimer.singleShot(250, poll)
    app.exec()
    if window._last_summary is None:
        raise AssertionError(f"GUI analysis did not finish successfully:\n{window.log.toPlainText()}")
    if window._last_summary.solver_returncode != 0:
        raise AssertionError("GUI analysis returned a non-zero solver code")
    if window._last_summary.case.application_mode == "球阀装配夹爪":
        if window._last_summary.case.force_n < 390:
            raise AssertionError("AI gripper load was not applied to the FEA case")
        if window._last_summary.case.load_direction != "-Y":
            raise AssertionError("Negative load direction was not preserved")
    if window._last_summary.report_path is None or not window._last_summary.report_path.exists():
        raise AssertionError("Chinese Markdown report was not generated")
    if window.canvas.fields is None or not window.canvas.fields.surface_faces:
        raise AssertionError("Real contour field data was not loaded")
    print(window._last_summary.workdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
