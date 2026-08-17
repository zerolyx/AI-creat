# ai_fea_mvp — AI-FEA Valve Gripper

Python package behind the **AI-FEA Valve Gripper** desktop assistant: mesh a
STEP assembly with Gmsh, run a linear-static solve with CalculiX, parse the
results and render a Chinese Markdown report — all locally, with no cloud
solver.

> Part of the AI-creat monorepo. Top-level docs:
> [README.en.md](../../README.en.md) / [README.md](../../README.md).

## Pipeline

```
STEP/STP ──► geometry.py   (inspect, auto-case)
          ──► mesh.py      (Gmsh tetrahedral mesh + physical groups)
          ──► calculix.py  (write *.inp, run ccx)
          ──► results.py   (parse *.dat maxima / fields)
          ──► report.py    (Chinese Markdown report)
          ──► cli.py       (run_cantilever orchestration + summary.json)
                                     │
                                     └─► gui.py (PySide6 desktop UI, worker process)
```

## Modules

| Module | Responsibility |
| --- | --- |
| `models.py` | Immutable dataclasses: `BeamCase`, `MeshData`, `ParsedResults`, `RunSummary`, `FieldResults` |
| `geometry.py` | STEP inspection (`inspect_step_geometry`), auto-case derivation, cantilever validation model |
| `mesh.py` | Gmsh meshing, per-solid X-min fix / X-max load end faces, CalculiX orientation + C3D10 remap, boundary node sets |
| `calculix.py` | `.inp` deck writer (`write_calculix_input`) and solver runner (`run_calculix`) |
| `results.py` | `.dat` parsing: maxima (`parse_dat_results`) and full fields + exterior faces (`parse_field_results`) |
| `report.py` | Chinese Markdown report generation |
| `cli.py` | End-to-end `run_cantilever`, `summary.json`, tiny argparse CLI, `load_run_summary` |
| `gui.py` | PySide6 Chinese light/dark desktop UI + worker subprocess |
| `runtime.py` | Project / bundled path + CalculiX solver resolution (source & frozen EXE) |
| `gripper.py` | CR605 payload pre-check and grip-force estimation (`GripperDuty`) |
| `materials.py` | FDM material presets for quick screening |

## Solver runtime

The `ccx` solver binary is **not** installed with this package. Obtain it via
the repo helper:

```powershell
# from the repo root
.\scripts\fetch-ccx.ps1
```

or place a CalculiX binary at `runtime/ccx/ccx.exe`, or put `ccx` on `PATH`
(e.g. `apt install calculix-ccx` on Linux). Tests skip gracefully when no
solver is available (see `tests/_solver.py`).

## Usage

Run end to end from a `Path` work directory:

```python
from pathlib import Path
from ai_fea_mvp.cli import run_cantilever

summary = run_cantilever(Path("runs/demo"), ccx_path=Path("runtime/ccx/ccx.exe"))
print(summary)
```

Or use the package-provided entry points: `python run_gui.py` (desktop UI) or
`python run_mvp.py` / `python -m ai_fea_mvp.cli` (headless CLI).

## License

Part of the AI-FEA Valve Gripper project, released under
[GPL-2.0](../../LICENSE).
