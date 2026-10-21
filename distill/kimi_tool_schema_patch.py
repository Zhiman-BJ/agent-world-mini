"""Preserve public tool schemas when using Kimi Code 0.43.0 with Qwen."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


ORIGINAL_NORMALIZER = (
    "function normalizeKimiToolSchema(schema) {\n"
    "\treturn ensureKimiPropertyTypes(derefJsonSchema(schema));\n"
    "}"
)
PRESERVING_NORMALIZER = (
    "function normalizeKimiToolSchema(schema) {\n"
    "\treturn cloneJsonValue(schema);\n"
    "}"
)


def patch_bundle(source: str) -> str:
    if PRESERVING_NORMALIZER in source:
        raise ValueError("The bundle already preserves Qwen tool schemas")
    if source.count(ORIGINAL_NORMALIZER) != 1:
        raise ValueError("Kimi bundle does not match the audited 0.43.0 schema normalizer")
    return source.replace(ORIGINAL_NORMALIZER, PRESERVING_NORMALIZER, 1)


def prepare_patch(source: Path, destination: Path) -> Path:
    source, destination = source.resolve(), destination.absolute()
    package = json.loads((source / "package.json").read_text())
    if package.get("name") != "@moonshot-ai/kimi-code" or package.get("version") != "0.43.0":
        raise ValueError("This compatibility patch is restricted to Kimi Code 0.43.0")
    dependencies = source / "node_modules"
    if not dependencies.is_dir():
        if source.parent.name != "@moonshot-ai" or source.parent.parent.name != "node_modules":
            raise ValueError("Cannot locate the source distribution's Node dependencies")
        dependencies = source.parent.parent
    original = (source / "dist/main.mjs").read_bytes()
    patched = patch_bundle(original.decode()).encode()
    destination.mkdir(parents=True, exist_ok=False)
    for item in source.iterdir():
        if item.name in {"node_modules", "patch_receipt.json", "tool_schema_patch_receipt.json"}:
            continue
        if item.name == "dist":
            shutil.copytree(item, destination / item.name)
        else:
            (destination / item.name).symlink_to(item, target_is_directory=item.is_dir())
    (destination / "node_modules").symlink_to(dependencies.resolve(), target_is_directory=True)
    executable = destination / "dist/main.mjs"
    executable.write_bytes(patched)
    receipt = {
        "base_version": "0.43.0",
        "purpose": "Qwen Chat Completions only; not a Moonshot API compatibility claim",
        "source": str(source),
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "patched_sha256": hashlib.sha256(patched).hexdigest(),
        "changes": ["Copy public tool JSON Schemas without type inference or reference rewriting"],
        "original_distribution_unchanged": True,
        "source_compaction_receipt": (
            json.loads((source / "patch_receipt.json").read_text())
            if (source / "patch_receipt.json").is_file() else None
        ),
    }
    (destination / "tool_schema_patch_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return executable


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    print(prepare_patch(arguments.source, arguments.destination))
