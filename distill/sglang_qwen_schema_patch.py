"""Keep arbitrary JSON arguments usable with SGLang's Qwen XML tool parser."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from distill.kimi_tool_schema_patch import (
    PRESERVING_NORMALIZER,
    patch_bundle as preserve_bundle,
    prepare_patch as prepare_preserving_patch,
)


SGLANG_NORMALIZER = """function normalizeKimiToolSchema(schema) {
\tconst normalized = cloneJsonValue(schema);
\tconst annotations = new Set(["title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly", "$comment"]);
\tif (isRecord$18(normalized) && isRecord$18(normalized.properties)) {
\t\tfor (const [name, property] of Object.entries(normalized.properties)) {
\t\t\tif (property === true) normalized.properties[name] = { type: ["object", "array", "string", "number", "boolean", "null"] };
\t\t\telse if (isRecord$18(property) && Object.keys(property).every((key) => annotations.has(key))) {
\t\t\t\tproperty.type = ["object", "array", "string", "number", "boolean", "null"];
\t\t\t}
\t\t}
\t}
\treturn normalized;
}"""


def patch_bundle(source: str) -> str:
    if SGLANG_NORMALIZER in source:
        raise ValueError("The bundle already declares arbitrary JSON arguments for SGLang")
    preserved = preserve_bundle(source)
    return preserved.replace(PRESERVING_NORMALIZER, SGLANG_NORMALIZER, 1)


def prepare_patch(source: Path, destination: Path) -> Path:
    patched = patch_bundle((source / "dist/main.mjs").read_text()).encode()
    executable = prepare_preserving_patch(source, destination)
    executable.write_bytes(patched)
    receipt_path = executable.parents[1] / "tool_schema_patch_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["preserving_only_sha256"] = receipt["patched_sha256"]
    receipt["patched_sha256"] = hashlib.sha256(patched).hexdigest()
    receipt["purpose"] = "SGLang Qwen3 Coder XML parser compatibility; not a Moonshot API claim"
    receipt["changes"].append("Declare the universal JSON type union for unconstrained top-level arguments")
    receipt["universal_type_order"] = ["object", "array", "string", "number", "boolean", "null"]
    receipt["allowed_json_values_unchanged"] = True
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return executable


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    print(prepare_patch(arguments.source, arguments.destination))
