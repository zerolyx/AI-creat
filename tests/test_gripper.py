from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from ai_fea_mvp.cli import run_cantilever
from ai_fea_mvp.gripper import GripperDuty


class GripperDutyTest(unittest.TestCase):
    def test_dn25_example_is_below_cr605_payload_and_calculates_grip_force(self) -> None:
        duty = GripperDuty(workpiece_mass_kg=4.05, gripper_mass_kg=0.60)
        self.assertTrue(duty.payload_ok)
        self.assertAlmostEqual(duty.total_payload_kg, 4.65)
        self.assertAlmostEqual(duty.payload_utilization_percent, 93.0)
        self.assertGreater(duty.required_total_grip_force_n, 390)
        self.assertAlmostEqual(duty.force_per_jaw_n * 2, duty.required_total_grip_force_n)

    def test_heavier_valve_is_rejected_for_cr605(self) -> None:
        duty = GripperDuty(workpiece_mass_kg=6.0, gripper_mass_kg=0.6)
        self.assertFalse(duty.payload_ok)
        self.assertGreater(duty.payload_utilization_percent, 100)

    def test_valve_case_uses_recommended_grip_force_for_fea(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ccx = root / "runtime" / "ccx" / "ccx.exe"
        with tempfile.TemporaryDirectory() as folder:
            summary = run_cantilever(Path(folder), ccx)
            self.assertAlmostEqual(summary.case.force_n, 397.169325, places=3)
            self.assertEqual(summary.solver_returncode, 0)
            load_lines = [
                line for line in summary.inp_path.read_text(encoding="ascii").splitlines()
                if line.strip().count(",") == 2 and line.split(",", 1)[0].strip().isdigit()
            ]
            self.assertTrue(load_lines)


if __name__ == "__main__":
    unittest.main()
