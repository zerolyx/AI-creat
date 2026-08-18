from __future__ import annotations

"""CalculiX solver bridge.

Builds a linear-static CalculiX input deck (`.inp`) from a :class:`~ai_fea_mvp.models.BeamCase`
and a :class:`~ai_fea_mvp.models.MeshData`, writes the fixed/load node sets and
material card, then runs the ``ccx`` solver in the job directory and captures its
log. ``write_calculix_input`` never runs a solver; ``run_calculix`` never writes
one — callers chain them through :func:`ai_fea_mvp.cli.run_cantilever`.
"""

import math
import subprocess
from pathlib import Path

from .models import BeamCase, MeshData


def _format_id_lines(ids: list[int], per_line: int = 16) -> list[str]:
    """Format a sequence of ids as wrapped comma-separated lines."""
    lines: list[str] = []
    for start in range(0, len(ids), per_line):
        lines.append(", ".join(str(i) for i in ids[start : start + per_line]))
    return lines


def write_calculix_input(case: BeamCase, mesh: MeshData, inp_path: Path) -> Path:
    """Write the CalculiX `.inp` deck (nodes/elements/sets/material/loads) for one case+mesh."""
    inp_path.parent.mkdir(parents=True, exist_ok=True)
    all_nodes = sorted(mesh.nodes)
    all_elements = sorted(mesh.elements)
    load_dof, load_sign = case.load_dof_and_sign
    force_per_node = load_sign * case.force_n / len(mesh.load_nodes)

    lines: list[str] = [
        "*HEADING",
        "AI-FEA-Assistant automatic STEP assembly analysis",
        "*NODE",
    ]
    for node_id in all_nodes:
        x, y, z = mesh.nodes[node_id]
        lines.append(f"{node_id}, {x:.10g}, {y:.10g}, {z:.10g}")

    lines.append(f"*ELEMENT, TYPE={mesh.element_type}, ELSET=EALL")
    for elem_id in all_elements:
        conn = ", ".join(str(n) for n in mesh.elements[elem_id])
        lines.append(f"{elem_id}, {conn}")

    lines.append("*NSET, NSET=ALLNODES")
    lines.extend(_format_id_lines(all_nodes))
    lines.append("*NSET, NSET=FIXED")
    lines.extend(_format_id_lines(mesh.fixed_nodes))
    lines.append("*NSET, NSET=LOAD")
    lines.extend(_format_id_lines(mesh.load_nodes))
    lines.extend(
        [
            "*MATERIAL, NAME=MAT",
            "*ELASTIC",
            f"{case.young_mpa:.10g}, {case.poisson:.10g}",
            "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT",
            "*STEP",
            "*STATIC",
            "*BOUNDARY",
            "FIXED, 1, 3, 0.0",
            "*CLOAD",
        ]
    )
    for node_id in mesh.load_nodes:
        lines.append(f"{node_id}, {load_dof}, {force_per_node:.12g}")

    lines.extend(
        [
            "*NODE PRINT, NSET=ALLNODES",
            "U",
            "*EL PRINT, ELSET=EALL",
            "S",
            "*NODE FILE",
            "U",
            "*EL FILE",
            "S",
            "*END STEP",
            "",
        ]
    )

    inp_path.write_text("\n".join(lines), encoding="ascii")
    if not inp_path.exists() or inp_path.stat().st_size == 0:
        raise RuntimeError(f"CalculiX input write failed: {inp_path}")
    return inp_path


def run_calculix(ccx_path: Path, inp_path: Path, log_path: Path) -> int:
    """Run the ccx solver on a job and write its stdout/stderr+return code to the log; returns the return code."""
    if not ccx_path.exists():
        raise FileNotFoundError(ccx_path)
    if not inp_path.exists():
        raise FileNotFoundError(inp_path)

    job_name = inp_path.stem
    completed = subprocess.run(
        [str(ccx_path), job_name],
        cwd=str(inp_path.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    log_path.write_text(
        "STDOUT:\n"
        + completed.stdout
        + "\nSTDERR:\n"
        + completed.stderr
        + f"\nRETURN_CODE: {completed.returncode}\n",
        encoding="utf-8",
    )
    return completed.returncode
