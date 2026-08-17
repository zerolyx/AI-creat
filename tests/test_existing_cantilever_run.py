from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_fea_mvp.cli import run_cantilever
from ai_fea_mvp.models import BeamCase

from _solver import find_ccx, requires_solver

ROOT = Path(__file__).resolve().parents[1]


class ExistingCantileverRunTest(unittest.TestCase):
    @requires_solver
    def test_real_cantilever_run_has_solver_outputs_and_reasonable_results(self) -> None:
        ccx = find_ccx()
        with tempfile.TemporaryDirectory() as folder:
            summary = run_cantilever(
                Path(folder),
                ccx,
                case=BeamCase(application_mode="通用结构分析"),
            )
            summary_path = summary.workdir / "summary.json"
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            run_dir = summary.workdir

            for name in [
                "cantilever_beam.step",
                "cantilever_beam.msh",
                "cantilever_beam.inp",
                "cantilever_beam.dat",
                "cantilever_beam.frd",
                "cantilever_beam.sta",
                "ccx.log",
            ]:
                path = run_dir / name
                self.assertTrue(path.exists(), path)
                self.assertGreater(path.stat().st_size, 0, path)

            self.assertEqual(data["solver_returncode"], 0)
            self.assertEqual(data["element_type"], "C3D4")
            self.assertGreaterEqual(data["node_count"], 100)
            self.assertGreaterEqual(data["element_count"], 100)

            results = data["results"]
            self.assertGreater(results["max_displacement_mm"], 0.0)
            self.assertGreater(results["max_von_mises_mpa"], 0.0)

            disp_theory = data["theoretical_tip_deflection_mm"]
            stress_theory = data["theoretical_max_stress_mpa"]
            disp_error = abs(results["max_displacement_mm"] - disp_theory) / disp_theory
            stress_error = abs(results["max_von_mises_mpa"] - stress_theory) / stress_theory

            self.assertLess(disp_error, 0.60)
            self.assertLess(stress_error, 0.50)


if __name__ == "__main__":
    unittest.main()
