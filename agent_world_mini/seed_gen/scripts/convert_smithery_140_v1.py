"""Convert the prepared Smithery catalog to the environment seed v1 format."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


SEED_GEN_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = SEED_GEN_DIR / "data"
DEFAULT_SOURCE = DATA_DIR / "prepared_environments.json"
DEFAULT_OUTPUT = DATA_DIR / "smithery_140_v1_0824.json"
SMITHERY_SERVER_BASE_URL = "https://smithery.ai/servers"
EXPECTED_ENVIRONMENT_COUNT = 140

SOURCE_METADATA_FIELDS = (
    ("id", "source_id"),
    ("qualifiedName", "qualified_name"),
    ("displayName", "display_name"),
    ("namespace", "namespace"),
    ("slug", "slug"),
    ("verified", "verified"),
    ("useCount", "use_count"),
    ("remote", "remote"),
    ("isDeployed", "is_deployed"),
    ("unlisted", "unlisted"),
    ("inactive", "inactive"),
    ("createdAt", "created_at"),
    ("homepage", "homepage"),
    ("bySmithery", "by_smithery"),
    ("owner", "owner"),
    ("score", "score"),
    ("deploymentUrl", "deployment_url"),
)

CONNECTION_FIELD_NAMES = {
    "type": "type",
    "deploymentUrl": "deployment_url",
    "configSchema": "config_schema",
    "bundleUrl": "bundle_url",
    "runtime": "runtime",
}

CONSUMED_OR_DELETED_FIELDS = {
    "description",
    "dataDirections",
    "iconUrl",
    "organizationStatus",
    "tools",
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _global_id_name(qualified_name: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z]+", "_", qualified_name).strip("_").lower()
    if not normalized:
        raise ValueError(f"qualifiedName cannot form a global ID: {qualified_name!r}")
    return normalized


def _convert_connections(connections: Any) -> list[dict[str, Any]]:
    if not isinstance(connections, list):
        raise TypeError("connections must be a list")

    converted: list[dict[str, Any]] = []
    for connection in connections:
        if not isinstance(connection, dict):
            raise TypeError("each connection must be an object")
        unknown = set(connection) - set(CONNECTION_FIELD_NAMES)
        if unknown:
            raise ValueError(f"unsupported connection fields: {sorted(unknown)}")
        converted.append(
            {
                CONNECTION_FIELD_NAMES[key]: copy.deepcopy(value)
                for key, value in connection.items()
            }
        )
    return converted


def _source_metadata(environment: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        target_name: copy.deepcopy(environment.get(source_name))
        for source_name, target_name in SOURCE_METADATA_FIELDS
    }
    metadata["connections"] = _convert_connections(environment.get("connections", []))

    # MCP prompts and resources occur only on some source records. They are
    # reference metadata, not AgentWorld tasks, so retain them under others.
    for optional_field in ("prompts", "resources"):
        if optional_field in environment:
            metadata[optional_field] = copy.deepcopy(environment[optional_field])
    return metadata


def convert_environment(
    environment: dict[str, Any], *, source: str, index: int
) -> dict[str, Any]:
    qualified_name = environment.get("qualifiedName")
    if not isinstance(qualified_name, str) or not qualified_name.strip():
        raise ValueError(f"environment {index} has no valid qualifiedName")
    if not isinstance(environment.get("tools"), list):
        raise TypeError(f"environment {qualified_name!r} has no tools list")

    known_source_fields = {
        source_name for source_name, _ in SOURCE_METADATA_FIELDS
    } | {"connections", "prompts", "resources"} | CONSUMED_OR_DELETED_FIELDS
    unknown_fields = set(environment) - known_source_fields
    if unknown_fields:
        raise ValueError(
            f"environment {qualified_name!r} has unsupported fields: "
            f"{sorted(unknown_fields)}"
        )

    return {
        "global_id": f"{source}_{_global_id_name(qualified_name)}_{index}",
        "schema_version": "1.0",
        "environment": {
            "basic_info": {
                "source": source,
                "url": f"{SMITHERY_SERVER_BASE_URL}/{qualified_name}",
                "name": qualified_name,
                "index": index,
            },
            "description": environment["description"],
            "domain": {
                "level1": "general",
                "level2": None,
                "level3": None,
            },
        },
        "init_ref_tools": copy.deepcopy(environment["tools"]),
        "init_ref_tasks": [],
        "others": {
            "source_metadata": _source_metadata(environment),
            "data_directions": copy.deepcopy(environment.get("dataDirections", [])),
            "organization_status": environment.get("organizationStatus"),
        },
    }


def build_output(source_document: dict[str, Any]) -> list[dict[str, Any]]:
    environments = source_document.get("environments")
    if not isinstance(environments, list):
        raise TypeError("source document must contain an environments list")
    if len(environments) != EXPECTED_ENVIRONMENT_COUNT:
        raise ValueError(
            f"expected {EXPECTED_ENVIRONMENT_COUNT} environments, got {len(environments)}"
        )
    if source_document.get("prepared") != len(environments):
        raise ValueError("prepared count does not match the environments list")

    source = source_document.get("catalog")
    if not isinstance(source, str) or not source:
        raise ValueError("source document has no catalog name")

    qualified_names = [environment.get("qualifiedName") for environment in environments]
    if len(set(qualified_names)) != len(qualified_names):
        raise ValueError("qualifiedName values must be unique")

    output = [
        convert_environment(environment, source=source, index=index)
        for index, environment in enumerate(environments, start=1)
    ]
    validate_output(source_document, output)
    return output


def validate_output(
    source_document: dict[str, Any], output: list[dict[str, Any]]
) -> None:
    environments = source_document["environments"]
    if len(output) != len(environments):
        raise ValueError("output count does not match source count")

    global_ids: set[str] = set()
    for index, (source_environment, converted) in enumerate(
        zip(environments, output, strict=True), start=1
    ):
        qualified_name = source_environment["qualifiedName"]
        basic_info = converted["environment"]["basic_info"]
        expected_global_id = (
            f"{source_document['catalog']}_{_global_id_name(qualified_name)}_{index}"
        )
        if converted["global_id"] != expected_global_id:
            raise ValueError(f"global_id mismatch at environment {index}")
        if converted["global_id"] in global_ids:
            raise ValueError(f"duplicate global_id: {converted['global_id']}")
        global_ids.add(converted["global_id"])

        if basic_info["name"] != qualified_name:
            raise ValueError(f"name mismatch at environment {index}")
        expected_url = f"{SMITHERY_SERVER_BASE_URL}/{qualified_name}"
        if basic_info["url"] != expected_url:
            raise ValueError(f"URL mismatch at environment {index}")
        if basic_info["index"] != index:
            raise ValueError(f"index mismatch at environment {index}")
        if converted["init_ref_tools"] != source_environment["tools"]:
            raise ValueError(f"tools changed at environment {index}")
        if converted["init_ref_tasks"] != []:
            raise ValueError(f"unexpected initial tasks at environment {index}")

        source_metadata = converted["others"]["source_metadata"]
        if "iconUrl" in source_metadata or "icon_url" in source_metadata:
            raise ValueError(f"iconUrl was retained at environment {index}")
        for optional_field in ("prompts", "resources"):
            if optional_field in source_environment:
                if source_metadata.get(optional_field) != source_environment[optional_field]:
                    raise ValueError(
                        f"{optional_field} changed at environment {index}"
                    )


def write_output(path: Path, output: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temporary_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_document = _load_json(args.source)
    output = build_output(source_document)
    write_output(args.output, output)

    # Validate the serialized artifact, not only the in-memory representation.
    serialized_output = _load_json(args.output)
    validate_output(source_document, serialized_output)
    tool_count = sum(len(environment["init_ref_tools"]) for environment in output)
    print(f"Wrote {len(output)} environments and {tool_count} tools to {args.output}")


if __name__ == "__main__":
    main()
