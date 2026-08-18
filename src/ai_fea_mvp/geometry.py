from __future__ import annotations

"""STEP geometry handling via Gmsh.

Inspects an imported STEP/STP assembly (:func:`inspect_step_geometry`) to report
volume count and bounding boxes, derives a first-pass :class:`~ai_fea_mvp.models.BeamCase`
(:func:`auto_case_for_step`), and generates the validation cantilever STEP model
(:func:`create_cantilever_step`). All Gmsh sessions are initialized and finalized
within each function so a crash in one entry point cannot leak a Gmsh session.
"""

from dataclasses import dataclass
from pathlib import Path

import gmsh

from .models import BeamCase


@dataclass(frozen=True)
class StepGeometry:
    volume_count: int
    bbox: tuple[float, float, float, float, float, float]
    volume_boxes: tuple[tuple[float, float, float, float, float, float], ...]

    @property
    def length_mm(self) -> float:
        """Overall X extent of the assembly bounding box [mm]."""
        return self.bbox[3] - self.bbox[0]

    @property
    def width_mm(self) -> float:
        """Overall Y extent of the assembly bounding box [mm]."""
        return self.bbox[4] - self.bbox[1]

    @property
    def height_mm(self) -> float:
        """Overall Z extent of the assembly bounding box [mm]."""
        return self.bbox[5] - self.bbox[2]


def inspect_step_geometry(step_path: Path) -> StepGeometry:
    """Return volume count and bounding boxes for a STEP assembly."""
    if not step_path.exists():
        raise FileNotFoundError(step_path)

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("step_geometry_inspection")
        gmsh.merge(str(step_path))
        gmsh.model.occ.synchronize()
        volumes = gmsh.model.getEntities(3)
        if not volumes:
            raise RuntimeError("STEP 中没有可识别的实体体积")
        boxes = [gmsh.model.occ.getBoundingBox(3, tag) for _dim, tag in volumes]
        bbox = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            min(box[2] for box in boxes),
            max(box[3] for box in boxes),
            max(box[4] for box in boxes),
            max(box[5] for box in boxes),
        )
    finally:
        gmsh.finalize()

    volume_boxes = tuple(tuple(float(value) for value in box) for box in boxes)
    return StepGeometry(
        volume_count=len(volumes),
        bbox=tuple(float(value) for value in bbox),
        volume_boxes=volume_boxes,
    )


def auto_case_for_step(info: StepGeometry) -> BeamCase:
    """Derive a sensible first-pass BeamCase (geometric dims + PLA default) from a STEP inspection."""
    if info.length_mm <= 0.0 or info.width_mm <= 0.0 or info.height_mm <= 0.0:
        raise RuntimeError(f"STEP 包围盒无效：{info.bbox}")
    cross_section = min(info.width_mm, info.height_mm)
    return BeamCase(
        length_mm=round(info.length_mm, 3),
        height_mm=round(info.height_mm, 3),
        width_mm=round(info.width_mm, 3),
        force_n=100.0,
        young_mpa=3_500.0,
        poisson=0.36,
        mesh_size_mm=round(max(1.0, min(5.0, cross_section / 4.0)), 3),
        material_name="PLA",
    )


def create_cantilever_step(case: BeamCase, step_path: Path) -> Path:
    """Generate the validation cantilever STEP model for a given BeamCase."""
    step_path.parent.mkdir(parents=True, exist_ok=True)
    if step_path.exists():
        step_path.unlink()

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("cantilever_beam_step")
        gmsh.model.occ.addBox(
            0.0,
            -case.height_mm / 2.0,
            -case.width_mm / 2.0,
            case.length_mm,
            case.height_mm,
            case.width_mm,
        )
        gmsh.model.occ.synchronize()
        gmsh.write(str(step_path))
    finally:
        gmsh.finalize()

    if not step_path.exists() or step_path.stat().st_size == 0:
        raise RuntimeError(f"STEP export failed: {step_path}")
    return step_path
