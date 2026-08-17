from __future__ import annotations

"""PySide6 desktop GUI.

Chinese light/dark themed Windows front-end for the FEA assistant: STEP import,
analysis-case editing, headless run via the CLI worker subprocess, result cloud
rendering (displacement / von Mises) with max-value markers, and the generated
Markdown report. Depends on the rest of the package for all numerics — this
module stays focused on presentation and worker process management.
"""

import math
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QProcess,
    QProcessEnvironment,
    QTimer,
    Qt,
    QVariantAnimation,
    Slot,
)
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .cli import load_run_summary
from .geometry import StepGeometry, auto_case_for_step, inspect_step_geometry
from .gripper import CR605_RATED_PAYLOAD_KG, GripperDuty
from .models import BeamCase, FieldResults, RunSummary
from .materials import MATERIAL_PRESETS
from .results import parse_field_results
from .runtime import bundled_root, find_calculix, writable_root


FONT_FAMILY = "Noto Sans SC"
FIELD_COLORS = (
    "#143D8D",
    "#1769C2",
    "#16A6C8",
    "#28C58C",
    "#A5D43A",
    "#F2C84B",
    "#F08A3C",
    "#D9473F",
)


def _register_ui_font() -> str:
    font_path = bundled_root() / "assets" / "fonts" / "NotoSansSC-Regular.otf"
    if font_path.exists():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
    return FONT_FAMILY


def _mix_color(value: float) -> QColor:
    value = min(1.0, max(0.0, value))
    scaled = value * (len(FIELD_COLORS) - 1)
    left = min(int(scaled), len(FIELD_COLORS) - 2)
    ratio = scaled - left
    first = QColor(FIELD_COLORS[left])
    second = QColor(FIELD_COLORS[left + 1])
    return QColor(
        round(first.red() + (second.red() - first.red()) * ratio),
        round(first.green() + (second.green() - first.green()) * ratio),
        round(first.blue() + (second.blue() - first.blue()) * ratio),
    )


