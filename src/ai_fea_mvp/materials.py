from __future__ import annotations

"""FDM material presets for quick screening.

Provides conservative starting values (Young's modulus, Poisson ratio,
allowable stress) for common FDM filaments. These are screening defaults only —
actual strength varies with brand, moisture, raster direction and process
settings, and must be verified before any sign-off.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MaterialPreset:
    young_mpa: float
    poisson: float
    allowable_stress_mpa: float
    note: str


# Conservative starting values for quick screening of common FDM prints.
# Actual strength varies with brand, moisture, raster direction and process settings.
MATERIAL_PRESETS: dict[str, MaterialPreset] = {
    "PLA": MaterialPreset(3500.0, 0.36, 30.0, "刚度高，适合原型与常温结构件"),
    "PLA+": MaterialPreset(3000.0, 0.36, 32.0, "韧性优于普通 PLA，配方差异较大"),
    "PETG": MaterialPreset(2100.0, 0.38, 25.0, "韧性与层间结合较好，适合功能件"),
    "ABS": MaterialPreset(2200.0, 0.35, 22.0, "耐热与抗冲击较好，注意翘曲"),
    "ASA": MaterialPreset(2300.0, 0.35, 24.0, "耐候性好，适合户外功能件"),
    "尼龙 PA": MaterialPreset(1700.0, 0.39, 24.0, "韧性高，吸湿会显著影响性能"),
    "PA-CF": MaterialPreset(6000.0, 0.34, 45.0, "高刚度，性能高度依赖纤维与打印方向"),
    "PC": MaterialPreset(2400.0, 0.37, 35.0, "耐热耐冲击，需要较高打印温度"),
    "TPU 95A": MaterialPreset(35.0, 0.48, 6.0, "柔性材料，线性小变形模型仅适合初筛"),
}
