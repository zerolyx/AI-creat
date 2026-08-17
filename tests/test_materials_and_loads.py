from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_fea_mvp.calculix import write_calculix_input
from ai_fea_mvp.materials import MATERIAL_PRESETS
from ai_fea_mvp.models import BeamCase, MeshData


class MaterialsAndLoadsTest(unittest.TestCase):
    def test_common_fdm_materials_have_editable_starting_values(self) -> None:
        for name in ("PLA", "PLA+", "PETG", "ABS", "ASA", "尼龙 PA", "PA-CF", "PC", "TPU 95A"):
            self.assertIn(name, MATERIAL_PRESETS)
            self.assertGreater(MATERIAL_PRESETS[name].young_mpa, 0)
            self.assertGreater(MATERIAL_PRESETS[name].allowable_stress_mpa, 0)

    def test_positive_z_load_is_written_to_calculix_dof_3(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mesh = MeshData(
                nodes={1: (0, 0, 0), 2: (1, 0, 0), 3: (0, 1, 0), 4: (0, 0, 1)},
                elements={1: (1, 2, 3, 4)}, fixed_nodes=[1], load_nodes=[2, 3],
                element_type="C3D4", mesh_path=root / "mesh.msh",
            )
            inp = write_calculix_input(BeamCase(force_n=100, load_direction="+Z"), mesh, root / "job.inp")
            text = inp.read_text(encoding="ascii")
            self.assertIn("2, 3, 50", text)
            self.assertIn("3, 3, 50", text)


if __name__ == "__main__":
    unittest.main()