class ResultCanvas(QFrame):
    """Paint a real CalculiX surface field without an additional rendering dependency."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("resultCanvas")
        self.setMinimumHeight(430)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.case = BeamCase()
        self.geometry: StepGeometry | None = None
        self.summary: RunSummary | None = None
        self.fields: FieldResults | None = None
        self.mode = "stress"
        self.running = False
        self.dark = True
        self.error_message = ""
        self.reveal_progress = 1.0
        self.activity_phase = 0
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(45)
        self._activity_timer.timeout.connect(self._advance_activity)
        self._reveal_animation = QVariantAnimation(self)
        self._reveal_animation.setDuration(850)
        self._reveal_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reveal_animation.valueChanged.connect(self._set_reveal_progress)

    def set_case(self, case: BeamCase) -> None:
        self.case = case
        self.update()

    def set_geometry(self, geometry: StepGeometry | None) -> None:
        self.geometry = geometry
        self.update()

    def set_running(self, running: bool) -> None:
        self.running = running
        if running:
            self.activity_phase = 0
            self._activity_timer.start()
        else:
            self._activity_timer.stop()
        self.update()

    def set_theme(self, dark: bool) -> None:
        self.dark = dark
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    def set_summary(self, summary: RunSummary | None) -> None:
        self.summary = summary
        self.fields = None
        self.error_message = ""
        if summary is not None:
            try:
                self.fields = parse_field_results(summary.inp_path, summary.dat_path)
            except Exception as exc:
                self.error_message = f"云图读取失败：{type(exc).__name__}: {exc}"
            else:
                self.reveal_progress = 0.0
                self._reveal_animation.stop()
                self._reveal_animation.setStartValue(0.0)
                self._reveal_animation.setEndValue(1.0)
                self._reveal_animation.start()
        self.update()

    def _advance_activity(self) -> None:
        self.activity_phase = (self.activity_phase + 1) % 120
        self.update()

    @Slot(object)
    def _set_reveal_progress(self, value: object) -> None:
        self.reveal_progress = float(value)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg = QColor("#0A0D10" if self.dark else "#F3F5F7")
        painter.fillRect(self.rect(), bg)
        self._draw_grid(painter)
        if self.fields is not None and self.summary is not None:
            self._paint_field(painter)
        elif self.geometry is not None:
            self._paint_geometry_preview(painter)
        else:
            self._paint_empty(painter)
        if self.running:
            self._paint_running(painter)
        if self.error_message:
            self._text(painter, self.error_message, 28, self.height() - 28, 10, "#D9473F", True)

    def _draw_grid(self, painter: QPainter) -> None:
        color = QColor("#182027" if self.dark else "#DFE4E8")
        painter.setPen(QPen(color, 1))
        for x in range(32, self.width(), 48):
            painter.drawLine(x, 0, x, self.height())
        for y in range(32, self.height(), 48):
            painter.drawLine(0, y, self.width(), y)

    def _paint_empty(self, painter: QPainter) -> None:
        fg = "#EEF2F4" if self.dark else "#171B1F"
        muted = "#7E8A93" if self.dark else "#68737C"
        self._text(painter, "导入 STEP，开始理解你的结构", 30, 54, 18, fg, True)
        self._text(painter, "求解完成后，这里会显示真实应力与位移云图。", 30, 82, 11, muted)
        center = QPointF(self.width() / 2, self.height() / 2 + 16)
        painter.setPen(QPen(QColor("#34414A" if self.dark else "#C5CDD3"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(center.x() - 115, center.y() - 70, 230, 140))
        painter.drawLine(center.x() - 115, center.y() - 70, center.x() - 65, center.y() - 104)
        painter.drawLine(center.x() + 115, center.y() - 70, center.x() + 165, center.y() - 104)
        painter.drawLine(center.x() - 65, center.y() - 104, center.x() + 165, center.y() - 104)

    def _paint_geometry_preview(self, painter: QPainter) -> None:
        geometry = self.geometry
        if geometry is None:
            return
        projected: list[tuple[float, float, float, float]] = []
        for box in geometry.volume_boxes:
            x0, y0, z0, x1, y1, z1 = box
            projected.append((x0 + y0 * 0.28, z0 - y0 * 0.16, x1 + y1 * 0.28, z1 - y1 * 0.16))
        min_u = min(min(item[0], item[2]) for item in projected)
        max_u = max(max(item[0], item[2]) for item in projected)
        min_v = min(min(item[1], item[3]) for item in projected)
        max_v = max(max(item[1], item[3]) for item in projected)
        view = QRectF(48, 68, max(200, self.width() - 96), max(180, self.height() - 140))
        scale = min(view.width() / max(max_u - min_u, 1), view.height() / max(max_v - min_v, 1)) * 0.82
        offset_x = view.center().x() - (min_u + max_u) * 0.5 * scale
        offset_y = view.center().y() + (min_v + max_v) * 0.5 * scale
        fill = QColor("#27343D" if self.dark else "#DCE3E8")
        edge = QColor("#72828D" if self.dark else "#778691")
        painter.setPen(QPen(edge, 1.2))
        painter.setBrush(QBrush(fill))
        for u0, v0, u1, v1 in projected:
            rect = QRectF(
                offset_x + min(u0, u1) * scale,
                offset_y - max(v0, v1) * scale,
                max(abs(u1 - u0) * scale, 3),
                max(abs(v1 - v0) * scale, 3),
            )
            painter.drawRect(rect)
        fg = "#EEF2F4" if self.dark else "#171B1F"
        muted = "#8A969E" if self.dark else "#68737C"
        self._text(painter, f"已识别 {geometry.volume_count} 个实体", 28, 38, 13, fg, True)
        self._text(painter, "几何预览 · 等待真实求解", 28, self.height() - 24, 10, muted)

    def _paint_field(self, painter: QPainter) -> None:
        fields = self.fields
        summary = self.summary
        if fields is None or summary is None:
            return
        node_magnitudes = {
            node: math.sqrt(dx * dx + dy * dy + dz * dz)
            for node, (dx, dy, dz) in fields.displacements.items()
        }
        max_disp = max(node_magnitudes.values(), default=0.0)
        coords = fields.nodes
        xs = [value[0] for value in coords.values()]
        ys = [value[1] for value in coords.values()]
        zs = [value[2] for value in coords.values()]
        span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1.0)
        deformation_scale = min(10000.0, span * 0.12 / max(max_disp, 1.0e-12))

        def deformed(node: int) -> tuple[float, float, float]:
            x, y, z = coords[node]
            dx, dy, dz = fields.displacements.get(node, (0.0, 0.0, 0.0))
            return x + dx * deformation_scale, y + dy * deformation_scale, z + dz * deformation_scale

        deformed_nodes = {node: deformed(node) for node in coords}
        projections = {node: (x + y * 0.34, z - y * 0.20, y) for node, (x, y, z) in deformed_nodes.items()}
        us = [value[0] for value in projections.values()]
        vs = [value[1] for value in projections.values()]
        view = QRectF(42, 54, max(240, self.width() - 132), max(220, self.height() - 116))
        scale = min(view.width() / max(max(us) - min(us), 1), view.height() / max(max(vs) - min(vs), 1)) * 0.92
        offset_x = view.center().x() - (min(us) + max(us)) * 0.5 * scale
        offset_y = view.center().y() + (min(vs) + max(vs)) * 0.5 * scale

        if self.mode == "stress":
            values = fields.element_von_mises_mpa
            max_value = max(values.values(), default=1.0)
            unit = "MPa"
            label = "von Mises 应力"
        else:
            values = {}
            max_value = max(max_disp, 1.0e-12)
            unit = "mm"
            label = "总位移"

        faces = sorted(
            fields.surface_faces,
            key=lambda face: sum(projections[node][2] for node in face[:3]) / 3.0,
        )
        edge = QColor(10, 16, 20, 40) if self.dark else QColor(255, 255, 255, 42)
        painter.setPen(QPen(edge, 0.45))
        visible_count = max(1, int(len(faces) * self.reveal_progress))
        for n1, n2, n3, element_id in faces[:visible_count]:
            points = []
            for node in (n1, n2, n3):
                u, v, _depth = projections[node]
                points.append(QPointF(offset_x + u * scale, offset_y - v * scale))
            value = (
                values.get(element_id, 0.0)
                if self.mode == "stress"
                else sum(node_magnitudes.get(node, 0.0) for node in (n1, n2, n3)) / 3.0
            )
            painter.setBrush(QBrush(_mix_color(value / max_value)))
            painter.drawPolygon(QPolygonF(points))

        self._paint_legend(painter, label, max_value, unit, deformation_scale)
        self._paint_extreme_marker(
            painter, fields, projections, offset_x, offset_y, scale,
            summary.results.max_von_mises_element if self.mode == "stress" else summary.results.max_displacement_node,
        )

    def _paint_extreme_marker(
        self,
        painter: QPainter,
        fields: FieldResults,
        projections: dict[int, tuple[float, float, float]],
        offset_x: float,
        offset_y: float,
        scale: float,
        result_id: int,
    ) -> None:
        if self.reveal_progress < 0.82:
            return
        if self.mode == "stress":
            nodes = fields.elements.get(result_id, ())[:4]
            if not nodes:
                return
            u = sum(projections[node][0] for node in nodes) / len(nodes)
            v = sum(projections[node][1] for node in nodes) / len(nodes)
            label = f"MAX · 单元 {result_id}"
        else:
            if result_id not in projections:
                return
            u, v, _depth = projections[result_id]
            label = f"MAX · 节点 {result_id}"
        point = QPointF(offset_x + u * scale, offset_y - v * scale)
        color = QColor("#FFFFFF" if self.dark else "#13171A")
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(QColor("#D9473F"))
        painter.drawEllipse(point, 4.5, 4.5)
        end = QPointF(min(point.x() + 64, self.width() - 160), max(point.y() - 38, 58))
        painter.drawLine(point, end)
        self._text(painter, label, end.x() + 6, end.y() + 4, 9, color.name(), True)

    def _paint_legend(self, painter: QPainter, label: str, maximum: float, unit: str, scale: float) -> None:
        fg = "#EEF2F4" if self.dark else "#171B1F"
        muted = "#8A969E" if self.dark else "#68737C"
        self._text(painter, label, 24, 32, 12, fg, True)
        self._text(painter, f"变形显示 ×{scale:.0f}", 24, 51, 9, muted)
        bar_x = self.width() - 64
        bar_top = 72
        bar_h = max(150, self.height() - 145)
        steps = 80
        for index in range(steps):
            ratio = 1.0 - index / (steps - 1)
            painter.fillRect(QRectF(bar_x, bar_top + index * bar_h / steps, 13, bar_h / steps + 1), _mix_color(ratio))
        self._text(painter, f"{maximum:.4g}", bar_x - 7, bar_top - 10, 9, fg, True)
        self._text(painter, unit, bar_x - 3, bar_top + bar_h + 21, 9, muted)
        self._text(painter, "0", bar_x - 3, bar_top + bar_h + 5, 9, muted)

    def _paint_running(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor(8, 12, 15, 150))
        rect = QRectF(self.width() / 2 - 155, self.height() / 2 - 38, 310, 76)
        painter.setPen(QPen(QColor("#3A4852"), 1))
        painter.setBrush(QColor("#11171C"))
        painter.drawRoundedRect(rect, 6, 6)
        self._text(painter, "正在计算真实有限元结果", rect.left() + 30, rect.top() + 31, 13, "#F2F5F6", True)
        self._text(painter, "网格生成 → CalculiX 求解 → 云图", rect.left() + 30, rect.top() + 54, 9, "#8E9AA2")
        track = QRectF(rect.left() + 30, rect.bottom() - 10, rect.width() - 60, 2)
        painter.fillRect(track, QColor("#26323A"))
        pulse_x = track.left() + (self.activity_phase / 119.0) * track.width()
        painter.fillRect(QRectF(track.left(), track.top(), max(2, pulse_x - track.left()), 2), QColor("#3EC7B6"))

    @staticmethod
    def _text(painter: QPainter, text: str, x: float, y: float, size: int, color: str, bold: bool = False) -> None:
        font = QFont(FONT_FAMILY, size)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(color)))
        painter.drawText(QPointF(x, y), text)


class MetricCard(QFrame):
    def __init__(self, title: str, unit: str) -> None:
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("metricLabel")
        layout.addWidget(title_label)
        row = QHBoxLayout()
        row.setSpacing(7)
        self.value_label = QLabel("--")
        self.value_label.setObjectName("metricValue")
        unit_label = QLabel(unit)
        unit_label.setObjectName("metricUnit")
        row.addWidget(self.value_label)
        row.addWidget(unit_label)
        row.addStretch()
        layout.addLayout(row)
        self.note_label = QLabel("等待分析")
        self.note_label.setObjectName("metricNote")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)


class AutomationTrack(QFrame):
    STAGES = ("识别几何", "生成网格", "定义工况", "求解", "解读结果")

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("automationTrack")
        self.stage = -1
        self.stage_labels: list[QLabel] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(5)
        ai_label = QLabel("AI 自动分析")
        ai_label.setObjectName("aiBadge")
        layout.addWidget(ai_label)
        for index, text in enumerate(self.STAGES):
            if index:
                arrow = QLabel("›")
                arrow.setObjectName("trackArrow")
                layout.addWidget(arrow)
            label = QLabel(text)
            label.setObjectName("trackStage")
            self.stage_labels.append(label)
            layout.addWidget(label)
        layout.addStretch()
        self.set_stage(-1)

    def set_stage(self, stage: int) -> None:
        self.stage = stage
        for index, label in enumerate(self.stage_labels):
            label.setProperty("state", "done" if index < stage else "active" if index == stage else "idle")
            label.style().unpolish(label)
            label.style().polish(label)


class ReportDialog(QDialog):
    def __init__(self, report_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report_path = report_path
        self.setWindowTitle("中文有限元分析报告")
        self.resize(920, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        top = QHBoxLayout()
        title = QLabel("分析报告")
        title.setObjectName("reportTitle")
        top.addWidget(title)
        top.addStretch()
        open_external = QPushButton("在系统中打开")
        open_external.setObjectName("secondaryButton")
        open_external.clicked.connect(lambda: os.startfile(str(report_path)))
        top.addWidget(open_external)
        layout.addLayout(top)
        browser = QTextBrowser()
        browser.setObjectName("reportBrowser")
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(report_path.read_text(encoding="utf-8-sig", errors="replace"))
        layout.addWidget(browser, 1)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        global FONT_FAMILY
        FONT_FAMILY = _register_ui_font()
        QApplication.instance().setFont(QFont(FONT_FAMILY))
        self.setWindowTitle("极析 · 智能有限元分析")
        self.resize(1520, 940)
        self.setMinimumSize(1160, 760)
        self._process: QProcess | None = None
        self._process_output = ""
        self._current_workdir: Path | None = None
        self._auto_step_path: Path | None = None
        self._step_info: StepGeometry | None = None
        self._last_summary: RunSummary | None = None
        self._dark = True

        self.length = self._number(100.0, 1.0, 10000.0, 1)
        self.height = self._number(10.0, 0.1, 1000.0, 1)
        self.width = self._number(10.0, 0.1, 1000.0, 1)
        self.force = self._number(100.0, 0.001, 1_000_000.0, 2)
        self.young = self._number(3500.0, 1.0, 10_000_000.0, 0)
        self.poisson = self._number(0.36, 0.0, 0.499, 3)
        self.mesh_size = self._number(5.0, 0.2, 100.0, 1)
        self.allowable_stress = self._number(30.0, 0.0, 1000.0, 1)
        self.material_combo = QComboBox()
        self.material_combo.addItems(list(MATERIAL_PRESETS))
        self.material_combo.currentTextChanged.connect(self._apply_material_preset)
        self.load_direction = QComboBox()
        self.load_direction.addItems(["-Y", "+Y", "-X", "+X", "-Z", "+Z"])
        self.material_note = QLabel(MATERIAL_PRESETS["PLA"].note)
        self.material_note.setObjectName("materialNote")
        self.material_note.setWordWrap(True)
        self.automation_track = AutomationTrack()
        self.task_mode = QComboBox()
        self.task_mode.addItems(["球阀装配夹爪", "通用结构分析"])
        self.valve_model = QComboBox()
        self.valve_model.addItems(["Q41F-16P / 16RL", "Q41F-10P", "其他法兰球阀"])
        self.nominal_diameter = QComboBox()
        self.nominal_diameter.addItems(["DN15", "DN20", "DN25", "DN32", "DN40", "DN50", "DN65", "DN80"])
        self.nominal_diameter.setCurrentText("DN25")
        self.workpiece_mass = self._number(4.05, 0.01, 100.0, 2)
        self.gripper_mass = self._number(0.60, 0.01, 20.0, 2)
        self.dynamic_factor = self._number(1.50, 1.0, 5.0, 2)
        self.friction_coefficient = self._number(0.30, 0.05, 1.5, 2)
        self.grip_safety_factor = self._number(2.00, 1.0, 10.0, 2)
        self.assembly_force = self._number(100.0, 0.0, 10000.0, 1)
        self.robot_check = QLabel()
        self.robot_check.setObjectName("robotCheck")
        self.robot_check.setWordWrap(True)
        self.step_path = QLineEdit()
        self.step_path.setPlaceholderText("选择一个 STEP 模型")
        self.browse_step = QPushButton("选择模型")
        self.browse_step.setObjectName("secondaryButton")
        self.browse_step.clicked.connect(self._choose_step)
        self.run_button = QPushButton("AI 自动分析")
        self.run_button.setObjectName("runButton")
        self.run_button.clicked.connect(self._start_analysis)
        self.report_button = QPushButton("查看中文报告")
        self.report_button.setObjectName("reportButton")
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self._open_report)
        self.open_button = QPushButton("打开结果目录")
        self.open_button.setObjectName("secondaryButton")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_results)
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setText("浅色")
        self.theme_button.setToolTip("切换明暗主题")
        self.theme_button.clicked.connect(self._toggle_theme)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("eventStream")
        self.canvas = ResultCanvas()
        self.metric_deflection = MetricCard("最大总位移", "mm")
        self.metric_stress = MetricCard("最大等效应力", "MPa")
        self.metric_quality = MetricCard("结果状态", "")
        self.title_label = QLabel("开始一次结构分析")
        self.title_label.setObjectName("pageTitle")
        self.subtitle_label = QLabel("导入 STEP 后自动识别几何，并提供可修改的默认工况。")
        self.subtitle_label.setObjectName("pageSubtitle")
        self.model_meta = QLabel("尚未导入模型")
        self.model_meta.setObjectName("modelMeta")
        self.conclusion_label = QLabel("完成求解后，这里会用一句话说明结果。")
        self.conclusion_label.setObjectName("conclusion")
        self.conclusion_label.setWordWrap(True)
        self.boundary_text = QLabel("自动固定：每个实体最小 X 端\n自动载荷：每个实体最大 X 端，沿 -Y\n这是快速评估工况，结果前请复核实际支撑。")
        self.boundary_text.setObjectName("helperText")
        self.boundary_text.setWordWrap(True)
        self._build_ui()
        self._apply_styles()
        self._sync_preview()
        for box in (self.length, self.height, self.width, self.force, self.young, self.poisson, self.mesh_size, self.allowable_stress):
            box.valueChanged.connect(self._sync_preview)
        for box in (self.workpiece_mass, self.gripper_mass, self.dynamic_factor, self.friction_coefficient, self.grip_safety_factor, self.assembly_force):
            box.valueChanged.connect(self._update_gripper_duty)
        self.valve_model.currentTextChanged.connect(self._update_gripper_duty)
        self.nominal_diameter.currentTextChanged.connect(self._update_gripper_duty)
        self._update_gripper_duty()

    @staticmethod
    def _number(value: float, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setDecimals(decimals)
        box.setValue(value)
        box.setSingleStep(1.0 if decimals < 2 else 0.1)
        return box

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 12, 24, 12)
        brand = QLabel("极析")
        brand.setObjectName("brand")
        header_layout.addWidget(brand)
        descriptor = QLabel("智能有限元分析")
        descriptor.setObjectName("brandDescriptor")
        header_layout.addWidget(descriptor)
        header_layout.addStretch()
        solver = QLabel("CalculiX 就绪")
        solver.setObjectName("solverStatus")
        header_layout.addWidget(solver)
        header_layout.addSpacing(16)
        header_layout.addWidget(self.theme_button)
        outer.addWidget(header)
        outer.addWidget(self.automation_track)

        content = QHBoxLayout()
        content.setContentsMargins(24, 20, 20, 20)
        content.setSpacing(20)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)
        title_row = QHBoxLayout()
        title_column = QVBoxLayout()
        title_column.setSpacing(3)
        title_column.addWidget(self.title_label)
        title_column.addWidget(self.subtitle_label)
        title_row.addLayout(title_column)
        title_row.addStretch()
        self.stress_mode = QToolButton()
        self.stress_mode.setText("应力")
        self.stress_mode.setCheckable(True)
        self.stress_mode.setChecked(True)
        self.stress_mode.setObjectName("modeButton")
        self.displacement_mode = QToolButton()
        self.displacement_mode.setText("位移")
        self.displacement_mode.setCheckable(True)
        self.displacement_mode.setObjectName("modeButton")
        self.stress_mode.clicked.connect(lambda: self._set_field_mode("stress"))
        self.displacement_mode.clicked.connect(lambda: self._set_field_mode("displacement"))
        title_row.addWidget(self.stress_mode)
        title_row.addWidget(self.displacement_mode)
        main_layout.addLayout(title_row)
        canvas_frame = QFrame()
        canvas_frame.setObjectName("canvasFrame")
        canvas_layout = QVBoxLayout(canvas_frame)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.addWidget(self.canvas)
        main_layout.addWidget(canvas_frame, 1)
        conclusion = QFrame()
        conclusion.setObjectName("conclusionFrame")
        conclusion_layout = QHBoxLayout(conclusion)
        conclusion_layout.setContentsMargins(16, 12, 16, 12)
        conclusion_tag = QLabel("结论")
        conclusion_tag.setObjectName("conclusionTag")
        conclusion_layout.addWidget(conclusion_tag)
        conclusion_layout.addWidget(self.conclusion_label, 1)
        main_layout.addWidget(conclusion)
        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        metrics.addWidget(self.metric_deflection)
        metrics.addWidget(self.metric_stress)
        metrics.addWidget(self.metric_quality)
        main_layout.addLayout(metrics)
        content.addWidget(main, 1)

        control_scroll = QScrollArea()
        control_scroll.setObjectName("controlScroll")
        control_scroll.setWidgetResizable(True)
        control_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        control_scroll.setFixedWidth(366)
        control = QWidget()
        control.setObjectName("control")
        controls = QVBoxLayout(control)
        controls.setContentsMargins(18, 4, 18, 18)
        controls.setSpacing(12)
        controls.addWidget(self._section_title("模型与工况", "先使用自动值，也可以按实际情况修改。"))
        path_row = QHBoxLayout()
        path_row.addWidget(self.step_path, 1)
        path_row.addWidget(self.browse_step)
        controls.addLayout(path_row)
        controls.addWidget(self.model_meta)
        duty_panel = QFrame()
        duty_panel.setObjectName("settingPanel")
        duty_layout = QVBoxLayout(duty_panel)
        duty_layout.setContentsMargins(12, 10, 12, 10)
        duty_layout.setSpacing(7)
        duty_layout.addWidget(self._label("球阀装配任务 · HSR-CR605-790", "panelTitle"))
        duty_layout.addWidget(self.task_mode)
        duty_form = QFormLayout()
        duty_form.setVerticalSpacing(7)
        duty_form.addRow("球阀型号", self.valve_model)
        duty_form.addRow("公称通径", self.nominal_diameter)
        duty_form.addRow("球阀质量 (kg)", self.workpiece_mass)
        duty_form.addRow("夹爪质量 (kg)", self.gripper_mass)
        duty_form.addRow("动态系数", self.dynamic_factor)
        duty_form.addRow("摩擦系数", self.friction_coefficient)
        duty_form.addRow("夹持安全系数", self.grip_safety_factor)
        duty_form.addRow("装配推力 (N)", self.assembly_force)
        duty_layout.addLayout(duty_form)
        duty_note = QLabel("质量必须以实测或厂家数据为准；夹爪质量应包含法兰、快换、气管和线缆随动负载。")
        duty_note.setObjectName("materialNote")
        duty_note.setWordWrap(True)
        duty_layout.addWidget(duty_note)
        duty_layout.addWidget(self.robot_check)
        controls.addWidget(duty_panel)
        material_panel = QFrame()
        material_panel.setObjectName("settingPanel")
        material_layout = QVBoxLayout(material_panel)
        material_layout.setContentsMargins(12, 10, 12, 10)
        material_layout.setSpacing(7)
        material_layout.addWidget(self._label("3D 打印材料", "panelTitle"))
        material_layout.addWidget(self.material_combo)
        material_layout.addWidget(self.material_note)
        material_form = QFormLayout()
        material_form.setVerticalSpacing(7)
        material_form.addRow("弹性模量 (MPa)", self.young)
        material_form.addRow("泊松比", self.poisson)
        material_form.addRow("许用应力 (MPa)", self.allowable_stress)
        material_layout.addLayout(material_form)
        controls.addWidget(material_panel)

        load_panel = QFrame()
        load_panel.setObjectName("settingPanel")
        load_layout = QVBoxLayout(load_panel)
        load_layout.setContentsMargins(12, 10, 12, 10)
        load_layout.setSpacing(7)
        load_layout.addWidget(self._label("载荷工况", "panelTitle"))
        load_form = QFormLayout()
        load_form.setVerticalSpacing(7)
        load_form.addRow("载荷大小 (N)", self.force)
        load_form.addRow("载荷方向", self.load_direction)
        load_layout.addLayout(load_form)
        controls.addWidget(load_panel)

        geometry_panel = QFrame()
        geometry_panel.setObjectName("settingPanel")
        geometry_layout = QVBoxLayout(geometry_panel)
        geometry_layout.setContentsMargins(12, 10, 12, 10)
        geometry_layout.setSpacing(7)
        geometry_layout.addWidget(self._label("自动识别与精度", "panelTitle"))
        geometry_form = QFormLayout()
        geometry_form.setVerticalSpacing(7)
        geometry_form.addRow("长度 L (mm)", self.length)
        geometry_form.addRow("高度 H (mm)", self.height)
        geometry_form.addRow("宽度 W (mm)", self.width)
        geometry_form.addRow("网格尺寸 (mm)", self.mesh_size)
        geometry_layout.addLayout(geometry_form)
        controls.addWidget(geometry_panel)
        condition = QFrame()
        condition.setObjectName("infoPanel")
        condition_layout = QVBoxLayout(condition)
        condition_layout.setContentsMargins(12, 10, 12, 10)
        condition_layout.addWidget(self._label("自动边界条件", "panelTitle"))
        condition_layout.addWidget(self.boundary_text)
        controls.addWidget(condition)
        controls.addWidget(self.run_button)
        controls.addWidget(self.progress_bar)
        controls.addWidget(self.report_button)
        controls.addWidget(self.open_button)
        details = QFrame()
        details.setObjectName("detailsPanel")
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 8, 0, 0)
        details_layout.addWidget(self._label("计算过程", "panelTitle"))
        self.log.setMinimumHeight(118)
        details_layout.addWidget(self.log)
        controls.addWidget(details)
        controls.addStretch()
        control_scroll.setWidget(control)
        content.addWidget(control_scroll)
        outer.addLayout(content, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("就绪 · 选择 STEP 模型开始")

    def _section_title(self, title: str, helper: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._label(title, "sectionTitle"))
        helper_label = self._label(helper, "helperText")
        helper_label.setWordWrap(True)
        layout.addWidget(helper_label)
        return widget

    @staticmethod
    def _label(text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        return label

    def _apply_styles(self) -> None:
        if self._dark:
            colors = {
                "bg": "#0D1115", "panel": "#141A1F", "panel2": "#192027", "field": "#10161B",
                "line": "#29343C", "text": "#F0F3F4", "muted": "#8C98A0", "accent": "#3EC7B6",
                "accent2": "#72E0D1", "buttonText": "#07110F", "warn": "#F2A65A",
            }
        else:
            colors = {
                "bg": "#F4F6F8", "panel": "#FFFFFF", "panel2": "#E9EEF1", "field": "#F9FAFB",
                "line": "#D4DCE1", "text": "#171B1F", "muted": "#66727B", "accent": "#087F74",
                "accent2": "#0C9A8C", "buttonText": "#FFFFFF", "warn": "#B85D17",
            }
        self.canvas.set_theme(self._dark)
        self.setStyleSheet(f"""
            QMainWindow, QWidget#root {{ background: {colors['bg']}; color: {colors['text']}; font-family: 'Noto Sans SC'; }}
            QLabel {{ color: {colors['text']}; }}
            QFrame#header {{ background: {colors['panel']}; border-bottom: 1px solid {colors['line']}; }}
            QLabel#brand {{ color: {colors['text']}; font-size: 20px; font-weight: 700; }}
            QLabel#brandDescriptor {{ color: {colors['muted']}; font-size: 11px; margin-left: 8px; }}
            QLabel#solverStatus {{ color: {colors['accent']}; font-size: 10px; }}
            QLabel#pageTitle {{ color: {colors['text']}; font-size: 25px; font-weight: 650; }}
            QLabel#pageSubtitle, QLabel#helperText, QLabel#modelMeta {{ color: {colors['muted']}; font-size: 10px; }}
            QLabel#modelMeta {{ padding: 7px 9px; background: {colors['panel2']}; border-radius: 4px; }}
            QLabel#sectionTitle {{ color: {colors['text']}; font-size: 17px; font-weight: 650; }}
            QLabel#panelTitle, QLabel#metricLabel {{ color: {colors['muted']}; font-size: 10px; font-weight: 600; }}
            QFrame#canvasFrame, QFrame#metricCard, QFrame#conclusionFrame, QFrame#infoPanel {{ background: {colors['panel']}; border: 1px solid {colors['line']}; border-radius: 6px; }}
            QFrame#settingPanel {{ background: {colors['field']}; border: 1px solid {colors['line']}; border-radius: 5px; }}
            QFrame#automationTrack {{ background: {colors['panel']}; border-bottom: 1px solid {colors['line']}; }}
            QLabel#aiBadge {{ color: {colors['buttonText']}; background: {colors['accent']}; border-radius: 3px; padding: 3px 8px; font-size: 9px; font-weight: 700; }}
            QLabel#trackArrow {{ color: {colors['line']}; font-size: 15px; }}
            QLabel#trackStage {{ color: {colors['muted']}; padding: 3px 5px; font-size: 9px; }}
            QLabel#trackStage[state="active"] {{ color: {colors['accent']}; font-weight: 700; }}
            QLabel#trackStage[state="done"] {{ color: {colors['text']}; }}
            QLabel#materialNote {{ color: {colors['muted']}; font-size: 9px; padding-bottom: 2px; }}
            QLabel#robotCheck {{ color: {colors['accent']}; background: {colors['panel2']}; border-radius: 4px; padding: 8px; font-size: 9px; }}
            QLabel#robotCheck[overload="true"] {{ color: #F06B5D; border: 1px solid #8A3B36; }}
            QFrame#metricCard {{ min-height: 88px; }}
            QLabel#metricValue {{ color: {colors['text']}; font-size: 24px; font-weight: 700; }}
            QLabel#metricUnit {{ color: {colors['accent']}; font-size: 10px; }}
            QLabel#metricNote {{ color: {colors['muted']}; font-size: 9px; }}
            QLabel#conclusionTag {{ color: {colors['buttonText']}; background: {colors['accent']}; border-radius: 3px; padding: 3px 8px; font-size: 9px; font-weight: 700; }}
            QLabel#conclusion {{ color: {colors['text']}; font-size: 11px; }}
            QScrollArea#controlScroll {{ border: 0; background: {colors['panel']}; border-radius: 6px; }}
            QWidget#control {{ background: {colors['panel']}; }}
            QLineEdit, QDoubleSpinBox, QComboBox {{ min-height: 34px; padding: 0 9px; border: 1px solid {colors['line']}; border-radius: 4px; background: {colors['field']}; color: {colors['text']}; }}
            QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {colors['accent']}; }}
            QComboBox QAbstractItemView {{ background: {colors['panel']}; color: {colors['text']}; selection-background-color: {colors['accent']}; }}
            QWidget#control QLabel {{ color: {colors['muted']}; font-size: 10px; }}
            QWidget#control QLabel#sectionTitle {{ color: {colors['text']}; font-size: 17px; font-weight: 650; }}
            QWidget#control QLabel#panelTitle {{ color: {colors['muted']}; font-size: 10px; font-weight: 600; }}
            QPushButton, QToolButton {{ min-height: 34px; padding: 0 12px; border: 1px solid {colors['line']}; border-radius: 4px; background: {colors['panel2']}; color: {colors['text']}; font-size: 10px; }}
            QPushButton:hover, QToolButton:hover {{ border-color: {colors['accent']}; }}
            QPushButton#runButton {{ min-height: 46px; border: 0; background: {colors['accent']}; color: {colors['buttonText']}; font-size: 12px; font-weight: 700; }}
            QPushButton#runButton:hover {{ background: {colors['accent2']}; }}
            QPushButton#runButton:disabled {{ background: {colors['panel2']}; color: {colors['muted']}; }}
            QPushButton#reportButton {{ min-height: 40px; border-color: {colors['accent']}; color: {colors['accent']}; font-weight: 650; }}
            QPushButton:disabled {{ color: {colors['muted']}; border-color: {colors['line']}; }}
            QToolButton#modeButton {{ min-width: 52px; }}
            QToolButton#modeButton:checked {{ color: {colors['buttonText']}; background: {colors['accent']}; border-color: {colors['accent']}; }}
            QPlainTextEdit#eventStream {{ border: 1px solid {colors['line']}; border-radius: 4px; background: {colors['field']}; color: {colors['muted']}; font-size: 9px; padding: 7px; }}
            QProgressBar {{ min-height: 3px; max-height: 3px; border: 0; background: {colors['panel2']}; }}
            QProgressBar::chunk {{ background: {colors['accent']}; }}
            QStatusBar {{ background: {colors['panel']}; border-top: 1px solid {colors['line']}; color: {colors['muted']}; font-size: 9px; }}
            QDialog {{ background: {colors['bg']}; color: {colors['text']}; }}
            QLabel#reportTitle {{ color: {colors['text']}; font-size: 22px; font-weight: 700; }}
            QTextBrowser#reportBrowser {{ border: 1px solid {colors['line']}; border-radius: 6px; background: {colors['panel']}; color: {colors['text']}; padding: 22px; font-size: 12px; }}
            QScrollBar:vertical {{ width: 8px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {colors['line']}; border-radius: 4px; min-height: 28px; }}
        """)

    def _case(self) -> BeamCase:
        return BeamCase(
            length_mm=self.length.value(), height_mm=self.height.value(), width_mm=self.width.value(),
            force_n=self.force.value(), young_mpa=self.young.value(), poisson=self.poisson.value(),
            mesh_size_mm=self.mesh_size.value(), material_name=self.material_combo.currentText(),
            allowable_stress_mpa=self.allowable_stress.value(), load_direction=self.load_direction.currentText(),
            application_mode=self.task_mode.currentText(), robot_model="HSR-CR605-790",
            valve_model=self.valve_model.currentText(), nominal_diameter=self.nominal_diameter.currentText(),
            workpiece_mass_kg=self.workpiece_mass.value(), gripper_mass_kg=self.gripper_mass.value(),
            dynamic_factor=self.dynamic_factor.value(), friction_coefficient=self.friction_coefficient.value(),
            grip_safety_factor=self.grip_safety_factor.value(), assembly_force_n=self.assembly_force.value(),
        )

    def _gripper_duty(self) -> GripperDuty:
        return GripperDuty(
            valve_model=self.valve_model.currentText(),
            nominal_diameter=self.nominal_diameter.currentText(),
            workpiece_mass_kg=self.workpiece_mass.value(),
            gripper_mass_kg=self.gripper_mass.value(),
            dynamic_factor=self.dynamic_factor.value(),
            friction_coefficient=self.friction_coefficient.value(),
            grip_safety_factor=self.grip_safety_factor.value(),
            assembly_force_n=self.assembly_force.value(),
        )

    @Slot()
    def _update_gripper_duty(self) -> None:
        duty = self._gripper_duty()
        overload = not duty.payload_ok
        self.robot_check.setProperty("overload", overload)
        self.robot_check.style().unpolish(self.robot_check)
        self.robot_check.style().polish(self.robot_check)
        if overload:
            self.robot_check.setText(
                f"CR605 超载 · 总负载 {duty.total_payload_kg:.2f} kg / 额定 {CR605_RATED_PAYLOAD_KG:g} kg "
                f"({duty.payload_utilization_percent:.0f}%)。请减轻夹爪/工件或更换机器人。"
            )
        else:
            self.robot_check.setText(
                f"CR605 负载 {duty.total_payload_kg:.2f} / {CR605_RATED_PAYLOAD_KG:g} kg "
                f"({duty.payload_utilization_percent:.0f}%) · 建议总夹持力 {duty.required_total_grip_force_n:.0f} N "
                f"· 单爪约 {duty.force_per_jaw_n:.0f} N"
            )

    @Slot(str)
    def _apply_material_preset(self, material_name: str) -> None:
        preset = MATERIAL_PRESETS[material_name]
        self.young.setValue(preset.young_mpa)
        self.poisson.setValue(preset.poisson)
        self.allowable_stress.setValue(preset.allowable_stress_mpa)
        self.material_note.setText(preset.note + " · 参数可手动覆盖")

    @Slot()
    def _sync_preview(self) -> None:
        self.canvas.set_case(self._case())

    @Slot()
    def _toggle_theme(self) -> None:
        self._dark = not self._dark
        self.theme_button.setText("浅色" if self._dark else "深色")
        self._apply_styles()

    def _set_field_mode(self, mode: str) -> None:
        self.stress_mode.setChecked(mode == "stress")
        self.displacement_mode.setChecked(mode == "displacement")
        self.canvas.set_mode(mode)

    @Slot()
    def _choose_step(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 STEP 模型", "", "STEP 模型 (*.step *.stp)")
        if path:
            self.step_path.setText(path)
            self._auto_fill_from_step(Path(path))

    def _auto_fill_from_step(self, source: Path) -> None:
        try:
            info = inspect_step_geometry(source)
            case = auto_case_for_step(info)
        except Exception as exc:
            self._step_info = None
            self._auto_step_path = None
            self.canvas.set_geometry(None)
            QMessageBox.warning(self, "STEP 识别失败", f"无法读取模型：\n{type(exc).__name__}: {exc}")
            return
        self._step_info = info
        self._auto_step_path = source
        self.canvas.set_geometry(info)
        self.canvas.set_summary(None)
        self.material_combo.setCurrentText(case.material_name)
        self.length.setValue(case.length_mm)
        self.height.setValue(case.height_mm)
        self.width.setValue(case.width_mm)
        self.force.setValue(case.force_n)
        self.mesh_size.setValue(case.mesh_size_mm)
        preset = MATERIAL_PRESETS[case.material_name]
        self.allowable_stress.setValue(preset.allowable_stress_mpa)
        self.load_direction.setCurrentText(case.load_direction)
        self.automation_track.set_stage(0)
        self.title_label.setText(source.stem)
        self.subtitle_label.setText("模型已识别。确认右侧自动工况后即可运行真实分析。")
        self.model_meta.setText(f"{info.volume_count} 个实体 · {case.length_mm:g} × {case.width_mm:g} × {case.height_mm:g} mm")
        self.conclusion_label.setText("AI 已完成几何识别，并自动建议 PLA、100 N 与网格尺寸；你可以直接运行或修改建议。")
        self.log.setPlainText(f"AI 几何识别完成：{info.volume_count} 个实体\n材料建议：PLA\n载荷建议：100 N / -Y")
        self.statusBar().showMessage(f"已导入 {source.name} · 等待运行")

    @Slot()
    def _start_analysis(self) -> None:
        if self._process is not None:
            return
        duty_message = ""
        if self.task_mode.currentText() == "球阀装配夹爪":
            duty = self._gripper_duty()
            if not duty.payload_ok:
                QMessageBox.warning(
                    self,
                    "CR605 负载超限",
                    f"球阀与夹爪总质量为 {duty.total_payload_kg:.2f} kg，超过 HSR-CR605-790 的 5 kg 额定负载。\n"
                    "请减轻夹爪/工件或选择更高负载机器人后再分析。",
                )
                return
            self.force.setValue(duty.recommended_fea_force_n)
            duty_message = (
                f"AI 工况建议：CR605 负载占用 {duty.payload_utilization_percent:.0f}%，"
                f"夹持力 {duty.required_total_grip_force_n:.0f} N，FEA 载荷取 {duty.recommended_fea_force_n:.0f} N。"
            )
        try:
            ccx = find_calculix()
        except FileNotFoundError as exc:
            QMessageBox.critical(self, "缺少 CalculiX", str(exc))
            return
        source = Path(self.step_path.text().strip()) if self.step_path.text().strip() else None
        if source is not None and source.suffix.lower() not in {".step", ".stp"}:
            QMessageBox.warning(self, "模型格式不支持", "请选择 .step 或 .stp 文件。")
            return
        if source is not None and not source.exists():
            QMessageBox.warning(self, "模型不存在", f"找不到文件：{source}")
            return
        if source is not None and self._auto_step_path != source:
            self._auto_fill_from_step(source)
            if self._auto_step_path != source:
                return
        if source is None:
            self._step_info = None
            self._auto_step_path = None
            self.canvas.set_geometry(None)
            self.title_label.setText("悬臂梁验证模型")
        case = self._case()
        self.log.clear()
        if duty_message:
            self.log.appendPlainText(duty_message)
        self._last_summary = None
        self.canvas.set_summary(None)
        self.canvas.set_running(True)
        self.run_button.setEnabled(False)
        self.report_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.progress_bar.show()
        self.automation_track.set_stage(1)
        self.conclusion_label.setText("正在建立网格与求解，请稍候。")
        self.statusBar().showMessage("运行中 · Gmsh 网格与 CalculiX 求解")
        workdir = writable_root() / "runs"
        self._current_workdir = workdir
        self._process_output = ""
        self._process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        self._process.setProcessEnvironment(environment)
        self._process.setWorkingDirectory(str(bundled_root()))
        if getattr(sys, "frozen", False):
            program, arguments = sys.executable, ["--worker"]
        else:
            program = sys.executable
            arguments = [str(Path(__file__).resolve().parents[2] / "run_gui.py"), "--worker"]
        arguments.extend([
            "--workdir", str(workdir), "--ccx", str(ccx), "--length", str(case.length_mm),
            "--height", str(case.height_mm), "--width", str(case.width_mm), "--force", str(case.force_n),
            "--young", str(case.young_mpa), "--poisson", str(case.poisson), "--mesh-size", str(case.mesh_size_mm),
            "--material", case.material_name,
            "--allowable-stress", str(case.allowable_stress_mpa), f"--load-direction={case.load_direction}",
            "--application-mode", case.application_mode, "--robot-model", case.robot_model,
            "--valve-model", case.valve_model, "--nominal-diameter", case.nominal_diameter,
            "--workpiece-mass", str(case.workpiece_mass_kg), "--gripper-mass", str(case.gripper_mass_kg),
            "--dynamic-factor", str(case.dynamic_factor), "--friction-coefficient", str(case.friction_coefficient),
            "--grip-safety-factor", str(case.grip_safety_factor), "--assembly-force", str(case.assembly_force_n),
        ])
        if source is not None:
            arguments.extend(["--source-step", str(source)])
        if self._step_info is not None and self._step_info.volume_count > 1:
            self.log.appendPlainText(f"自动工况：{self._step_info.volume_count} 个实体分别固定自身最小 X 端，并在最大 X 端加载。")
            self.log.appendPlainText("多实体模型不使用悬臂梁理论偏差作为判断标准。")
        self._process.readyReadStandardOutput.connect(self._read_process_output)
        self._process.readyReadStandardError.connect(self._read_process_error)
        self._process.finished.connect(self._process_finished)
        self._process.start(program, arguments)

    @Slot()
    def _read_process_output(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._process_output += text
        for line in text.splitlines():
            if line.strip():
                self.log.appendPlainText(line)
                if "网格完成" in line:
                    self.automation_track.set_stage(2)
                elif "边界条件" in line:
                    self.automation_track.set_stage(3)
                elif "正在调用 CalculiX" in line:
                    self.automation_track.set_stage(3)
                elif "求解完成" in line:
                    self.automation_track.set_stage(4)

    @Slot()
    def _read_process_error(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        self._process_output += text
        for line in text.splitlines():
            if line.strip():
                self.log.appendPlainText(f"[求解器] {line}")

    @Slot(int, QProcess.ExitStatus)
    def _process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._read_process_output()
        self._read_process_error()
        if exit_code == 0:
            match = re.search(r"(?m)^run_dir=(.+)$", self._process_output)
            if match:
                try:
                    summary = load_run_summary(Path(match.group(1).strip()) / "summary.json")
                except Exception as exc:
                    self._show_process_failure(f"结果读取失败：{type(exc).__name__}: {exc}")
                else:
                    self._analysis_finished(summary)
            else:
                self._show_process_failure("求解完成，但未找到结果目录。")
        else:
            self._show_process_failure(f"分析进程返回码：{exit_code}\n请检查计算过程。")
        if self._process is not None:
            self._process.deleteLater()
        self._process = None
        self.run_button.setEnabled(True)
        self.progress_bar.hide()

    @Slot(object)
    def _analysis_finished(self, summary: RunSummary) -> None:
        self._last_summary = summary
        self.automation_track.set_stage(5)
        self.title_label.setText(summary.source_model_name)
        self.material_combo.setCurrentText(summary.case.material_name)
        self.length.setValue(summary.case.length_mm)
        self.height.setValue(summary.case.height_mm)
        self.width.setValue(summary.case.width_mm)
        self.force.setValue(summary.case.force_n)
        self.young.setValue(summary.case.young_mpa)
        self.poisson.setValue(summary.case.poisson)
        self.mesh_size.setValue(summary.case.mesh_size_mm)
        self.allowable_stress.setValue(summary.case.allowable_stress_mpa)
        self.load_direction.setCurrentText(summary.case.load_direction)
        self.task_mode.setCurrentText(summary.case.application_mode)
        self.valve_model.setCurrentText(summary.case.valve_model)
        self.nominal_diameter.setCurrentText(summary.case.nominal_diameter)
        self.workpiece_mass.setValue(summary.case.workpiece_mass_kg)
        self.gripper_mass.setValue(summary.case.gripper_mass_kg)
        self.dynamic_factor.setValue(summary.case.dynamic_factor)
        self.friction_coefficient.setValue(summary.case.friction_coefficient)
        self.grip_safety_factor.setValue(summary.case.grip_safety_factor)
        self.assembly_force.setValue(summary.case.assembly_force_n)
        self.canvas.set_running(False)
        self.canvas.set_summary(summary)
        self._show_results(summary)
        self.report_button.setEnabled(summary.report_path is not None and summary.report_path.exists())
        self.open_button.setEnabled(True)
        self.subtitle_label.setText(f"真实求解完成 · {summary.node_count} 节点 · {summary.element_count} 单元")
        self.log.appendPlainText(f"中文报告：{summary.report_path}")
        self.statusBar().showMessage("完成 · 求解器返回码 0 · 云图与中文报告已生成")

    def _show_process_failure(self, message: str) -> None:
        self.canvas.set_running(False)
        self.log.appendPlainText(message)
        self.conclusion_label.setText("分析没有完成。请查看右侧计算过程中的错误信息。")
        self.statusBar().showMessage("分析失败 · 请检查计算过程")
        QMessageBox.critical(self, "有限元分析失败", message)

    def _show_results(self, summary: RunSummary) -> None:
        result = summary.results
        self.metric_deflection.value_label.setText(f"{result.max_displacement_mm:.4g}")
        self.metric_deflection.note_label.setText(f"最大值位于节点 {result.max_displacement_node}")
        self.metric_stress.value_label.setText(f"{result.max_von_mises_mpa:.4g}")
        self.metric_stress.note_label.setText(f"最大值位于单元 {result.max_von_mises_element}")
        self.metric_quality.value_label.setText("已完成")
        if summary.case.allowable_stress_mpa > 0 and result.max_von_mises_mpa > 0:
            safety_factor = summary.case.allowable_stress_mpa / result.max_von_mises_mpa
            self.metric_quality.value_label.setText(f"{safety_factor:.2f}")
            self.metric_quality.note_label.setText("估算安全系数 · 需结合打印工艺复核")
            assessment = "低于当前许用值" if safety_factor >= 1.0 else "超过当前许用值"
            self.conclusion_label.setText(
                f"AI 解读：最大变形 {result.max_displacement_mm:.4g} mm；最大应力 {result.max_von_mises_mpa:.4g} MPa，"
                f"{assessment}，估算安全系数 {safety_factor:.2f}。FDM 结果仍需结合打印方向与填充参数复核。"
            )
        else:
            self.metric_quality.note_label.setText(f"CalculiX 返回码 0 · {summary.element_type}")
            self.conclusion_label.setText(
                f"AI 解读：最大变形 {result.max_displacement_mm:.4g} mm，最大等效应力 {result.max_von_mises_mpa:.4g} MPa。"
                "当前未设置材料许用应力，因此还不能自动判定是否安全。"
            )

    @Slot()
    def _open_report(self) -> None:
        if self._last_summary is None or self._last_summary.report_path is None:
            return
        dialog = ReportDialog(self._last_summary.report_path, self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    @Slot()
    def _open_results(self) -> None:
        if self._last_summary is not None:
            os.startfile(str(self._last_summary.workdir))


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
