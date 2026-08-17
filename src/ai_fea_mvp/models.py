from __future__ import annotations

"""Core data models.

Immutable dataclasses shared across the pipeline: the analysis :class:`BeamCase`
(geometry, material, load direction and gripper duty inputs, plus theoretical
beam-aid formulas), the generated :class:`MeshData`, parsed solver output
(:class:`ParsedResults`), the per-run :class:`RunSummary`, and the
:class:`FieldResults` carrying nodal/elemental fields for cloud rendering.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BeamCase:
    length_mm: float = 100.0
    height_mm: float = 10.0
    width_mm: float = 10.0
    force_n: float = 100.0
    young_mpa: float = 3_500.0
    poisson: float = 0.36
    mesh_size_mm: float = 5.0
    material_name: str = "PLA"
    allowable_stress_mpa: float = 30.0
    load_direction: str = "-Y"
    application_mode: str = "球阀装配夹爪"
    robot_model: str = "HSR-CR605-790"
    valve_model: str = "Q41F-16P / 16RL"
    nominal_diameter: str = "DN25"
    workpiece_mass_kg: float = 4.05
    gripper_mass_kg: float = 0.60
    dynamic_factor: float = 1.50
    friction_coefficient: float = 0.30
    grip_safety_factor: float = 2.00
    assembly_force_n: float = 100.0
    # C3D10 is the target element, but this MVP defaults to C3D4 because
    # the Gmsh->CalculiX C3D10 midside-node mapping still needs validation.
    element_order: int = 1

    @property
    def second_moment_mm4(self) -> float:
        return self.width_mm * self.height_mm**3 / 12.0

    @property
    def theoretical_tip_deflection_mm(self) -> float:
        return self.force_n * self.length_mm**3 / (
            3.0 * self.young_mpa * self.second_moment_mm4
        )

    @property
    def theoretical_max_stress_mpa(self) -> float:
        moment_n_mm = self.force_n * self.length_mm
        return moment_n_mm * (self.height_mm / 2.0) / self.second_moment_mm4

    @property
    def load_dof_and_sign(self) -> tuple[int, float]:
        direction = self.load_direction.upper()
        mapping = {
            "+X": (1, 1.0), "-X": (1, -1.0),
            "+Y": (2, 1.0), "-Y": (2, -1.0),
            "+Z": (3, 1.0), "-Z": (3, -1.0),
        }
        if direction not in mapping:
            raise ValueError(f"Unsupported load direction: {self.load_direction}")
        return mapping[direction]


@dataclass(frozen=True)
class MeshData:
    nodes: dict[int, tuple[float, float, float]]
    elements: dict[int, tuple[int, ...]]
    fixed_nodes: list[int]
    load_nodes: list[int]
    element_type: str
    mesh_path: Path
    solid_count: int = 1


@dataclass(frozen=True)
class ParsedResults:
    max_displacement_mm: float
    max_displacement_node: int
    max_von_mises_mpa: float
    max_von_mises_element: int
    displacement_count: int
    stress_count: int


@dataclass(frozen=True)
class RunSummary:
    workdir: Path
    step_path: Path
    mesh_path: Path
    inp_path: Path
    dat_path: Path
    sta_path: Path
    solver_returncode: int
    element_type: str
    node_count: int
    element_count: int
    fixed_node_count: int
    load_node_count: int
    results: ParsedResults
    theoretical_tip_deflection_mm: float
    theoretical_max_stress_mpa: float
    solid_count: int = 1
    case: BeamCase = field(default_factory=BeamCase)
    source_model_name: str = "悬臂梁验证模型"
    report_path: Path | None = None


@dataclass(frozen=True)
class FieldResults:
    nodes: dict[int, tuple[float, float, float]]
    displacements: dict[int, tuple[float, float, float]]
    elements: dict[int, tuple[int, ...]]
    element_von_mises_mpa: dict[int, float]
    surface_faces: tuple[tuple[int, int, int, int], ...]
