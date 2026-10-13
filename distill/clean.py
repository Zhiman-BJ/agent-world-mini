"""Build compact, namespace-neutral training views of distilled trajectories."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any


MCP_PREFIX = "mcp__agent_world_distill__"
NATIVE_TOOL_NAMES = {"Read", "Grep", "Glob"}
PRESERVED_UNAVAILABLE_TOOL_NAMES = {"Bash"}
_SHORTENED_SUFFIX = re.compile(r"(?:_[0-9a-f]{8}|-[0-9a-f]{8}|_-[0-9a-f]{7,8})$")
_CUSTOM_SYSTEM_PREFIX = """Native `Read`, `Grep`, and `Glob` are only for files in the empty model workspace,
the current session's saved long tool results, and the current session's wire log.
Do not use them to search the environment Scope, `execution-state`, delivery
directories, host paths, or another task. Use the environment MCP tools to inspect
and modify business state; a long MCP result can be recovered from the path Kimi
provides in the current session.

"""
_CWD_DECLARATION = re.compile(
    r"(The current working directory is `)(/[^`\n]+)(`)"
)
_KIMI_SESSION_PATH = re.compile(
    r"/tmp/agent-world-kimi-home-[^/\s`\"']+/sessions/"
    r"[^/\s`\"']+/session_[^/\s`\"']+"
)
_ADDED_DESCRIPTION = re.compile(
    r"\n(?:Target resources:|Target objects:|Preconditions:|Side effects:|"
    r"Filesystem Scope paths are relative to the named scope root\.|Output contract:)",
)
_MCP_RESULT_EXTRAS = re.compile(
    r"(?:^|\n)<mcp-result-extras>\s*(\{.*\})\s*</mcp-result-extras>\s*$",
    re.DOTALL,
)
_MCP_WRAPPER_LINE = re.compile(
    r"(?m)^(?:\d+\t)?(?:Tool result: (?:success|error)\.[^\n]*|"
    r"</?mcp-result-extras>)\s*(?:\n|$)"
)

_SCHEMA_MAP_KEYS = {
    "properties", "patternProperties", "$defs", "definitions", "dependentSchemas",
}
_SCHEMA_SINGLE_KEYS = {
    "items", "additionalProperties", "contains", "propertyNames", "not", "if", "then", "else",
}
_SCHEMA_LIST_KEYS = {"prefixItems", "oneOf", "anyOf", "allOf"}


def _compact_space(value: str) -> str:
    return " ".join(value.split())


def validate_generated_environment_context(value: str) -> str:
    """Validate model-written context without synthesizing or rewriting it."""
    if not isinstance(value, str):
        raise ValueError("generated environment context must be a string")
    value = value.strip()
    if not 40 <= len(value) <= 500:
        raise ValueError("generated environment context must contain 40 to 500 characters")
    forbidden = {
        "record_sets", "relationships", "filesystem_scopes", MCP_PREFIX,
        "```", "工具调用顺序", "第一步", "第二步",
    }
    present = sorted(token for token in forbidden if token in value)
    if present:
        raise ValueError(f"generated environment context contains forbidden content: {present}")
    return value


def cleaned_prompt(
    task: dict[str, Any], environment_context: str | None = None,
) -> str:
    """Return the original task text, optionally preceded by reviewed context."""
    original = task.get("prompt")
    task_text = task.get("task_text")
    if not isinstance(task_text, str) or not task_text.strip():
        task_text = str(task.get("task") or original or "")
    task_text = task_text.strip()
    if environment_context is None:
        return task_text
    return environment_context + "\n\n" + task_text


def _canonical_tool_name(observed: str, canonical_names: set[str]) -> str:
    if observed in NATIVE_TOOL_NAMES:
        return observed
    match = re.match(r"^mcp__[A-Za-z0-9_-]+?__", observed)
    prefix = match.group() if match else MCP_PREFIX
    short = observed[len(prefix):] if match else observed
    if short in canonical_names:
        return short
    suffix = _SHORTENED_SUFFIX.search(observed)
    if suffix is None:
        return short
    candidates = [
        candidate
        for candidate in canonical_names
        if (prefix + candidate).startswith(observed[:suffix.start()])
    ]
    return candidates[0] if len(candidates) == 1 else short


def tool_name_mapping(
    trajectory: dict[str, Any], request: dict[str, Any] | None = None,
) -> dict[str, str]:
    canonical_names = {
        str(name) for name in trajectory.get("environment", {}).get("tool_names", [])
        if isinstance(name, str)
    }
    observed = set()
    for step in trajectory.get("steps", []):
        for call in step.get("tool_calls", []):
            if isinstance(call.get("name"), str):
                observed.add(call["name"])
    if isinstance(request, dict):
        for tool in request.get("tools", []):
            name = tool.get("function", {}).get("name") if isinstance(tool, dict) else None
            if isinstance(name, str):
                observed.add(name)
    mapping = {MCP_PREFIX + name: name for name in canonical_names}
    mapping.update({name: name for name in canonical_names | NATIVE_TOOL_NAMES})
    for name in observed:
        mapping[name] = _canonical_tool_name(name, canonical_names)
    return mapping


def _clean_string(
    value: str, mapping: dict[str, str], runtime_paths: tuple[str, ...] = (),
    workspace_path: str = "/workspace",
) -> str:
    for source in sorted(mapping, key=len, reverse=True):
        if source != mapping[source]:
            value = value.replace(source, mapping[source])
    value = re.sub(r"mcp__[A-Za-z0-9_-]+?__", "", value)
    for path in sorted(runtime_paths, key=len, reverse=True):
        value = value.replace(path, workspace_path)
        if path.endswith("/model-workspace") and workspace_path.endswith("/model-workspace"):
            source_task_root = path.removesuffix("/model-workspace")
            portable_task_root = workspace_path.removesuffix("/model-workspace")
            value = value.replace(source_task_root, portable_task_root)
    value = _CWD_DECLARATION.sub(
        lambda match: match.group(1) + workspace_path + match.group(3), value
    )
    return _KIMI_SESSION_PATH.sub("/session", value)


def _clean_value(
    value: Any, mapping: dict[str, str], runtime_paths: tuple[str, ...] = (),
    workspace_path: str = "/workspace",
) -> Any:
    if isinstance(value, str):
        return _clean_string(value, mapping, runtime_paths, workspace_path)
    if isinstance(value, list):
        return [
            _clean_value(item, mapping, runtime_paths, workspace_path)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _clean_value(item, mapping, runtime_paths, workspace_path)
            for key, item in value.items()
        }
    return value


def _runtime_paths(request: dict[str, Any]) -> tuple[str, ...]:
    paths = set()
    for message in request.get("messages", []):
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        match = _CWD_DECLARATION.search(content)
        if match is not None:
            paths.add(match.group(2).rstrip("/"))
    return tuple(sorted(paths))


def _strip_added_description(description: str) -> str:
    match = _ADDED_DESCRIPTION.search(description)
    return description[:match.start()].rstrip() if match is not None else description


def _schema_without_description_annotations(schema: Any) -> Any:
    """Remove description annotations without dropping a property named description."""
    if not isinstance(schema, dict):
        return deepcopy(schema)
    result = {}
    for key, value in schema.items():
        if key == "description":
            continue
        if key in _SCHEMA_MAP_KEYS and isinstance(value, dict):
            result[key] = {
                name: _schema_without_description_annotations(child)
                for name, child in value.items()
            }
        elif key in _SCHEMA_SINGLE_KEYS and isinstance(value, dict):
            result[key] = _schema_without_description_annotations(value)
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            result[key] = [
                _schema_without_description_annotations(child)
                if isinstance(child, dict) else deepcopy(child)
                for child in value
            ]
        else:
            result[key] = deepcopy(value)
    return result


def _merge_schema_descriptions(target: dict[str, Any], source: dict[str, Any]) -> None:
    description = source.get("description")
    if isinstance(description, str) and description.strip():
        target["description"] = description.strip()

    for key in _SCHEMA_MAP_KEYS:
        target_children = target.get(key)
        source_children = source.get(key)
        if not isinstance(target_children, dict) or not isinstance(source_children, dict):
            continue
        for name, target_child in target_children.items():
            source_child = source_children.get(name)
            if isinstance(target_child, dict) and isinstance(source_child, dict):
                _merge_schema_descriptions(target_child, source_child)

    for key in _SCHEMA_SINGLE_KEYS:
        target_child = target.get(key)
        source_child = source.get(key)
        if isinstance(target_child, dict) and isinstance(source_child, dict):
            _merge_schema_descriptions(target_child, source_child)

    for key in _SCHEMA_LIST_KEYS:
        target_children = target.get(key)
        source_children = source.get(key)
        if not isinstance(target_children, list) or not isinstance(source_children, list):
            continue
        for target_child, source_child in zip(target_children, source_children):
            if isinstance(target_child, dict) and isinstance(source_child, dict):
                _merge_schema_descriptions(target_child, source_child)


def _missing_schema_descriptions(schema: dict[str, Any], pointer: str = "") -> list[str]:
    missing = []
    for key in _SCHEMA_MAP_KEYS:
        children = schema.get(key)
        if not isinstance(children, dict):
            continue
        for name, child in children.items():
            if not isinstance(child, dict):
                continue
            child_pointer = f"{pointer}/{key}/{name}"
            if not isinstance(child.get("description"), str) or not child["description"].strip():
                missing.append(child_pointer)
            missing.extend(_missing_schema_descriptions(child, child_pointer))
    for key in _SCHEMA_SINGLE_KEYS:
        child = schema.get(key)
        if not isinstance(child, dict):
            continue
        child_pointer = f"{pointer}/{key}"
        if not isinstance(child.get("description"), str) or not child["description"].strip():
            missing.append(child_pointer)
        missing.extend(_missing_schema_descriptions(child, child_pointer))
    for key in _SCHEMA_LIST_KEYS:
        children = schema.get(key)
        if not isinstance(children, list):
            continue
        for index, child in enumerate(children):
            if isinstance(child, dict):
                missing.extend(_missing_schema_descriptions(child, f"{pointer}/{key}/{index}"))
    return missing


def apply_repaired_tool_definitions(
    request: dict[str, Any],
    tool_definitions: list[dict[str, Any]],
    *,
    expected_names: set[str],
) -> set[str]:
    """Inject repaired MCP descriptions into a cleaned Chat Completions request."""
    definitions = {
        str(tool.get("name")): tool
        for tool in tool_definitions
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    if set(definitions) != expected_names:
        raise ValueError(
            "repaired tool set differs from trajectory environment: "
            f"missing={sorted(expected_names - set(definitions))[:5]}, "
            f"extra={sorted(set(definitions) - expected_names)[:5]}"
        )

    replaced = set()
    for item in request.get("tools", []):
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if name not in definitions:
            continue
        definition = definitions[name]
        description = definition.get("description")
        input_schema = definition.get("inputSchema")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"repaired tool {name} has no description")
        if not isinstance(input_schema, dict):
            raise ValueError(f"repaired tool {name} has no inputSchema")
        parameters = function.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError(f"cleaned request tool {name} has no parameters")
        original_semantics = _schema_without_description_annotations(parameters)
        function["description"] = description.strip()
        _merge_schema_descriptions(parameters, input_schema)
        if _schema_without_description_annotations(parameters) != original_semantics:
            raise AssertionError(f"description merge changed schema semantics for {name}")
        missing = _missing_schema_descriptions(parameters)
        if missing:
            raise ValueError(
                f"repaired definitions do not describe every historical field for {name}: "
                f"{missing[:5]}"
            )
        replaced.add(name)

    if replaced != expected_names:
        raise ValueError(
            "cleaned request is missing repaired environment tools: "
            f"{sorted(expected_names - replaced)[:5]}"
        )
    return replaced


def _normalize_mcp_output(output: Any) -> Any:
    if (
        not isinstance(output, dict)
        or "output" not in output
        or not set(output).issubset({"output", "isError"})
    ):
        return output
    text = output["output"]
    if not isinstance(text, str):
        return output
    extras_match = _MCP_RESULT_EXTRAS.search(text)
    if extras_match is not None:
        try:
            extras = json.loads(extras_match.group(1))
        except json.JSONDecodeError:
            return output
        if isinstance(extras, dict) and "structuredContent" in extras:
            return extras["structuredContent"]
        text = text[:extras_match.start()].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return output


def _normalize_tool_call_output(call: dict[str, Any]) -> Any:
    environment_record = call.get("environment_record")
    if (
        call.get("tool_kind") == "environment_mcp"
        and isinstance(environment_record, dict)
        and isinstance(environment_record.get("result"), dict)
    ):
        return deepcopy(environment_record["result"])
    if call.get("tool_kind") == "native_file_tool":
        output = deepcopy(call.get("output"))
        if isinstance(output, dict) and isinstance(output.get("output"), str):
            output["output"] = _MCP_WRAPPER_LINE.sub("", output["output"]).strip()
        return output
    return _normalize_mcp_output(call.get("output"))


def clean_initial_request(
    request_record: dict[str, Any], trajectory: dict[str, Any], prompt: str,
    *, workspace_path: str = "/workspace",
    tool_definitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request = request_record.get("request")
    if not isinstance(request, dict):
        raise ValueError("model request record has no request object")
    mapping = tool_name_mapping(trajectory, request)
    runtime_paths = _runtime_paths(request)
    cleaned_request = _clean_value(
        deepcopy(request), mapping, runtime_paths, workspace_path
    )
    for message in cleaned_request.get("messages", []):
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            if message["content"].startswith(_CUSTOM_SYSTEM_PREFIX):
                message["content"] = message["content"][len(_CUSTOM_SYSTEM_PREFIX):]
    for tool in cleaned_request.get("tools", []):
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and isinstance(function.get("description"), str):
            function["description"] = _strip_added_description(function["description"])
    if tool_definitions is not None:
        expected_names = {
            str(name)
            for name in trajectory.get("environment", {}).get("tool_names", [])
            if isinstance(name, str)
        }
        apply_repaired_tool_definitions(
            cleaned_request,
            tool_definitions,
            expected_names=expected_names,
        )
    old_prompt = trajectory.get("task", {}).get("prompt")
    cleaned_old_prompt = (
        _clean_string(old_prompt, mapping, runtime_paths, workspace_path)
        if isinstance(old_prompt, str) else old_prompt
    )
    replaced = 0
    for message in cleaned_request.get("messages", []):
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        if message["content"] == cleaned_old_prompt:
            message["content"] = prompt
            replaced += 1
    if replaced != 1:
        raise ValueError(f"expected exactly one task prompt in initial request, replaced {replaced}")
    names = [
        tool.get("function", {}).get("name")
        for tool in cleaned_request.get("tools", [])
        if isinstance(tool, dict)
    ]
    if len(names) != len(set(names)):
        raise ValueError("tool names collide after namespace cleaning")
    payload = {
        "source": {"path": "input.json", "line": 1},
        "request_id": request_record.get("request_id"),
        "recorded_at": request_record.get("recorded_at"),
        "request": cleaned_request,
    }
    encoded = json.dumps(cleaned_request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    payload["sha256"] = hashlib.sha256(encoded).hexdigest()
    return payload


def clean_trajectory(
    trajectory: dict[str, Any], request_record: dict[str, Any], *, source: str,
    environment_context: str | None = None, workspace_path: str = "/workspace",
    tool_definitions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a cleaned trajectory plus its cleaned first model input."""
    if environment_context is not None:
        environment_context = validate_generated_environment_context(environment_context)
    if workspace_path != "/workspace" and (
        not workspace_path.startswith("/")
        or not workspace_path.endswith("/model-workspace")
    ):
        raise ValueError("workspace path must be absolute and end with /model-workspace")
    prompt = cleaned_prompt(trajectory["task"], environment_context)
    request = request_record.get("request")
    mapping = tool_name_mapping(trajectory, request)
    runtime_paths = _runtime_paths(request) if isinstance(request, dict) else ()
    cleaned = _clean_value(
        deepcopy(trajectory), mapping, runtime_paths, workspace_path
    )
    task = cleaned["task"]
    task["prompt"] = prompt
    original_environment = trajectory["environment"]
    support_tool_names = sorted({
        _canonical_tool_name(str(call.get("name", "")), set(original_environment.get("tool_names", [])))
        for step in trajectory.get("steps", [])
        for call in step.get("tool_calls", [])
        if call.get("tool_kind") == "harness_support"
    })
    cleaned["environment"] = {
        "environment_id": original_environment.get("environment_id"),
        "name": original_environment.get("name"),
        "tool_names": sorted({
            _canonical_tool_name(str(name), set(original_environment.get("tool_names", [])))
            for name in original_environment.get("tool_names", [])
        }),
        **({"description": environment_context} if environment_context is not None else {}),
        **({"support_tool_names": support_tool_names} if support_tool_names else {}),
    }
    for step in cleaned.get("steps", []):
        for call in step.get("tool_calls", []):
            if call.get("tool_kind") in {
                "environment_mcp", "harness_support", "native_file_tool",
            }:
                call["output"] = _normalize_tool_call_output(call)
                call.pop("environment_record", None)
    cleaned_input = clean_initial_request(
        request_record,
        trajectory,
        prompt,
        workspace_path=workspace_path,
        tool_definitions=tool_definitions,
    )
    allowed = (
        set(cleaned["environment"]["tool_names"])
        | set(cleaned["environment"].get("support_tool_names", []))
        | NATIVE_TOOL_NAMES
        | PRESERVED_UNAVAILABLE_TOOL_NAMES
        | {
            str(call.get("name"))
            for step in cleaned.get("steps", [])
            for call in step.get("tool_calls", [])
            if call.get("tool_kind") == "native_file_tool"
            and isinstance(call.get("name"), str)
        }
    )
    unexpected = {
        call["name"]
        for step in cleaned.get("steps", [])
        for call in step.get("tool_calls", [])
        if call.get("name") not in allowed
    }
    if unexpected:
        raise ValueError(f"uncanonicalized tool names: {sorted(unexpected)}")
    return cleaned, cleaned_input


def first_request_record(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    break
                return value
    raise ValueError(f"no model request found: {path}")


def first_request_record_from_source(source_directory: Path) -> dict[str, Any]:
    """Load the first request from raw evidence or a legacy embedded visualization."""
    source_directory = source_directory.expanduser().resolve()
    requests_path = source_directory / "raw/model_requests.jsonl"
    if requests_path.is_file():
        return first_request_record(requests_path)

    visualization_path = source_directory / "trajectory.html"
    if not visualization_path.is_file():
        raise ValueError(
            f"source has neither raw model requests nor trajectory.html: {source_directory}"
        )
    html = visualization_path.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="initial-request-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if match is None:
        raise ValueError(f"legacy visualization has no initial request: {visualization_path}")
    record = json.loads(match.group(1))
    if not isinstance(record, dict) or not isinstance(record.get("request"), dict):
        raise ValueError(f"legacy visualization has an invalid initial request: {visualization_path}")
    return record
