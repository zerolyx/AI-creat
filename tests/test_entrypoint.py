from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EntrypointTest(unittest.TestCase):
    def test_gui_entrypoint_calls_gui_main_without_arguments(self) -> None:
        tree = ast.parse((ROOT / "run_gui.py").read_text(encoding="utf-8"))
        source = ast.unparse(tree)
        self.assertIn("raise SystemExit(gui_main())", source)
        self.assertIn("raise SystemExit(worker_main(arguments))", source)


if __name__ == "__main__":
    unittest.main()
