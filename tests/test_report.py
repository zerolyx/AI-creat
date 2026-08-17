from __future__ import annotations

import unittest
from pathlib import Path

from ai_fea_mvp.models import BeamCase, ParsedResults, RunSummary
from ai_fea_mvp.report import generate_markdown_report


class ChineseReportTest(unittest.TestCase):
    def test_multibody_report_explains_scope_without_claiming_safety(self) -> None:
        root = Path("result")
        summary = RunSummary(
            workdir=root, step_path=root / "model.step", mesh_path=root / "model.msh",
            inp_path=root / "model.inp", dat_path=root / "model.dat", sta_path=root / "model.sta",
            solver_returncode=0, element_type="C3D4", node_count=100, element_count=300,
            fixed_node_count=12, load_node_count=15,
            results=ParsedResults(0.12, 8, 1.3, 20, 100, 300),
            theoretical_tip_deflection_mm=0.1, theoretical_max_stress_mpa=1.0,
            solid_count=8, case=BeamCase(allowable_stress_mpa=0), source_model_name="P7",
        )
        report = generate_markdown_report(summary)
        self.assertIn("# P7 有限元分析报告", report)
        self.assertIn("不能仅凭应力数值自动判定安全", report)
        self.assertIn("经典悬臂梁理论不适用于该装配体", report)

    def test_report_includes_print_material_safety_factor_and_load_direction(self) -> None:
        root = Path("result")
        summary = RunSummary(
            workdir=root, step_path=root / "model.step", mesh_path=root / "model.msh",
            inp_path=root / "model.inp", dat_path=root / "model.dat", sta_path=root / "model.sta",
            solver_returncode=0, element_type="C3D4", node_count=100, element_count=300,
            fixed_node_count=12, load_node_count=15,
            results=ParsedResults(0.12, 8, 5.0, 20, 100, 300),
            theoretical_tip_deflection_mm=0.1, theoretical_max_stress_mpa=1.0,
            solid_count=8,
            case=BeamCase(material_name="PETG", young_mpa=2100, poisson=0.38,
                          allowable_stress_mpa=25, load_direction="+Z"),
            source_model_name="P7",
        )
        report = generate_markdown_report(summary)
        self.assertIn("| 材料 | PETG |", report)
        self.assertIn("沿 +Z 方向", report)
        self.assertIn("安全系数约为 **5.00**", report)
        self.assertIn("## 3. 机器人与夹持可行性", report)
        self.assertIn("HSR-CR605-790", report)


if __name__ == "__main__":
    unittest.main()
