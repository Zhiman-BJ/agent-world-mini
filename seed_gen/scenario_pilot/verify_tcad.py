"""Run two fixed, local DEVSIM pilot fixtures; record evidence, not a benchmark."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def build_mesh(ds, *, device, length, spacing, midpoint=None):
    ds.create_1d_mesh(mesh="fixture_mesh")
    ds.add_1d_mesh_line(mesh="fixture_mesh", pos=0, ps=spacing, tag="top")
    if midpoint is not None:
        ds.add_1d_mesh_line(mesh="fixture_mesh", pos=midpoint, ps=1e-9, tag="mid")
    ds.add_1d_mesh_line(mesh="fixture_mesh", pos=length, ps=spacing, tag="bot")
    ds.add_1d_region(mesh="fixture_mesh", material="Si", region="bulk", tag1="top", tag2="bot")
    for contact in ("top", "bot"):
        ds.add_1d_contact(mesh="fixture_mesh", name=contact, tag=contact, material="metal")
    ds.finalize_mesh(mesh="fixture_mesh")
    ds.create_device(mesh="fixture_mesh", device=device)


def solve_checked(ds, **kwargs):
    info = ds.solve(type="dc", info=True, **kwargs)
    check(isinstance(info, dict), "solve(info=True) must return a dict in this release")
    check(info.get("converged") is True, f"DC solve did not converge: {info}")
    return info


def pn_fixture(ds, helpers, model, ramp, output):
    ds.reset_devsim()
    check(ds.get_parameter(name="info")["extended_precision"], "Extended precision is required for this high-doping PN fixture")
    # Official release example gmsh_diode3d_float128.py uses these three switches.
    # Default precision produced a 4.92e-8 A/cm^2 zero-bias contact imbalance.
    # Keep the independently declared tolerance; improve numerical precision.
    for name in ("extended_solver", "extended_model", "extended_equation"):
        ds.set_parameter(name=name, value=True)
    device, region = "pn_fixture", "bulk"
    build_mesh(ds, device=device, length=1e-5, spacing=1e-7, midpoint=5e-6)
    helpers.SetSiliconParameters(device, region, 300)
    for name in ("taun", "taup"):
        ds.set_parameter(device=device, region=region, name=name, value=1e-8)
    for name, expression in (
        ("Acceptors", "1e18*step(0.5e-5-x)"),
        ("Donors", "1e18*step(x-0.5e-5)"),
        ("NetDoping", "Donors-Acceptors"),
    ):
        ds.node_model(device=device, region=region, name=name, equation=expression)
    model.CreateSolution(device, region, "Potential")
    helpers.CreateSiliconPotentialOnly(device, region)
    for contact in ds.get_contact_list(device=device):
        ds.set_parameter(device=device, name=helpers.GetContactBiasName(contact), value=0)
        helpers.CreateSiliconPotentialOnlyContact(device, region, contact)
    equilibrium = solve_checked(ds, absolute_error=1, relative_error=1e-10, maximum_iterations=30)
    for carrier, initial in (("Electrons", "IntrinsicElectrons"), ("Holes", "IntrinsicHoles")):
        model.CreateSolution(device, region, carrier)
        ds.set_node_values(device=device, region=region, name=carrier, init_from=initial)
    helpers.CreateSiliconDriftDiffusion(device, region)
    for contact in ds.get_contact_list(device=device):
        helpers.CreateSiliconDriftDiffusionAtContact(device, region, contact)
    initial_dd = solve_checked(ds, absolute_error=1e10, relative_error=1e-10, maximum_iterations=30)
    records = []

    def collect(current_device):
        bias = ds.get_parameter(device=current_device, name=helpers.GetContactBiasName("top"))
        currents = {}
        for contact in ("top", "bot"):
            currents[contact] = sum(
                ds.get_contact_current(device=current_device, contact=contact, equation=equation)
                for equation in ("ElectronContinuityEquation", "HoleContinuityEquation")
            )
        imbalance = abs(currents["top"] + currents["bot"])
        limit = 1e-10 + 1e-6 * max(abs(value) for value in currents.values())
        check(imbalance <= limit, f"Contact current conservation failed at {bias}: {currents}")
        for name in ("Potential", "Electrons", "Holes"):
            values = ds.get_node_model_values(device=current_device, region=region, name=name)
            check(all(math.isfinite(value) for value in values), f"Nonfinite {name}")
            if name != "Potential":
                check(min(values) >= 0, f"Negative {name}")
        fields = ds.get_edge_model_values(device=current_device, region=region, name="ElectricField")
        check(all(math.isfinite(value) for value in fields), "Nonfinite ElectricField")
        records.append({"bias_V": bias, "current_density_A_cm2": currents,
                        "conservation_error": imbalance, "conservation_limit": limit,
                        "electric_field_range_V_cm": [min(fields), max(fields)]})

    collect(device)
    ramp.rampbias(device, "top", 0.3, 0.1, 1e-6, 30, 1e-10, 1e10, collect)
    for expected in (0, 0.1, 0.2, 0.3):
        check(any(math.isclose(row["bias_V"], expected, abs_tol=1e-12) for row in records),
              f"Missing requested bias {expected}")
    forward = [abs(row["current_density_A_cm2"]["top"]) for row in records if row["bias_V"] > 0]
    check(all(right > left for left, right in zip(forward, forward[1:])), "Forward current is not increasing")
    saved = {name: list(ds.get_node_model_values(device=device, region=region, name=name))
             for name in ("Potential", "Electrons", "Holes")}
    state_file = output / "pn_state.devsim"
    ds.write_devices(file=str(state_file), type="devsim")
    ds.reset_devsim()
    ds.load_devices(file=str(state_file))
    check(device in ds.get_device_list(), "Native state did not restore device")
    restore_error = {}
    for name, reference in saved.items():
        loaded = ds.get_node_model_values(device=device, region=region, name=name)
        check(len(loaded) == len(reference), "Native reload length mismatch")
        restore_error[name] = max(abs(a - b) for a, b in zip(reference, loaded))
        for a, b in zip(reference, loaded):
            check(math.isclose(a, b, rel_tol=0 if name == "Potential" else 1e-10, abs_tol=1e-12),
                  f"Native reload changed {name}")
    return {"id": "tcad_pn_bias_sweep", "status": "passed", "nodes": len(saved["Potential"]),
            "precision": "extended_solver/model/equation=True",
            "records": records, "equilibrium_solve": equilibrium, "initial_dd_solve": initial_dd,
            "native_restore_max_abs_error": restore_error,
            "assertions_passed": ["convergence", "finite_fields", "nonnegative_carriers",
                                  "current_continuity", "forward_current_increase", "native_reload"],
            "native_file": str(state_file.relative_to(ROOT)),
            "scope": "1D PN at 300 K, default constant mobilities/SRH, DC 0 to 0.3 V; no MOS/3D run"}


def capacitor_fixture(ds, model):
    ds.reset_devsim()
    device, region = "capacitor_fixture", "bulk"
    build_mesh(ds, device=device, length=1, spacing=0.1)
    model.CreateSolution(device, region, "Potential")
    ds.set_parameter(device=device, region=region, name="Permittivity", value=39 * 8.85e-14)
    expressions = {"ElectricField": "(Potential@n0-Potential@n1)*EdgeInverseLength",
                   "DField": "Permittivity*ElectricField"}
    for name, expression in expressions.items():
        ds.edge_model(device=device, region=region, name=name, equation=expression)
        model.CreateEdgeModelDerivatives(device, region, name, expression, "Potential")
    ds.equation(device=device, region=region, name="PotentialEquation", variable_name="Potential",
                edge_model="DField", variable_update="default")
    for contact, bias in (("top", 1.0), ("bot", 0.0)):
        ds.set_parameter(device=device, region=region, name=f"{contact}_bias", value=bias)
        ds.contact_node_model(device=device, contact=contact, name=f"{contact}_bc",
                              equation=f"Potential-{contact}_bias")
        ds.contact_node_model(device=device, contact=contact, name=f"{contact}_bc:Potential", equation="1")
        ds.contact_equation(device=device, contact=contact, name="PotentialEquation",
                            node_model=f"{contact}_bc", edge_charge_model="DField")
    solve_checked(ds, absolute_error=1e-12, relative_error=1e-10, maximum_iterations=30)
    expected_charge = 3.4515e-13  # Independent fixed epsilon * deltaV / L; C/cm^2.
    charge = lambda c: ds.get_contact_charge(device=device, contact=c, equation="PotentialEquation")
    wrong_charge = abs(charge("top"))
    wrong_target_rejected = not math.isclose(wrong_charge, expected_charge, rel_tol=1e-9, abs_tol=1e-24)
    check(wrong_target_rejected, "Corrupt permittivity unexpectedly passed the fixed charge target")
    observed_before = ds.get_parameter(device=device, region=region, name="Permittivity")
    actual_equation = ds.get_equation_command(device=device, region=region, name="PotentialEquation")
    actual_contacts = {c: ds.get_contact_equation_command(device=device, contact=c, name="PotentialEquation")
                       for c in ("top", "bot")}
    ds.set_parameter(device=device, region=region, name="Permittivity", value=3.9 * 8.85e-14)
    solved = solve_checked(ds, absolute_error=1e-12, relative_error=1e-10, maximum_iterations=30)
    x = ds.get_node_model_values(device=device, region=region, name="x")
    potential = ds.get_node_model_values(device=device, region=region, name="Potential")
    electric_field = ds.get_edge_model_values(device=device, region=region, name="ElectricField")
    potential_error = max(abs(value - (1 - position)) for position, value in zip(x, potential))
    field_error = max(abs(value - 1) for value in electric_field)
    top, bot = charge("top"), charge("bot")
    check(potential_error <= 1e-10, "Potential differs from analytic linear solution")
    check(field_error <= 1e-10, "ElectricField differs from analytic uniform solution")
    check(abs(top + bot) <= 1e-24, "Contact charges not balanced")
    check(math.isclose(abs(top), expected_charge, rel_tol=1e-9, abs_tol=1e-24), "Repaired charge target failed")
    return {"id": "tcad_dielectric_parameter_repair", "status": "passed",
            "corrupt_charge_C_cm2": wrong_charge, "expected_charge_C_cm2": expected_charge,
            "wrong_target_rejected": wrong_target_rejected, "permittivity_before_F_cm": observed_before,
            "permittivity_after_F_cm": ds.get_parameter(device=device, region=region, name="Permittivity"),
            "potential_max_error_V": potential_error, "field_max_error_V_cm": field_error,
            "contact_charge_C_cm2": {"top": top, "bot": bot}, "charge_balance_error": abs(top + bot),
            "equation": actual_equation, "contact_equations": actual_contacts, "repair_solve": solved,
            "assertions_passed": ["wrong_target_rejected", "analytic_potential", "analytic_field",
                                  "charge_balance", "repaired_charge_target"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "seed_gen/scenario_pilot/runtime/tcad")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": "tcad-pilot-runtime-1", "scenario_id": "02.01.01",
              "checked_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "python": sys.executable, "python_version": platform.python_version(),
              "platform": platform.platform(), "tasks": [],
              "source_commit": "43b41ca845184c47e22b72d144db7e7db8509377",
              "install_command": r"C:\Apps\anaconda3\Scripts\uv.exe pip install --python .venv-scenario-tcad\Scripts\python.exe devsim==2.11.0",
              "run_command": r".venv-scenario-tcad\Scripts\python.exe -X utf8 seed_gen\scenario_pilot\verify_tcad.py",
              "verification_boundary": "Two deterministic 1D local fixtures only; not all 83 symbols or all scenario physics validated.",
              "prerequisite_note": "DEVSIM loads native math libraries found in this Windows environment; the installed wheel alone is not a verified portable runtime."}
    for relative in ("seed_pypi_raw/devsim/examples/diode/diode_common.py",
                     "seed_pypi_raw/devsim/examples/capacitance/cap1d.py"):
        report.setdefault("fixture_source_sha256", {})[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    try:
        import devsim as ds
        from devsim.python_packages import model_create, ramp, simple_physics

        report["package_version"] = importlib.metadata.version("devsim")
        check(report["package_version"] == "2.11.0", "Expected fixed devsim==2.11.0")
        report["native_backend_info"] = ds.get_parameter(name="info")
        report["tasks"].append(pn_fixture(ds, simple_physics, model_create, ramp, output))
        report["tasks"].append(capacitor_fixture(ds, model_create))
        ds.reset_devsim()
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(report_path),
                      "tasks": [{"id": task["id"], "status": task["status"]} for task in report["tasks"]]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
