from __future__ import annotations

"""Command-line orchestration.

Ties the pipeline together: :func:`run_cantilever` drives geometry → mesh →
CalculiX input → solver → result parse → report for one load case, persists a
``summary.json`` in the run directory, and exposes a small ``argparse`` CLI
(:func:`main`) plus :func:`load_run_summary` for the GUI/worker to read runs
back. This is the only module that owns the sequencing of the whole analysis.
"""

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from shutil import copy2
from typing import Callable

from .calculix import run_calculix, write_calculix_input
from .geometry import create_cantilever_step
from .gripper import GripperDuty
from .mesh import mesh_step_cantilever
from .models import BeamCase, ParsedResults, RunSummary
from .results import parse_dat_results
from .report import generate_markdown_report


def run_cantilever(
    workdir: Path,
    ccx_path: Path,
    case: BeamCase | None = None,
    source_step: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> RunSummary:
    case = case or BeamCase()
    if case.application_mode == "球阀装配夹爪":
        duty = GripperDuty(
            valve_model=case.valve_model,
            nominal_diameter=case.nominal_diameter,
            workpiece_mass_kg=case.workpiece_mass_kg,
            gripper_mass_kg=case.gripper_mass_kg,
            dynamic_factor=case.dynamic_factor,
            friction_coefficient=case.friction_coefficient,
            grip_safety_factor=case.grip_safety_factor,
            assembly_force_n=case.assembly_force_n,
        )
        if not duty.payload_ok:
            raise ValueError(
                f"球阀与夹爪总质量 {duty.total_payload_kg:.3g} kg 超过 "
                f"HSR-CR605-790 的额定负载 5 kg"
            )
        case = BeamCase(**{**case.__dict__, "force_n": duty.recommended_fea_force_n})
    report = progress or (lambda _message: None)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = workdir / f"cantilever_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)

    step_path = run_dir / "cantilever_beam.step"
    mesh_path = run_dir / "cantilever_beam.msh"
    inp_path = run_dir / "cantilever_beam.inp"
    log_path = run_dir / "ccx.log"

    report("正在准备 STEP 模型")
    if source_step is None:
        create_cantilever_step(case, step_path)
    else:
        if not source_step.exists():
            raise FileNotFoundError(source_step)
        copy2(source_step, step_path)
    report("STEP 已导入，正在生成四面体网格")
    mesh = mesh_step_cantilever(case, step_path, mesh_path)
    report(f"网格完成：{mesh.solid_count} 个实体，{len(mesh.nodes)} 节点，{len(mesh.elements)} 单元")
    write_calculix_input(case, mesh, inp_path)
    report("边界条件和末端载荷已写入 CalculiX 输入文件")
    report("正在调用 CalculiX 求解")
    returncode = run_calculix(ccx_path, inp_path, log_path)

    dat_path = run_dir / "cantilever_beam.dat"
    sta_path = run_dir / "cantilever_beam.sta"
    if returncode != 0:
        raise RuntimeError(f"CalculiX returned {returncode}; see {log_path}")
    if not sta_path.exists() or not dat_path.exists() or dat_path.stat().st_size == 0:
        raise RuntimeError(f"CalculiX output files are incomplete; see {log_path}")

    results = parse_dat_results(dat_path)
    report("求解完成，正在解析位移和 von Mises 应力")
    report_path = run_dir / "分析报告.md"
    summary = RunSummary(
        workdir=run_dir,
        step_path=step_path,
        mesh_path=mesh_path,
        inp_path=inp_path,
        dat_path=dat_path,
        sta_path=sta_path,
        solver_returncode=returncode,
        element_type=mesh.element_type,
        node_count=len(mesh.nodes),
        element_count=len(mesh.elements),
        fixed_node_count=len(mesh.fixed_nodes),
        load_node_count=len(mesh.load_nodes),
        results=results,
        theoretical_tip_deflection_mm=case.theoretical_tip_deflection_mm,
        theoretical_max_stress_mpa=case.theoretical_max_stress_mpa,
        solid_count=mesh.solid_count,
        case=case,
        source_model_name=source_step.stem if source_step is not None else "悬臂梁验证模型",
        report_path=report_path,
    )
    report_path.write_text(generate_markdown_report(summary), encoding="utf-8-sig")
    report("中文 Markdown 分析报告已生成")
    (run_dir / "summary.json").write_text(
        json.dumps(asdict(summary), indent=2, default=str),
        encoding="utf-8",
    )
    return summary


def load_run_summary(summary_path: Path) -> RunSummary:
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    return RunSummary(
        workdir=Path(data["workdir"]),
        step_path=Path(data["step_path"]),
        mesh_path=Path(data["mesh_path"]),
        inp_path=Path(data["inp_path"]),
        dat_path=Path(data["dat_path"]),
        sta_path=Path(data["sta_path"]),
        solver_returncode=int(data["solver_returncode"]),
        element_type=data["element_type"],
        node_count=int(data["node_count"]),
        element_count=int(data["element_count"]),
        fixed_node_count=int(data["fixed_node_count"]),
        load_node_count=int(data["load_node_count"]),
        results=ParsedResults(**data["results"]),
        theoretical_tip_deflection_mm=float(data["theoretical_tip_deflection_mm"]),
        theoretical_max_stress_mpa=float(data["theoretical_max_stress_mpa"]),
        solid_count=int(data.get("solid_count", 1)),
        case=BeamCase(**data.get("case", {})),
        source_model_name=data.get("source_model_name", "悬臂梁验证模型"),
        report_path=Path(data["report_path"]) if data.get("report_path") else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", default="runs", type=Path)
    parser.add_argument("--ccx", required=True, type=Path)
    defaults = BeamCase()
    parser.add_argument("--source-step", type=Path)
    parser.add_argument("--length", type=float, default=defaults.length_mm)
    parser.add_argument("--height", type=float, default=defaults.height_mm)
    parser.add_argument("--width", type=float, default=defaults.width_mm)
    parser.add_argument("--force", type=float, default=defaults.force_n)
    parser.add_argument("--young", type=float, default=defaults.young_mpa)
    parser.add_argument("--poisson", type=float, default=defaults.poisson)
    parser.add_argument("--mesh-size", type=float, default=defaults.mesh_size_mm)
    parser.add_argument("--material", default=defaults.material_name)
    parser.add_argument("--allowable-stress", type=float, default=defaults.allowable_stress_mpa)
    parser.add_argument("--load-direction", default=defaults.load_direction)
    parser.add_argument("--application-mode", default=defaults.application_mode)
    parser.add_argument("--robot-model", default=defaults.robot_model)
    parser.add_argument("--valve-model", default=defaults.valve_model)
    parser.add_argument("--nominal-diameter", default=defaults.nominal_diameter)
    parser.add_argument("--workpiece-mass", type=float, default=defaults.workpiece_mass_kg)
    parser.add_argument("--gripper-mass", type=float, default=defaults.gripper_mass_kg)
    parser.add_argument("--dynamic-factor", type=float, default=defaults.dynamic_factor)
    parser.add_argument("--friction-coefficient", type=float, default=defaults.friction_coefficient)
    parser.add_argument("--grip-safety-factor", type=float, default=defaults.grip_safety_factor)
    parser.add_argument("--assembly-force", type=float, default=defaults.assembly_force_n)
    args = parser.parse_args(argv)

    case = BeamCase(
        length_mm=args.length,
        height_mm=args.height,
        width_mm=args.width,
        force_n=args.force,
        young_mpa=args.young,
        poisson=args.poisson,
        mesh_size_mm=args.mesh_size,
        material_name=args.material,
        allowable_stress_mpa=args.allowable_stress,
        load_direction=args.load_direction,
        application_mode=args.application_mode,
        robot_model=args.robot_model,
        valve_model=args.valve_model,
        nominal_diameter=args.nominal_diameter,
        workpiece_mass_kg=args.workpiece_mass,
        gripper_mass_kg=args.gripper_mass,
        dynamic_factor=args.dynamic_factor,
        friction_coefficient=args.friction_coefficient,
        grip_safety_factor=args.grip_safety_factor,
        assembly_force_n=args.assembly_force,
    )
    summary = run_cantilever(
        args.workdir,
        args.ccx,
        case=case,
        source_step=args.source_step,
        progress=print,
    )
    r = summary.results
    print(f"run_dir={summary.workdir}")
    print(f"element_type={summary.element_type}")
    print(f"nodes={summary.node_count}")
    print(f"elements={summary.element_count}")
    print(f"fixed_nodes={summary.fixed_node_count}")
    print(f"load_nodes={summary.load_node_count}")
    print(f"solid_count={summary.solid_count}")
    print(f"max_displacement_mm={r.max_displacement_mm:.8g} node={r.max_displacement_node}")
    print(f"theory_tip_deflection_mm={summary.theoretical_tip_deflection_mm:.8g}")
    disp_error = (
        (r.max_displacement_mm - summary.theoretical_tip_deflection_mm)
        / summary.theoretical_tip_deflection_mm
        * 100.0
    )
    print(f"tip_deflection_error_percent={disp_error:.3f}")
    print(f"max_von_mises_mpa={r.max_von_mises_mpa:.8g} element={r.max_von_mises_element}")
    print(f"theory_max_stress_mpa={summary.theoretical_max_stress_mpa:.8g}")
    stress_error = (
        (r.max_von_mises_mpa - summary.theoretical_max_stress_mpa)
        / summary.theoretical_max_stress_mpa
        * 100.0
    )
    print(f"max_stress_error_percent={stress_error:.3f}")
    return 0
