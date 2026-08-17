# Cantilever MVP Validation

Date: 2026-07-21

## Real Toolchain Used

| Tool | Version / path |
| --- | --- |
| Python | `C:\tmp\AI-FEA-Assistant-MVP\.venv\Scripts\python.exe` |
| Gmsh Python package | 4.15.2 |
| CalculiX | 2.22, `tools\CalculiX-2.22.0-win-x64\CalculiX-2.22.0-win-x64\bin\ccx.exe` |

## Final Successful Run

Run directory:

`C:\tmp\AI-FEA-Assistant-MVP\runs\cantilever_20260721_121248`

Generated files include:

- `cantilever_beam.step`
- `cantilever_beam.msh`
- `cantilever_beam.inp`
- `cantilever_beam.dat`
- `cantilever_beam.frd`
- `cantilever_beam.sta`
- `ccx.log`
- `summary.json`

## Model

Units: N, mm, MPa

| Parameter | Value |
| --- | ---: |
| Length | 100 mm |
| Cross-section height | 10 mm |
| Cross-section width | 10 mm |
| Material | Linear elastic steel |
| Young's modulus | 210000 MPa |
| Poisson ratio | 0.30 |
| End force | 100 N in negative Y |
| Constraint | Fixed face at x = 0 |
| Load | Nodal forces distributed across x = 100 end-face nodes |
| Element | C3D4 tetrahedra |
| Nodes | 190 |
| Elements | 434 |

## Results

| Result | FEA | Theory | Error |
| --- | ---: | ---: | ---: |
| Max displacement | 0.11264926 mm | 0.19047619 mm | -40.859% |
| Max von Mises stress | 45.945415 MPa | 60.0 MPa | -23.424% |

## Notes

- This is a real closed loop: STEP export, STEP import, tetrahedral mesh,
  fixed boundary, end load, CalculiX solve, `.dat` parsing, and theory
  comparison all ran on this machine.
- The MVP defaults to C3D4 because the Gmsh-to-CalculiX C3D10 midside-node
  mapping currently triggers `nonpositive jacobian` in CalculiX.
- The C3D4 coarse mesh is too stiff for a slender bending beam. The next
  validation step must add mesh refinement studies and fix C3D10 before
  claiming engineering-grade accuracy.
- The original requested Chinese workspace path was usable for file storage,
  but Python/venv execution through this toolchain showed path encoding issues.
  The real solver run was moved to ASCII path `C:\tmp\AI-FEA-Assistant-MVP`.
