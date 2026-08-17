# Valve Assembly Project Baseline

Use this reference only as a project working baseline. Confirm every value against the active robot, PLC, vision, and point-table files before using it on hardware.

## System Scope

- Six-axis collaborative robot, reported as Huazhong/Huashu CR605 with a third-generation controller.
- Robot programs in `.PRG` files.
- VisionMaster or Hikvision 2D vision.
- PLC/TIA project, pneumatic gripper IO, fixtures, simulation, point tables, and register planning.
- Flow: locate material, pick studs/bolts/washers/nuts/upper valve body, transfer to the assembly area, assemble and tighten.

## Routine Map To Verify

| Routine | Working purpose |
| --- | --- |
| `MAIN.PRG` | Main sequence |
| `FW.PRG` | Reset |
| `QL.PRG` | General pick |
| `FL.PRG` | General place |
| `FL_UP.PRG` | Slow upper-valve-body insertion |
| `TURN.PRG` | Repeated clamp, turn/press, release, return tightening action |
| `QFLZ.PRG` | Stud or bolt flow |
| `QFFT.PRG` | Upper valve body flow; do not assume it means lower valve body |
| `QFPD.PRG` | Washer flow |
| `QFLM.PRG` | Nut flow |
| `SJZC.PRG` | Vision trigger and result transfer |
| `SZ1J.PRG` | Gripper close |
| `SZ1S.PRG` | Gripper open |

## Preliminary Register Plan

| Register | Proposed purpose |
| --- | --- |
| `R[63]` | Gripper mode: 0 external grip, 1 internal expansion |
| `R[64]` | Tightening loop target count |
| `R[65]` | Current target identifier |
| `R[66]` | Tightening loop counter |
| `R[68]` | Washer grip mode: 0 external grip, 1 internal expansion |
| `R[99]` | Workpiece type |
| `R[100]` | Vision trigger |
| `R[101]` - `R[106]` | Returned vision pose components |
| `R[107]` | Vision completion indicator |

Proposed `R[99]` types: 1 stud/bolt, 2 nut, 3 washer, 4 upper valve body, 9 tightening tool or special action.

Proposed `R[65]` targets: 40 and 41 studs/bolts, 42 and 43 nuts, 44 and 45 washers, 48 upper valve body.

## Gripper Versions

`P4` is a current test gripper rather than a final design. Any `P5`, `P6`, or `P7` revision can change grip mode, part orientation, TCP, IO logic, pick/place paths, tightening clearance, and the affected routines. Evaluate each revision for stable gripping, drop risk, collision clearance, assembly compatibility, ease of manufacture, ease of tuning, and a fallback.

## Controlled Field Test Order

1. Test gripper open (`SZ1S.PRG`).
2. Test gripper close (`SZ1J.PRG`).
3. Test external and internal grip modes separately.
4. Test general pick (`QL.PRG`).
5. Test general place (`FL.PRG`).
6. Test slow upper-body insertion (`FL_UP.PRG`).
7. Test tightening (`TURN.PRG`).
8. Test each part flow: stud/bolt, upper valve body, washer, then nut.
9. Test `MAIN.PRG` only after the preceding routines pass safely.

Use low speed, single-step execution, and no-load or half-physical tests for every new motion. Confirm tool/work frames, actual payload, IO polarity, interlocks, physical clearance, and emergency-stop readiness before motion.

## Field Record

```text
Date/time:
Program and version:
Robot/controller:
Tool and work frame:
Part and gripper version:
Test goal:
Initial R/DI/DO values:
Vision/PLC values:
Speed and test mode:
Observed result:
Failure point or evidence:
Single variable changed:
Rollback version:
Next safe test:
```
