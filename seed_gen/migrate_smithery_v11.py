"""Migrate the Smithery environment seed export from v1.0 to v1.1."""

import copy
import json
from pathlib import Path


DATA = Path(__file__).resolve().parent / "data"
TARGET = DATA / "smithery_1000_v1_0902.json"


def schema_fields(schema):
    """Use the v1.1 direct field-map representation for a source schema."""
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return copy.deepcopy(properties)
    return {}


def migrate(seed):
    seed = copy.deepcopy(seed)
    seed["schema_version"] = "1.1"

    old_basic_info = seed["environment"]["basic_info"]
    source_url = old_basic_info.get("url")
    basic_info = {
        "source": old_basic_info["source"],
        "url": source_url if isinstance(source_url, list) else [source_url],
        "name": old_basic_info["name"],
        "version": "2026-09-02",
        "index": old_basic_info["index"],
    }
    seed["environment"]["basic_info"] = basic_info

    migrated_tools = []
    for source_tool in seed.get("init_ref_tools", []):
        tool = {
            "name": source_tool["name"],
            "type": "function",
            "module": None,
            "description": source_tool["description"],
            "input": schema_fields(source_tool.get("inputSchema")),
            "output": schema_fields(source_tool.get("outputSchema")),
        }
        # Keep any source fields that are not part of the old schema contract.
        for key, value in source_tool.items():
            if key not in {"name", "description", "inputSchema", "outputSchema"}:
                tool[key] = copy.deepcopy(value)
        migrated_tools.append(tool)
    seed["init_ref_tools"] = migrated_tools

    others = seed.setdefault("others", {})
    others["tool_count"] = len(migrated_tools)
    return seed


def main():
    seeds = json.loads(TARGET.read_text(encoding="utf-8"))
    migrated = [migrate(seed) for seed in seeds]
    TARGET.write_bytes(
        (json.dumps(migrated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    print(
        f"wrote {TARGET} ({len(migrated)} environments, "
        f"{sum(len(seed['init_ref_tools']) for seed in migrated)} tools)"
    )


if __name__ == "__main__":
    main()
