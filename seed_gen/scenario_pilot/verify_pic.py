"""Run two fixed PIC fixtures with positive and negative acceptance checks."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
from pathlib import Path

import jax
import jax.numpy as jnp
import numpy as np
import sax

jax.config.update("jax_enable_x64", True)

MZI_YAML = """instances:
  left: {component: coupler, settings: {coupling: 0.5}}
  right: {component: coupler, settings: {coupling: 0.5}}
  upper: {component: phase, settings: {length: 10.0, loss: 0.0, voltage: 0.0}}
  lower: {component: phase, settings: {length: 10.0, loss: 0.0, voltage: 0.0}}
connections:
  left,out0: lower,in0
  lower,out0: right,in0
  left,out1: upper,in0
  upper,out0: right,in1
ports:
  in0: left,in0
  in1: left,in1
  out0: right,out0
  out1: right,out1
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("seed_gen/scenario_pilot/runtime/pic"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sax.set_port_naming_strategy("inout")
    netlist = sax.load_netlist(MZI_YAML)
    models = {"coupler": sax.models.coupler_ideal, "phase": sax.models.phase_shifter}
    circuit, _ = sax.circuit(netlist, models=models, backend="filipsson_gunnar")
    settings = sax.get_settings(circuit)
    wl = jnp.linspace(1.50, 1.60, 101)
    sweep_settings = sax.update_settings(settings, wl=wl)
    updated = sax.update_settings(sweep_settings, "upper", voltage=1.0)
    s0 = sax.sdict(circuit(wl=wl, upper={"voltage": 0.0}))
    s1 = sax.sdict(circuit(wl=wl, **updated))
    initial_power = np.abs(s0["in0", "out0"]) ** 2
    final_power = np.abs(s1["in0", "out0"]) ** 2
    other_power = np.abs(s1["in0", "out1"]) ** 2
    np.testing.assert_allclose(initial_power, 0.0, atol=1e-10)
    np.testing.assert_allclose(final_power, 1.0, atol=1e-10)
    np.testing.assert_allclose(final_power + other_power, 1.0, atol=1e-10)
    np.testing.assert_allclose(s1["in0", "out0"], s1["out0", "in0"], atol=1e-10)
    assert float(np.max(initial_power)) < 0.99, "Unchanged voltage must fail target transmission >= 0.99"
    dense, port_map = sax.sdense(s1)
    assert np.asarray(dense).shape == (101, 4, 4)
    assert set(port_map) == {"in0", "in1", "out0", "out1"}
    assert float(settings["upper"]["voltage"]) == 0.0

    # This loop requires a feedback-capable backend, not the forward-only one.
    ring_netlist = {
        "instances": {
            "dc": {"component": "coupler", "settings": {"coupling": 0.5}},
            "ring": {"component": "straight", "settings": {
                "length": 10.0, "loss_dB_cm": 1000.0,
                "neff": 2.34, "ng": 3.4, "wl0": 1.55,
            }},
        },
        "connections": {"dc,out1": "ring,in0", "ring,out0": "dc,in1"},
        "ports": {"in0": "dc,in0", "out0": "dc,out0"},
    }
    ring, _ = sax.circuit(ring_netlist, models={
        "coupler": sax.models.coupler_ideal, "straight": sax.models.straight,
    }, backend="filipsson_gunnar")
    wrong_ring_settings = {"ring": {"loss_dB_cm": 0.1}}
    repaired_ring_settings = {"ring": {"loss_dB_cm": 1000.0}}
    wrong_ring_s = sax.sdict(ring(wl=wl, **wrong_ring_settings))
    wrong_response = np.asarray(wrong_ring_s["in0", "out0"])
    ring_s = sax.sdict(ring(wl=wl, **repaired_ring_settings))
    response = np.asarray(ring_s["in0", "out0"])
    wavelengths = np.asarray(wl)
    neff_wl = 2.34 + (1.55 - wavelengths) * (3.4 - 2.34) / 1.55
    phase = 2 * np.pi * neff_wl * 10.0 / wavelengths
    # API loss is dB/cm: 1000 dB/cm = 0.1 dB/um.
    roundtrip = 10 ** (-1000.0 * 1e-4 * 10.0 / 20) * np.exp(1j * phase)
    tau = np.sqrt(0.5)
    analytical = (tau - roundtrip) / (1 - tau * roundtrip)
    np.testing.assert_allclose(response, analytical, rtol=1e-9, atol=1e-10)
    power = np.abs(response) ** 2
    assert np.isfinite(power).all() and np.all(power <= 1.0 + 1e-10) and np.all(power >= 0.0)
    # Run the actual erroneous input through SAX, then use the same independent
    # oracle and tolerances as for the repaired model. Do not mutate the oracle.
    negative_rejected = False
    negative_error = ""
    try:
        np.testing.assert_allclose(wrong_response, analytical, rtol=1e-9, atol=1e-10)
    except AssertionError as exc:
        negative_rejected = True
        negative_error = str(exc)
    assert negative_rejected, "Missing dB/um-to-dB/cm conversion must fail the fixed oracle"

    np.savez(args.output / "spectra.npz", wavelength_um=wavelengths,
             mzi_initial_power=initial_power, mzi_final_power=final_power,
             mzi_other_power=other_power, ring_s=response,
             ring_wrong_s=wrong_response, ring_analytical=analytical)
    (args.output / "mzi.pic.yml").write_text(MZI_YAML, encoding="utf-8")
    (args.output / "ring.netlist.json").write_text(json.dumps(ring_netlist, indent=2) + "\n", encoding="utf-8")
    (args.output / "ring.run_settings.json").write_text(json.dumps({
        "wrong": wrong_ring_settings, "repaired": repaired_ring_settings,
        "physical_loss_dB_per_um": 0.1,
    }, indent=2) + "\n", encoding="utf-8")
    report = {
        "scenario_id": "02.07.01", "status": "passed", "python": platform.python_version(),
        "versions": {p: importlib.metadata.version(p) for p in ("sax", "jax", "jaxlib", "numpy", "klujax")},
        "backend": "filipsson_gunnar", "device": str(jax.devices()[0]), "x64": True,
        "fixtures": [
            {"id": "pic_mzi_switch", "status": "passed", "sample_count": 101,
             "initial_max_transmission": float(np.max(initial_power)),
             "final_min_transmission": float(np.min(final_power)),
             "max_power_balance_error": float(np.max(np.abs(final_power + other_power - 1))),
             "negative_control": "unchanged 0 V fails transmission >= 0.99",
             "negative_control_rejected": True},
            {"id": "pic_allpass_units", "status": "passed", "sample_count": 101,
             "max_complex_response_error": float(np.max(np.abs(response - analytical))),
             "negative_control": "Actual SAX run with loss_dB_cm=0.1 instead of 1000.0 fails the same fixed analytical oracle",
             "wrong_model_max_complex_response_error": float(np.max(np.abs(wrong_response - analytical))),
             "negative_control_rejected": negative_rejected,
             "negative_control_assertion_error": negative_error,
             "repaired_model_passed": True},
        ],
        "scope": "Two fixed model chains only; not all selected APIs, gradients, imported layouts, or nonzero phase-shifter loss.",
    }
    (args.output / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
