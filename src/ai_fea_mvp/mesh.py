from __future__ import annotations

"""Gmsh tetrahedral meshing pipeline.

Loads a STEP assembly, identifies per-volume X-min fixed and X-max load end
faces, writes the CalculiX MSH with physical groups (SOLID / FIXED_FACE /
LOAD_FACE), generates a first-order/second-order tetrahedral mesh, converts
Gmsh element orientation to CalculiX convention (including C3D10 midside-node
remap), and extracts the fixed/load boundary node sets. Returns the
:class:`~ai_fea_mvp.models.MeshData` consumed by the CalculiX input writer.
"""

from pathlib import Path

import gmsh

from .models import BeamCase, MeshData


def _signed_tet_volume(
    nodes: dict[int, tuple[float, float, float]], conn: tuple[int, ...]
) -> float:
    p1 = nodes[conn[0]]
    p2 = nodes[conn[1]]
    p3 = nodes[conn[2]]
    p4 = nodes[conn[3]]
    ax, ay, az = (p2[i] - p1[i] for i in range(3))
    bx, by, bz = (p3[i] - p1[i] for i in range(3))
    cx, cy, cz = (p4[i] - p1[i] for i in range(3))
    cross = (by * cz - bz * cy, bz * cx - bx * cz, bx * cy - by * cx)
    return (ax * cross[0] + ay * cross[1] + az * cross[2]) / 6.0


def _gmsh_to_calculix_tet(
    nodes: dict[int, tuple[float, float, float]], conn: tuple[int, ...]
) -> tuple[int, ...]:
    if len(conn) == 4:
        return conn
    # Gmsh and CalculiX use opposite tetrahedral orientation in this pipeline.
    # Swap vertices 2 and 3, and remap C3D10 midside nodes to the new edges.
    if _signed_tet_volume(nodes, conn) <= 0.0:
        return conn
    if len(conn) == 10:
        return (
            conn[0],
            conn[2],
            conn[1],
            conn[3],
            conn[6],
            conn[5],
            conn[4],
            conn[7],
            conn[9],
            conn[8],
        )
    raise RuntimeError(f"Unsupported tetrahedral node count: {len(conn)}")


