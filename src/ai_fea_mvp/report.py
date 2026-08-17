from __future__ import annotations

"""Chinese Markdown FEA report generation.

Builds the end-user report from a :class:`~ai_fea_mvp.models.RunSummary`: a
one-sentence conclusion, model/mesh table, robot and gripping feasibility check,
material and load summary, solver results, interpretation guidance, credibility
notes and the list of produced files. Pure string generation — no filesystem or
solver interaction.
"""

from datetime import datetime

from .models import RunSummary
from .gripper import CR605_RATED_PAYLOAD_KG, GripperDuty


def generate_markdown_report(summary: RunSummary) -> str:
    case = summary.case
    result = summary.results
    is_assembly = summary.solid_count > 1
    theory_note = (
        "该模型包含多个独立实体，经典悬臂梁理论不适用于该装配体，因此不将理论偏差作为合格判据。"
        if is_assembly
        else "该模型为单实体悬臂梁验证工况，可使用经典梁理论进行趋势对照。"
    )
    safety_factor = (
        case.allowable_stress_mpa / result.max_von_mises_mpa
        if case.allowable_stress_mpa > 0 and result.max_von_mises_mpa > 0
        else None
    )
    safety_text = (
        f"按当前许用应力 **{case.allowable_stress_mpa:g} MPa** 估算，安全系数约为 **{safety_factor:.2f}**。"
        if safety_factor is not None
        else "当前未设置材料许用应力，不能仅凭应力数值自动判定安全或失效。"
    )
    conclusion = (
        f"本次线性静力求解正常完成。最大总位移为 **{result.max_displacement_mm:.6g} mm**，"
        f"最大 von Mises 应力为 **{result.max_von_mises_mpa:.6g} MPa**。"
        f"{safety_text}"
    )
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
    return f"""# {summary.source_model_name} 有限元分析报告

> 自动生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}  
> 分析类型：线性静力分析  
> 单位制：N / mm / MPa

## 1. 一句话结论

{conclusion}

## 2. 模型与网格

| 项目 | 数值 |
|---|---:|
| STEP 模型 | `{summary.source_model_name}` |
| 实体数量 | {summary.solid_count} |
| 单元类型 | {summary.element_type} |
| 节点数量 | {summary.node_count} |
| 单元数量 | {summary.element_count} |
| 网格尺寸 | {case.mesh_size_mm:g} mm |

## 3. 机器人与夹持可行性

| 项目 | 数值 |
|---|---:|
| 应用任务 | {case.application_mode} |
| 机器人 | {case.robot_model} |
| 机器人额定负载 | {CR605_RATED_PAYLOAD_KG:g} kg |
| 球阀 | {case.valve_model} · {case.nominal_diameter} |
| 球阀质量 | {case.workpiece_mass_kg:g} kg |
| 夹爪质量 | {case.gripper_mass_kg:g} kg |
| 总负载 | {duty.total_payload_kg:.3g} kg（占额定 {duty.payload_utilization_percent:.1f}%） |
| 负载检查 | {"通过" if duty.payload_ok else "超出 CR605 额定负载"} |
| 动态系数 | {case.dynamic_factor:g} |
| 摩擦系数 | {case.friction_coefficient:g} |
| 夹持安全系数 | {case.grip_safety_factor:g} |
| 建议总夹持力 | {duty.required_total_grip_force_n:.3g} N |
| 建议单爪力 | {duty.force_per_jaw_n:.3g} N |
| 装配推力 | {case.assembly_force_n:g} N |

球阀重量必须以实物称重或制造商数据为准；型号和公称通径不会替代真实重量输入。
夹爪质量应包含连接法兰、快换盘、传感器以及气管/线缆随动负载。当前示例仅输入夹爪本体质量时，机器人负载余量会被高估。

## 4. 材料与载荷

| 项目 | 数值 |
|---|---:|
| 材料 | {case.material_name} |
| 弹性模量 E | {case.young_mpa:g} MPa |
| 泊松比 | {case.poisson:g} |
| 建议许用应力 | {case.allowable_stress_mpa:g} MPa |
| 总载荷 | {case.force_n:g} N，沿 {case.load_direction} 方向 |
| 固定节点 | {summary.fixed_node_count} |
| 载荷节点 | {summary.load_node_count} |

自动工况将每个实体自身的最小 X 端设为全固定，并将总载荷平均分配到每个实体最大 X 端的节点。
材料参数是面向 FDM 快速筛选的初始值，实际强度会随品牌、含水率、层高、填充率、壁厚和打印方向变化。

## 5. 求解结果

| 结果 | 最大值 | 位置 |
|---|---:|---:|
| 总位移 | {result.max_displacement_mm:.8g} mm | 节点 {result.max_displacement_node} |
| von Mises 应力 | {result.max_von_mises_mpa:.8g} MPa | 单元 {result.max_von_mises_element} |
| 估算安全系数 | {f'{safety_factor:.3g}' if safety_factor is not None else '未设置'} | 许用应力 / 最大应力 |

## 6. 结果怎么理解

- **位移云图**表示结构在载荷作用下移动的程度。数值越大，说明该位置变形越明显。
- **应力云图**表示材料内部受力集中程度。暖色区域需要优先检查，但是否安全仍取决于材料许用应力、制造缺陷和安全系数。
- {theory_note}
- 当前分析未定义实体间接触、螺栓、胶接、摩擦或真实地面支撑。若这些条件与实际产品不同，应重新定义工况后再作工程决策。

## 7. 可信度说明

- CalculiX 求解器返回码：{summary.solver_returncode}
- 位移结果记录：{result.displacement_count}
- 应力结果记录：{result.stress_count}
- 本结果适合自动快速评估和方案比较，不替代经过边界条件复核、网格收敛检查及材料许用值校核的正式工程签核。

## 8. 结果文件

- STEP：`{summary.step_path.name}`
- 网格：`{summary.mesh_path.name}`
- CalculiX 输入：`{summary.inp_path.name}`
- CalculiX 结果：`{summary.dat_path.name}`
- 汇总数据：`summary.json`
"""
