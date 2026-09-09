"""Select a curated, capability-balanced subset from a raw Python API seed."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


_EVIDENCE_WEIGHTS = {
    "public_api": 25,
    "official_docs": 20,
    "core_capability": 15,
    "deterministic_local": 20,
    "composable_io": 20,
}
_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_SUPPORT_MODULE_PARTS = {
    "builders",
    "cli",
    "command_line",
    "schemas",
    "settings",
    "testing",
    "tests",
}


class SelectionError(ValueError):
    """Raised when a selection profile does not match its raw seed."""


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SelectionError(f"JSON file does not exist: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelectionError(f"Cannot read JSON file {path}: {exc}") from exc


def _seed_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise SelectionError("Expected the raw seed to contain exactly one object")
    seed = payload[0]
    if not isinstance(seed.get("init_ref_tools"), list):
        raise SelectionError("Raw seed init_ref_tools must be an array")
    return seed


def _symbol_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return str(record.get("module") or ""), str(record.get("name") or ""), str(record.get("type") or "")


def _validate_profile_identity(seed: dict[str, Any], profile: dict[str, Any], source_sha256: str) -> None:
    basic_info = seed.get("environment", {}).get("basic_info", {})
    expected = {
        "package": basic_info.get("name"),
        "version": basic_info.get("version"),
        "source_sha256": source_sha256,
    }
    for field, actual in expected.items():
        declared = profile.get(field)
        if declared != actual:
            raise SelectionError(f"Profile {field} mismatch: expected {actual!r}, got {declared!r}")
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or not _CAPABILITY_ID_RE.fullmatch(profile_id):
        raise SelectionError("profile_id must be a non-empty snake_case identifier")


def _tool_index(tools: list[Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for position, value in enumerate(tools):
        if not isinstance(value, dict):
            raise SelectionError(f"Raw tool at index {position} must be an object")
        key = _symbol_key(value)
        if not all(key):
            raise SelectionError(f"Raw tool at index {position} has an incomplete symbol identity")
        if key in index:
            raise SelectionError(f"Raw seed contains duplicate symbol {'.'.join(key[:2])} ({key[2]})")
        index[key] = value
    return index


def _select_methods(tool: dict[str, Any], requested: Any) -> list[dict[str, Any]]:
    if not isinstance(requested, list) or not all(isinstance(name, str) and name for name in requested):
        raise SelectionError(f"Class method selection must be an array of names: {tool['module']}.{tool['name']}")
    if len(requested) != len(set(requested)):
        raise SelectionError(f"Class method selection contains duplicates: {tool['module']}.{tool['name']}")

    available: dict[str, dict[str, Any]] = {}
    for method in tool.get("function", []):
        if isinstance(method, dict) and isinstance(method.get("name"), str):
            available[method["name"]] = method
    missing = [name for name in requested if name not in available]
    if missing:
        raise SelectionError(f"Missing methods on {tool['module']}.{tool['name']}: {missing}")

    selected = [copy.deepcopy(available[name]) for name in requested]
    undocumented = [method["name"] for method in selected if not str(method.get("description") or "").strip()]
    if undocumented:
        raise SelectionError(f"Selected methods have empty descriptions on {tool['module']}.{tool['name']}: {undocumented}")
    return selected


def _exclusion_reason(tool: dict[str, Any]) -> str:
    if not str(tool.get("description") or "").strip():
        return "missing_description"
    module_parts = set(str(tool.get("module") or "").split("."))
    if module_parts & _SUPPORT_MODULE_PARTS:
        return "support_schema_or_test_surface"
    name = str(tool.get("name") or "")
    if name.endswith(("Error", "Warning", "Mixin")):
        return "support_type"
    return "outside_curated_capability_boundary"


def select_seed(
    raw_payload: Any,
    profile: dict[str, Any],
    *,
    input_label: str,
    profile_label: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a selected seed payload and a complete selection decision report."""
    seed = _seed_from_payload(raw_payload)
    source_sha256 = _canonical_sha256(raw_payload)
    profile_sha256 = _canonical_sha256(profile)
    _validate_profile_identity(seed, profile, source_sha256)

    tools = seed["init_ref_tools"]
    index = _tool_index(tools)
    capabilities = profile.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise SelectionError("Profile capabilities must be a non-empty array")

    selected_tools: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str, str]] = set()
    selected_report: list[dict[str, Any]] = []
    capability_report: list[dict[str, Any]] = []
    capability_ids: set[str] = set()

    for capability in capabilities:
        if not isinstance(capability, dict):
            raise SelectionError("Each capability must be an object")
        capability_id = capability.get("id")
        if not isinstance(capability_id, str) or not _CAPABILITY_ID_RE.fullmatch(capability_id):
            raise SelectionError(f"Invalid capability id: {capability_id!r}")
        if capability_id in capability_ids:
            raise SelectionError(f"Duplicate capability id: {capability_id}")
        capability_ids.add(capability_id)

        evidence = capability.get("evidence", [])
        if not isinstance(evidence, list) or not all(item in _EVIDENCE_WEIGHTS for item in evidence):
            raise SelectionError(f"Capability {capability_id} has unsupported evidence values")
        score = sum(_EVIDENCE_WEIGHTS[item] for item in set(evidence))
        reason = capability.get("selection_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise SelectionError(f"Capability {capability_id} needs selection_reason")
        specs = capability.get("symbols")
        if not isinstance(specs, list) or not specs:
            raise SelectionError(f"Capability {capability_id} needs at least one symbol")

        capability_symbols: list[str] = []
        capability_method_count = 0
        for spec in specs:
            if not isinstance(spec, dict):
                raise SelectionError(f"Capability {capability_id} symbol must be an object")
            key = _symbol_key(spec)
            if not all(key):
                raise SelectionError(f"Capability {capability_id} has an incomplete symbol identity")
            if key in selected_keys:
                raise SelectionError(f"Profile selects symbol more than once: {'.'.join(key[:2])}")
            source_tool = index.get(key)
            if source_tool is None:
                raise SelectionError(f"Profile symbol is absent from raw seed: {'.'.join(key[:2])} ({key[2]})")
            if not str(source_tool.get("description") or "").strip():
                raise SelectionError(f"Selected symbol has an empty description: {'.'.join(key[:2])}")

            selected_tool = copy.deepcopy(source_tool)
            if key[2] == "class":
                selected_tool["function"] = _select_methods(source_tool, spec.get("methods"))
                method_count = len(selected_tool["function"])
            elif "methods" in spec:
                raise SelectionError(f"Function symbol cannot declare methods: {'.'.join(key[:2])}")
            else:
                method_count = 0

            selected_tools.append(selected_tool)
            selected_keys.add(key)
            qualified_name = f"{key[0]}.{key[1]}"
            capability_symbols.append(qualified_name)
            capability_method_count += method_count
            selected_report.append({
                "module": key[0],
                "name": key[1],
                "type": key[2],
                "capability": capability_id,
                "score": score,
                "evidence": sorted(set(evidence)),
                "reason": reason,
                "selected_method_count": method_count,
            })

        capability_report.append({
            "id": capability_id,
            "description": capability.get("description", ""),
            "verification_tier": capability.get("verification_tier", "unspecified"),
            "selected_symbol_count": len(capability_symbols),
            "selected_method_count": capability_method_count,
            "symbols": capability_symbols,
        })

    excluded_report = []
    for tool in tools:
        key = _symbol_key(tool)
        if key in selected_keys:
            continue
        excluded_report.append({
            "module": key[0],
            "name": key[1],
            "type": key[2],
            "available_method_count": len(tool.get("function", [])) if key[2] == "class" else 0,
            "reason": _exclusion_reason(tool),
        })

    class_count = sum(tool.get("type") == "class" for tool in selected_tools)
    function_count = sum(tool.get("type") == "function" for tool in selected_tools)
    method_count = sum(len(tool.get("function", [])) for tool in selected_tools if tool.get("type") == "class")
    exclusion_counts = Counter(item["reason"] for item in excluded_report)
    summary = {
        "raw_tool_count": len(tools),
        "selected_tool_count": len(selected_tools),
        "selected_class_count": class_count,
        "selected_function_count": function_count,
        "selected_method_count": method_count,
        "excluded_tool_count": len(excluded_report),
        "capability_count": len(capability_report),
    }

    selected_seed = copy.deepcopy(seed)
    selected_seed["init_ref_tools"] = selected_tools
    selected_seed.setdefault("environment", {})["nums"] = {
        "class": class_count,
        "function": function_count,
        "class_func": method_count,
        "all_func": function_count + method_count,
    }
    others = selected_seed.setdefault("others", {})
    others.pop("tool_count", None)
    others["python_api_selection"] = {
        "profile_id": profile["profile_id"],
        "source_artifact": input_label,
        "profile_artifact": profile_label,
        "source_sha256": source_sha256,
        "profile_sha256": profile_sha256,
        "boundary": profile.get("boundary", ""),
        **summary,
    }

    report = {
        "profile_id": profile["profile_id"],
        "package": profile["package"],
        "version": profile["version"],
        "source_artifact": input_label,
        "profile_artifact": profile_label,
        "source_sha256": source_sha256,
        "profile_sha256": profile_sha256,
        "boundary": profile.get("boundary", ""),
        "summary": summary,
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
        "capabilities": capability_report,
        "selected_symbols": selected_report,
        "excluded_symbols": excluded_report,
    }
    return [selected_seed], report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Raw one-package Python seed JSON")
    parser.add_argument("--profile", type=Path, required=True, help="Curated package selection profile")
    parser.add_argument("--output", type=Path, required=True, help="Selected seed JSON")
    parser.add_argument("--report", type=Path, required=True, help="Selection decision report JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_payload = _read_json(args.input)
    profile = _read_json(args.profile)
    if not isinstance(profile, dict):
        raise SelectionError("Selection profile must be an object")
    selected, report = select_seed(
        raw_payload,
        profile,
        input_label=args.input.as_posix(),
        profile_label=args.profile.as_posix(),
    )
    for path, payload in ((args.output, selected), (args.report, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Selected {report['summary']['selected_tool_count']} tools and "
        f"{report['summary']['selected_method_count']} class methods from "
        f"{report['summary']['raw_tool_count']} raw tools"
    )


if __name__ == "__main__":
    main()
