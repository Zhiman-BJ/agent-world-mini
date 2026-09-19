"""Run the fixed meshio -> scikit-fem Poisson pilot; no network is required."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

import meshio
import numpy as np
from skfem import BilinearForm, CellBasis, ElementTriP1, Functional, LinearForm, asm
from skfem.helpers import dot, grad
from skfem.io.meshio import from_meshio, to_meshio
from skfem.utils import condense, solve


CORE_TOOL_REFS = [
    "meshio._mesh.Mesh.__init__",
    "meshio._mesh.Mesh.get_cells_type",
    "meshio._mesh.Mesh.get_cell_data",
    "meshio._helpers.read",
    "meshio._helpers.write",
    "skfem.io.meshio.from_meshio",
    "skfem.io.meshio.to_meshio",
    "skfem.mesh.mesh.Mesh.with_boundaries",
    "skfem.mesh.mesh.Mesh.with_subdomains",
    "skfem.mesh.mesh.Mesh.is_valid",
    "skfem.mesh.mesh.Mesh.refined",
    "skfem.mesh.mesh.Mesh.p",
    "skfem.mesh.mesh.Mesh.nelements",
    "skfem.mesh.mesh.Mesh.nvertices",
    "skfem.assembly.basis.cell_basis.CellBasis.__init__",
    "skfem.assembly.basis.abstract_basis.AbstractBasis.get_dofs",
    "skfem.assembly.basis.abstract_basis.AbstractBasis.complement_dofs",
    "skfem.assembly.basis.abstract_basis.AbstractBasis.interpolate",
    "skfem.assembly.dofs.DofsView.all",
    "skfem.assembly.form.form.Form.__init__",
    "skfem.assembly.asm",
    "skfem.helpers.dot",
    "skfem.helpers.grad",
    "skfem.utils.condense",
    "skfem.utils.solve",
]

RUNTIME_INFRASTRUCTURE = [
    {
        "entry": "skfem.ElementTriP1()",
        "reason": "Class is selected; its parameterless constructor is inherited from object and has no source-defined __init__ to index.",
    },
    {
        "entry": "skfem.BilinearForm / skfem.LinearForm / skfem.Functional constructors",
        "reason": "All three concrete classes are selected and inherit the selected Form.__init__; construction does not add invented child methods.",
    },
    {
        "entry": "skfem.MeshTri1 generated dataclass constructor",
        "reason": "Selected concrete class; from_meshio constructs it internally. Generated __init__ is absent in the static source index.",
    },
    {
        "entry": "meshio.Mesh.points / point_data, skfem.Mesh.t, FormExtraParams.x / uh, DiscreteField ndarray arithmetic",
        "reason": "Data attributes passed along the documented object chain, not additional callable tools.",
    },
    {
        "entry": "numpy arrays, arithmetic and linalg.norm; scipy sparse indexing and matrix-vector product",
        "reason": "Numerical runtime and verifier infrastructure; pinned through the recorded installed versions, not counted as domain tools.",
    },
    {
        "entry": "Python argparse/pathlib/json/hashlib/importlib.metadata/platform/sys",
        "reason": "CLI, artifact serialization, file hashes and version reporting only.",
    },
]


def unit_square_mesh(n: int) -> meshio.Mesh:
    """Deterministic fixture, two triangles per grid square, z=0."""
    axis = np.linspace(0.0, 1.0, n + 1)
    points = np.array([(x, y, 0.0) for y in axis for x in axis])
    triangles = []
    for row in range(n):
        for col in range(n):
            a = row * (n + 1) + col
            b, c, d = a + 1, a + n + 1, a + n + 2
            triangles.extend([(a, b, d), (a, d, c)])
    triangles = np.asarray(triangles, dtype=np.int32)
    centroids = points[triangles].mean(axis=1)
    regions = np.where(centroids[:, 0] < 0.5, 1, 2)
    return meshio.Mesh(points, [("triangle", triangles)], cell_data={"region_id": [regions]})


def exact_solution(x):
    return np.sin(np.pi * x[0]) * np.sin(np.pi * x[1])


def solve_case(mesh, source_scale: float):
    basis = CellBasis(mesh, ElementTriP1(), intorder=6)

    def stiffness(u, v, w):
        return dot(grad(u), grad(v))

    def load(v, w):
        return source_scale * 2.0 * np.pi**2 * exact_solution(w.x) * v

    def squared_error(w):
        return (w.uh - exact_solution(w.x)) ** 2

    A = asm(BilinearForm(stiffness), basis)
    b = asm(LinearForm(load), basis)
    boundary_dofs = basis.get_dofs("outer").all()
    interior_dofs = basis.complement_dofs(boundary_dofs)
    u = solve(*condense(A, b, D=boundary_dofs))
    residual = A @ u - b
    residual_relative = float(np.linalg.norm(residual[interior_dofs]) / max(np.linalg.norm(b[interior_dofs]), 1.0))
    l2_error = float(np.sqrt(asm(Functional(squared_error), basis, uh=basis.interpolate(u))))
    nodal_error = float(np.max(np.abs(u - exact_solution(mesh.p))))
    boundary_max = float(np.max(np.abs(u[boundary_dofs])))
    metrics = {
        "source_scale": source_scale,
        "nodes": int(mesh.nvertices),
        "elements": int(mesh.nelements),
        "dirichlet_dofs": int(boundary_dofs.size),
        "free_dofs": int(interior_dofs.size),
        "relative_interior_residual": residual_relative,
        "l2_error": l2_error,
        "max_nodal_error": nodal_error,
        "boundary_max_abs": boundary_max,
        "solution_min": float(u.min()),
        "solution_max": float(u.max()),
    }
    return u, metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("seed_gen/scenario_pilot/runtime/mesh"))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    mesh = unit_square_mesh(16)
    input_path = output / "unit_square_16.vtu"
    meshio.write(input_path, mesh)
    loaded = meshio.read(input_path)
    np.testing.assert_allclose(loaded.points, mesh.points, rtol=0, atol=0)
    np.testing.assert_array_equal(loaded.get_cells_type("triangle"), mesh.get_cells_type("triangle"))
    np.testing.assert_array_equal(loaded.get_cell_data("region_id", "triangle"), mesh.get_cell_data("region_id", "triangle"))
    fem_mesh = from_meshio(loaded).with_boundaries({
        "outer": lambda x: np.isclose(x[0], 0) | np.isclose(x[0], 1) | np.isclose(x[1], 0) | np.isclose(x[1], 1)
    }).with_subdomains({"left": lambda x: x[0] < 0.5, "right": lambda x: x[0] >= 0.5})
    assert fem_mesh.is_valid(), "invalid coarse mesh"
    coarse_u, coarse = solve_case(fem_mesh, 1.0)
    fine_mesh = fem_mesh.refined()
    assert fine_mesh.is_valid(), "invalid refined mesh"
    fine_u, fine = solve_case(fine_mesh, 1.0)
    failed_u, failure = solve_case(fine_mesh, 0.0)
    fine_path = output / "poisson_solution_32.vtu"
    meshio.write(fine_path, to_meshio(fine_mesh, point_data={"u": fine_u, "u_exact": exact_solution(fine_mesh.p)}))
    reread = meshio.read(fine_path)
    np.testing.assert_allclose(reread.point_data["u"], fine_u, rtol=0, atol=0)
    reread_mesh = from_meshio(reread)
    np.testing.assert_array_equal(reread_mesh.boundaries["outer"], fine_mesh.boundaries["outer"])
    for region in ("left", "right"):
        np.testing.assert_array_equal(reread_mesh.subdomains[region], fine_mesh.subdomains[region])
    error_ratio = fine["l2_error"] / coarse["l2_error"]
    assertions = {
        "meshio_points_cells_and_region_data_roundtrip": True,
        "solution_and_named_boundaries_subdomains_roundtrip": True,
        "coarse_and_fine_mesh_valid": True,
        "fine_l2_error_below_0_002": fine["l2_error"] < 0.002,
        "error_reduction_ratio_below_0_30": error_ratio < 0.30,
        "fine_relative_interior_residual_below_1e_10": fine["relative_interior_residual"] < 1e-10,
        "fine_dirichlet_boundary_below_1e_12": fine["boundary_max_abs"] < 1e-12,
        "fine_max_nodal_error_below_0_01": fine["max_nodal_error"] < 0.01,
        "zero_source_wrong_problem_passes_residual": failure["relative_interior_residual"] < 1e-10,
        "zero_source_wrong_problem_rejected_by_analytic_error": failure["l2_error"] > 0.49,
        "fixed_source_reduces_error_by_factor_over_100": failure["l2_error"] / fine["l2_error"] > 100,
        "all_numeric_metrics_finite": all(np.isfinite(value) for metrics in (coarse, fine, failure) for value in metrics.values()),
    }
    evidence = {
        "scenario_id": "02.03.03",
        "status": "passed" if all(assertions.values()) else "failed",
        "command": [sys.executable, "seed_gen/scenario_pilot/verify_mesh.py", "--output-dir", str(args.output_dir)],
        "python_version": platform.python_version(),
        "versions": {name: importlib.metadata.version(name) for name in ("meshio", "scikit-fem", "numpy", "scipy")},
        "fixture": {"domain": "[0,1] x [0,1]", "mesh": "16 x 16 Cartesian cells split into 512 triangles", "fine_mesh": "one uniform refinement: 2048 triangles", "operator": "-Laplacian(u)=2*pi^2*sin(pi*x)*sin(pi*y)", "dirichlet": "u=0 on all four edges", "exact_solution": "sin(pi*x)*sin(pi*y)", "units": "dimensionless manufactured verification problem; not a calibrated thermal device", "quadrature_order": 6, "randomness": "none"},
        "task_results": [
            {"id": "mesh_poisson_refine", "status": "passed" if all(assertions.values()) else "failed", "coarse": coarse, "fine": fine, "l2_error_ratio": error_ratio},
            {"id": "mesh_repair_missing_source", "status": "passed" if all(assertions.values()) else "failed", "injected_failure": "source_scale=0, which solves a different PDE while achieving zero algebraic residual", "expected_failed_assertion": "l2_error < 0.002", "before": failure, "repair": "restore source_scale=1", "after": fine},
        ],
        "assertions": assertions,
        "core_tool_refs": CORE_TOOL_REFS,
        "runtime_infrastructure": RUNTIME_INFRASTRUCTURE,
        "object_dataflow": ["fixed points/cells/region_id -> meshio.Mesh", "meshio.write/read -> VTU roundtrip", "from_meshio -> MeshTri1 -> named boundaries/subdomains", "Mesh.refined -> CellBasis(ElementTriP1)", "BilinearForm/LinearForm -> asm -> sparse matrix and load", "get_dofs/condense/solve -> nodal solution", "interpolate/Functional/asm -> integrated analytic error", "to_meshio/write/read -> field and tag roundtrip"],
        "files": [{"path": str(path.relative_to(Path.cwd())), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size} for path in (input_path, fine_path)],
        "unverified": ["Other selected APIs beyond this exact tool path", "3D, mixed elements, transient integration, Neumann/Robin and variable-coefficient material problems", "TiN heater geometry, calibrated material data, optical eigenmode and phase shift", "Interactive environment wrappers, entity IDs/reset/permissions and agent policies are not implemented in this smoke run"],
    }
    (output / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "fine_l2_error": fine["l2_error"], "error_ratio": error_ratio, "failure_l2_error": failure["l2_error"], "passed_assertions": sum(assertions.values()), "total_assertions": len(assertions), "report": str(output / "verification.json")}, ensure_ascii=False))
    assert all(assertions.values()), assertions


if __name__ == "__main__":
    main()
