from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

import gmsh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_fea_mvp.mesh import mesh_step_cantilever
from ai_fea_mvp.models import BeamCase


class MultiBodyStepTest(unittest.TestCase):
    def test_eight_solid_step_is_meshed_as_one_model(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai_fea_multibody_") as temp:
            root = Path(temp)
            step_path = root / "eight_bodies.step"
            mesh_path = root / "eight_bodies.msh"

            gmsh.initialize()
            try:
                gmsh.model.add("eight_bodies")
                for index in range(8):
                    gmsh.model.occ.addBox(index * 12.5, -5.0, -5.0, 12.5, 10.0, 10.0)
                gmsh.model.occ.synchronize()
                gmsh.write(str(step_path))
            finally:
                gmsh.finalize()

            mesh = mesh_step_cantilever(BeamCase(length_mm=100.0), step_path, mesh_path)
            self.assertEqual(mesh.solid_count, 8)
            self.assertGreater(len(mesh.elements), 0)
            self.assertGreater(len(mesh.fixed_nodes), 0)
            self.assertGreater(len(mesh.load_nodes), 0)


if __name__ == "__main__":
    unittest.main()
