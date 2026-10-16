import pytest

from task_gen.program.tool_test_coverage import asserted_leaves, audit_manifest, schema_leaves, tool_coverage
from task_gen.program.utils.io import write_json


def test_arrays_and_union_schemas_keep_calculation_fields_visible():
    schema = {"oneOf": [{"properties": {"data": {"properties": {
        "confidence": {"type": "number"}, "points": {"type": "array", "items": {
            "properties": {"mcf": {"type": "number"}, "lower": {"type": "number"}}}},
    }}}}, {"properties": {"error": {"type": "string"}}}]}
    tool = {"name": "estimate", "outputSchema": schema,
            "inputSchema": {"properties": {"confidence": {"type": "number"}}}}
    result = tool_coverage(tool, {"status": "passed", "tests": [
        {"expect_success": True, "expected_data": {"confidence": 0.95}},
        {"expect_success": False, "expected_data": {"points": [{"mcf": 1, "lower": 0}]}}]})
    assert result["advertised_data_leaf_paths_without_assertions"] == ["points.[].lower", "points.[].mcf"]
    assert result["assertions_on_possible_input_echoes"] == ["confidence"]
    assert result["semantic_certification"] == "not_certified"
    assert ("data", "points", "[]", "mcf") in schema_leaves(schema)


def test_empty_containers_and_absent_expectations_do_not_certify_values():
    assert asserted_leaves({}) == set()
    assert asserted_leaves({"rows": []}) == {("rows",)}
    result = tool_coverage({"name": "tool"}, {"status": "passed", "tests": [{"expect_success": True}]})
    assert result["asserted_data_leaf_paths"] == []
    assert result["successful_tests_with_expected_data"] == 0
    assert result["independent_expected_value_provenance_verified"] is False


def test_audit_reuses_environment_version_without_dropping_cases(tmp_path):
    tools_path = tmp_path / "tools/tools_repaired.json"
    binding = tmp_path / "binding.json"
    write_json(tools_path, {"tools": [{"name": "tool"}]})
    write_json(binding, {"version": "frozen"})
    entries = []
    for index in range(20):
        variant = tmp_path / f"variant_{index}"
        write_json(variant / "intermediate/step_5_bundle.json", {
            "environment": {"environment_id": "same"},
            "runtime": {"tools_path": str(tools_path), "binding_path": str(binding)}})
        entries.append({"source_case": f"case_{index}", "variant_run": str(variant)})
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"entries": entries})
    report = audit_manifest(manifest)
    assert report["source_count"] == 20
    assert report["environment_version_count"] == 1
    assert len(report["environments"][0]["source_cases"]) == 20
    assert report["tools_without_successful_value_assertions"] == 1
    assert report["semantic_certified_count"] == 0


def test_audit_refuses_partial_coverage(tmp_path):
    path = tmp_path / "manifest.json"
    write_json(path, {"entries": []})
    with pytest.raises(ValueError, match="complete frozen twenty"):
        audit_manifest(path)