def mesh_step_cantilever(case: BeamCase, step_path: Path, mesh_path: Path) -> MeshData:
    if not step_path.exists():
        raise FileNotFoundError(step_path)

    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    if mesh_path.exists():
        mesh_path.unlink()

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("cantilever_beam_mesh")
        gmsh.merge(str(step_path))
        gmsh.model.occ.synchronize()

        volumes = gmsh.model.getEntities(3)
        if not volumes:
            raise RuntimeError("STEP 中没有可网格化的实体体积")

        volume_boxes = [gmsh.model.occ.getBoundingBox(3, tag) for _dim, tag in volumes]
        x_min = min(box[0] for box in volume_boxes)
        x_max = max(box[3] for box in volume_boxes)
        x_span = x_max - x_min
        coordinate_tol = max(x_span * 1.0e-6, case.mesh_size_mm * 1.0e-4, 1.0e-7)

        fixed_surfaces: list[int] = []
        load_surfaces: list[int] = []
        fixed_x_positions: list[float] = []
        load_x_positions: list[float] = []
        # Select end faces per volume. This keeps every disconnected solid
        # restrained in the automatic quick-evaluation load case.
        for (_dim, volume_tag), volume_box in zip(volumes, volume_boxes):
            volume_x_min, volume_x_max = volume_box[0], volume_box[3]
            boundary = gmsh.model.getBoundary([(3, volume_tag)], oriented=False, recursive=False)
            volume_fixed: list[int] = []
            volume_load: list[int] = []
            for _surface_dim, surface_tag in boundary:
                surface_box = gmsh.model.occ.getBoundingBox(2, surface_tag)
                if abs(surface_box[0] - volume_x_min) <= coordinate_tol and abs(surface_box[3] - volume_x_min) <= coordinate_tol:
                    volume_fixed.append(surface_tag)
                if abs(surface_box[0] - volume_x_max) <= coordinate_tol and abs(surface_box[3] - volume_x_max) <= coordinate_tol:
                    volume_load.append(surface_tag)
            if not volume_fixed or not volume_load:
                boundary_centers = {
                    surface_tag: (gmsh.model.occ.getBoundingBox(2, surface_tag)[0] + gmsh.model.occ.getBoundingBox(2, surface_tag)[3]) / 2.0
                    for _surface_dim, surface_tag in boundary
                }
                if not volume_fixed and boundary_centers:
                    nearest = min(boundary_centers.values(), key=lambda value: abs(value - volume_x_min))
                    volume_fixed = [tag for tag, value in boundary_centers.items() if abs(value - nearest) <= coordinate_tol]
                if not volume_load and boundary_centers:
                    nearest = min(boundary_centers.values(), key=lambda value: abs(value - volume_x_max))
                    volume_load = [tag for tag, value in boundary_centers.items() if abs(value - nearest) <= coordinate_tol]
            fixed_surfaces.extend(volume_fixed)
            load_surfaces.extend(volume_load)
            fixed_x_positions.append(volume_x_min)
            load_x_positions.append(volume_x_max)

        fixed_surfaces = sorted(set(fixed_surfaces))
        load_surfaces = sorted(set(load_surfaces))

        if not fixed_surfaces or not load_surfaces:
            raise RuntimeError(
                f"Could not identify end faces. fixed={fixed_surfaces}, load={load_surfaces}"
            )

        gmsh.model.addPhysicalGroup(3, [tag for _dim, tag in volumes], 1)
        gmsh.model.setPhysicalName(3, 1, "SOLID")
        gmsh.model.addPhysicalGroup(2, fixed_surfaces, 2)
        gmsh.model.setPhysicalName(2, 2, "FIXED_FACE")
        gmsh.model.addPhysicalGroup(2, load_surfaces, 3)
        gmsh.model.setPhysicalName(2, 3, "LOAD_FACE")

        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", case.mesh_size_mm)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", case.mesh_size_mm)
        gmsh.option.setNumber("Mesh.ElementOrder", case.element_order)
        gmsh.option.setNumber("Mesh.HighOrderOptimize", 1)
        gmsh.model.mesh.generate(3)
        gmsh.write(str(mesh_path))

        node_tags, coords, _params = gmsh.model.mesh.getNodes()
        nodes: dict[int, tuple[float, float, float]] = {}
        for index, tag in enumerate(node_tags):
            nodes[int(tag)] = (
                float(coords[3 * index]),
                float(coords[3 * index + 1]),
                float(coords[3 * index + 2]),
            )

        elem_types, elem_tags, elem_nodes = gmsh.model.mesh.getElements(3)
        elements: dict[int, tuple[int, ...]] = {}
        element_type = ""
        for elem_type, tags, connectivity in zip(elem_types, elem_tags, elem_nodes):
            name, _dim, order, node_count, _local_coords, _ = gmsh.model.mesh.getElementProperties(elem_type)
            if name not in {"Tetrahedron 10", "Tetrahedron 4"}:
                continue
            element_type = "C3D10" if node_count == 10 else "C3D4"
            for i, elem_id in enumerate(tags):
                start = i * node_count
                end = start + node_count
                elements[int(elem_id)] = tuple(int(n) for n in connectivity[start:end])

        if not elements:
            raise RuntimeError("No tetrahedral volume elements were generated")

        elements = {
            elem_id: _gmsh_to_calculix_tet(nodes, conn) for elem_id, conn in elements.items()
        }

        tol = coordinate_tol
        fixed_nodes = sorted(
            node for node, (x, _y, _z) in nodes.items()
            if any(abs(x - end_x) <= tol for end_x in fixed_x_positions)
        )
        load_nodes = sorted(
            node for node, (x, _y, _z) in nodes.items()
            if any(abs(x - end_x) <= tol for end_x in load_x_positions)
        )
    finally:
        gmsh.finalize()

    if not mesh_path.exists() or mesh_path.stat().st_size == 0:
        raise RuntimeError(f"Mesh export failed: {mesh_path}")
    if not fixed_nodes or not load_nodes:
        raise RuntimeError(
            f"Boundary node extraction failed. fixed={len(fixed_nodes)}, load={len(load_nodes)}"
        )

    return MeshData(
        nodes=nodes,
        elements=elements,
        fixed_nodes=fixed_nodes,
        load_nodes=load_nodes,
        element_type=element_type,
        mesh_path=mesh_path,
        solid_count=len(volumes),
    )
