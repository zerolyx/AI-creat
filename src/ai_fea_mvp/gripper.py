from __future__ import annotations

"""Gripper duty-cycle engineering checks.

Encapsulates the ball-valve gripper load case against the Huashu HSR-CR605-790
collaborative robot: rated-payload pre-check, grasp-force estimation from mass /
dynamic factor / friction / grip safety factor, per-jaw share, and the FEA load
recommendation (max of grip force and assembly thrust).
"""

from dataclasses import dataclass

CR605_RATED_PAYLOAD_KG = 5.0
CR605_REACH_MM = 785.0
CR605_REPEATABILITY_MM = 0.02
GRAVITY_M_S2 = 9.80665


@dataclass(frozen=True)
class GripperDuty:
    valve_model: str = "Q41F-16P / 16RL"
    nominal_diameter: str = "DN25"
    workpiece_mass_kg: float = 4.05
    gripper_mass_kg: float = 0.60
    dynamic_factor: float = 1.50
    friction_coefficient: float = 0.30
    grip_safety_factor: float = 2.00
    assembly_force_n: float = 100.0

    @property
    def total_payload_kg(self) -> float:
        """Workpiece + gripper mass [kg]."""
        return self.workpiece_mass_kg + self.gripper_mass_kg

    @property
    def payload_utilization_percent(self) -> float:
        """Total payload as a percentage of the CR605 5 kg rating."""
        return self.total_payload_kg / CR605_RATED_PAYLOAD_KG * 100.0

    @property
    def payload_ok(self) -> bool:
        """True when total payload does not exceed the CR605 rated payload."""
        return self.total_payload_kg <= CR605_RATED_PAYLOAD_KG

    @property
    def required_total_grip_force_n(self) -> float:
        """Required total gripping force from mass, dynamic factor, safety factor and friction [N]."""
        if self.friction_coefficient <= 0:
            raise ValueError("摩擦系数必须大于 0")
        return (
            self.workpiece_mass_kg
            * GRAVITY_M_S2
            * self.dynamic_factor
            * self.grip_safety_factor
            / self.friction_coefficient
        )

    @property
    def force_per_jaw_n(self) -> float:
        """Required grip force per jaw (half of total) [N]."""
        return self.required_total_grip_force_n / 2.0

    @property
    def recommended_fea_force_n(self) -> float:
        """Recommended FEA load: max of required grip force and assembly thrust [N]."""
        return max(self.required_total_grip_force_n, self.assembly_force_n)
