from __future__ import annotations

"""CalculiX result parsing.

Parses the ``.dat`` output into scalar maxima (:func:`parse_dat_results`) or the
full nodal/elemental fields plus exterior tetra faces for cloud rendering
(:func:`parse_field_results`). Tolerates CalculiX's ASCII layout with a loose
numeric tokenizer; a missing displacement or stress section raises rather than
returning partial data.
"""

import math
import re
from pathlib import Path

from .models import FieldResults, ParsedResults


_NUM_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[Ee][-+]?\d+)?")


def _numbers(line: str) -> list[float]:
    return [float(x) for x in _NUM_RE.findall(line)]


def _von_mises(sxx: float, syy: float, szz: float, sxy: float, sxz: float, syz: float) -> float:
    return math.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy**2 + sxz**2 + syz**2)
    )


def parse_dat_results(dat_path: Path) -> ParsedResults:
    if not dat_path.exists():
        raise FileNotFoundError(dat_path)

    max_disp = -1.0
    max_disp_node = -1
    max_vm = -1.0
    max_vm_element = -1
    disp_count = 0
    stress_count = 0
    section: str | None = None

    for raw in dat_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        lower = raw.lower()
        if "displacements" in lower and "for set" in lower:
            section = "disp"
            continue
        if "stresses" in lower and "for set" in lower:
            section = "stress"
            continue
        if not raw.strip():
            continue

        vals = _numbers(raw)
        if section == "disp" and len(vals) >= 4:
            node = int(vals[0])
            ux, uy, uz = vals[1:4]
            mag = math.sqrt(ux * ux + uy * uy + uz * uz)
            disp_count += 1
            if mag > max_disp:
                max_disp = mag
                max_disp_node = node
        elif section == "stress" and len(vals) >= 8:
            elem = int(vals[0])
            sxx, syy, szz, sxy, sxz, syz = vals[2:8]
            vm = _von_mises(sxx, syy, szz, sxy, sxz, syz)
            stress_count += 1
            if vm > max_vm:
                max_vm = vm
                max_vm_element = elem

    if disp_count == 0:
        raise RuntimeError(f"No displacement results found in {dat_path}")
    if stress_count == 0:
        raise RuntimeError(f"No stress results found in {dat_path}")

    return ParsedResults(
        max_displacement_mm=max_disp,
        max_displacement_node=max_disp_node,
        max_von_mises_mpa=max_vm,
        max_von_mises_element=max_vm_element,
        displacement_count=disp_count,
        stress_count=stress_count,
    )


def parse_field_results(inp_path: Path, dat_path: Path) -> FieldResults:
    """Read the solved nodal/element fields and extract the exterior tetra faces."""
    nodes: dict[int, tuple[float, float, float]] = {}
    elements: dict[int, tuple[int, ...]] = {}
    section: str | None = None
    for raw in inp_path.read_text(encoding="ascii", errors="ignore").splitlines():
        stripped = raw.strip()
        upper = stripped.upper()
        if upper.startswith("*NODE"):
            section = "nodes"
            continue
        if upper.startswith("*ELEMENT"):
            section = "elements"
            continue
        if upper.startswith("*"):
            section = None
            continue
        if not stripped:
            continue
        parts = [part.strip() for part in stripped.split(",")]
        if section == "nodes" and len(parts) >= 4:
            nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
        elif section == "elements" and len(parts) >= 5:
            elements[int(parts[0])] = tuple(int(value) for value in parts[1:] if value)

    displacements: dict[int, tuple[float, float, float]] = {}
    element_vm: dict[int, float] = {}
    section = None
    for raw in dat_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        lower = raw.lower()
        if "displacements" in lower and "for set" in lower:
            section = "disp"
            continue
        if "stresses" in lower and "for set" in lower:
            section = "stress"
            continue
        values = _numbers(raw)
        if section == "disp" and len(values) >= 4:
            displacements[int(values[0])] = (values[1], values[2], values[3])
        elif section == "stress" and len(values) >= 8:
            element_id = int(values[0])
            vm = _von_mises(*values[2:8])
            element_vm[element_id] = max(element_vm.get(element_id, 0.0), vm)

    if not nodes or not elements or not displacements or not element_vm:
        raise RuntimeError("有限元场数据不完整，无法生成真实云图")

    face_owners: dict[tuple[int, int, int], tuple[int, tuple[int, int, int]]] = {}
    duplicate_faces: set[tuple[int, int, int]] = set()
    for element_id, connectivity in elements.items():
        corners = connectivity[:4]
        if len(corners) < 4:
            continue
        faces = (
            (corners[0], corners[1], corners[2]),
            (corners[0], corners[1], corners[3]),
            (corners[0], corners[2], corners[3]),
            (corners[1], corners[2], corners[3]),
        )
        for face in faces:
            key = tuple(sorted(face))
            if key in face_owners:
                duplicate_faces.add(key)
            else:
                face_owners[key] = (element_id, face)
    surface_faces = tuple(
        (*face, element_id)
        for key, (element_id, face) in face_owners.items()
        if key not in duplicate_faces
    )
    return FieldResults(nodes, displacements, elements, element_vm, surface_faces)
