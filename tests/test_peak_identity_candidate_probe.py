import ast
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "agent_train/evaluation/probe_peak_identity_template.py"
SPEC = importlib.util.spec_from_file_location("peak_identity_candidate_probe", MODULE_PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_candidate_changes_only_the_no_template_call_and_adds_identity_setup():
    source = "def run():\n    import numpy as np\n    frame = source_frame\n    q = find_Bragg_disks(frame, None, sigma_dp=2)\n    return q\n"
    candidate = probe.candidate_source(source)
    tree = ast.parse(candidate)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "find_Bragg_disks"]
    assert len(calls) == 1
    assert calls[0].args[1].id == "identity_template"
    assert ast.literal_eval(calls[0].keywords[0].value) == 2
    assert "identity_template[0, 0] = 1.0" in candidate
    assert "np.zeros_like(frame)" in candidate
    assert source.endswith("    return q\n")


@pytest.mark.parametrize("source", ["value = other(frame, None)", "value = find_Bragg_disks(frame, template)",
                                    "first = find_Bragg_disks(frame, None)\nsecond = find_Bragg_disks(frame, None)"])
def test_candidate_does_not_silently_patch_an_unrecognized_tool(source):
    with pytest.raises(ValueError, match="exactly one"):
        probe.candidate_source(source)


def test_known_fixture_checks_fail_for_missing_or_extra_peaks():
    spec = probe.fixture_specs()[0]
    result = {"success": True, "data": {"peak_count": 1, "scan_count": 1,
                                        "peaks": [{"row": 13.0, "col": 19.0, "intensity": 1000.0}]}}
    assert probe.check_result(result, spec)["coordinates_exactly_match"] is True
    result["data"]["peaks"].append({"row": 4.0, "col": 4.0, "intensity": 1.0})
    checked = probe.check_result(result, spec)
    assert checked["coordinates_exactly_match"] is False
    assert checked["peak_count_consistent"] is False


def test_zero_and_nonsquare_fixtures_are_not_assumed_to_have_a_default_peak():
    fixtures = {spec["name"]: spec for spec in probe.fixture_specs()}
    assert probe.expected_coordinates(fixtures["zero"]) == set()
    assert fixtures["nonsquare"]["shape"] == [32, 48]
    assert probe.expected_coordinates(fixtures["nonsquare"]) == {(13.0, 35.0)}
    result = {"success": True, "data": {"peak_count": 0, "scan_count": 1, "peaks": []}}
    assert probe.fixture_passed(probe.check_result(result, fixtures["zero"])) is True
    assert probe.fixture_passed({}) is False
