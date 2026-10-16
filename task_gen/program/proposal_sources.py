"""Public parameter-source checks without orchestration or third-party dependencies."""

from __future__ import annotations

import json
import math
import operator
from pathlib import Path
import re


SELF_CHECKS = ("real_need_preserved", "meaningful_hardening", "public_rules_complete",
               "computation_boundary_respected", "no_padding", "answer_supported",
               "solver_plans_route", "deliverables_supported")
OPERATIONS = {"add": operator.add, "subtract": operator.sub,
              "multiply": operator.mul, "divide": operator.truediv}
UNIT_FACTORS = {"Hz": 1, "kHz": 1_000, "MHz": 1_000_000, "GHz": 1_000_000_000,
                "THz": 1_000_000_000_000}
PUBLIC_PROTOCOL_FIELDS = ("model", "provider_type", "reasoning_effort", "context_cap", "max_output_tokens",
                          "read_default_chars", "read_max_chars", "max_total_tool_calls", "compaction_trigger_ratio")
EXPLORATION_TOOLS = frozenset({"Read", "Grep", "Glob"})
CONTROL_PARAMETERS = {
    "query", "search_query", "search_text", "query_text", "keywords",
    "page", "page_size", "per_page", "limit", "offset", "context_chars", "render_dpi", "page_numbers",
    "sort", "sort_by", "order", "order_by", "fields", "columns", "projection", "group_by",
    "format", "output_format",
}


class SourceCheckError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def same_json(left, right) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def same_source_value(left, right) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        if any(type(value) is float and not math.isfinite(value) for value in (left, right)):
            return False
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_source_value(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(same_source_value(first, second) for first, second in zip(left, right))
    return left == right


def pointer(document, path: str):
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise ValueError("source requires a JSON Pointer")
    for part in path.split("/")[1:] if path else ():
        if "~" in part.replace("~0", "").replace("~1", ""):
            raise ValueError("invalid JSON Pointer escape")
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(document, list):
            if not part.isascii() or not part.isdigit() or str(int(part)) != part:
                raise ValueError("invalid JSON array index")
            document = document[int(part)]
        elif isinstance(document, dict):
            document = document[part]
        else:
            raise ValueError("source pointer traverses a scalar")
    return document


def argument_leaves(value, path="") -> dict:
    if isinstance(value, (dict, list)) and value:
        items = value.items() if isinstance(value, dict) else enumerate(value)
        result = {}
        for key, item in items:
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            result.update(argument_leaves(item, path + "/" + escaped))
        return result
    return {path: value}


def argument_schema(schema: dict, path: str) -> dict:
    for token in path.split("/")[1:] if path else ():
        token = token.replace("~1", "/").replace("~0", "~")
        schema = schema.get("items", {}) if schema.get("type") == "array" else schema.get("properties", {}).get(token, {})
    return schema


def parameter_source_values(arguments: dict, sources: dict) -> dict:
    leaves = argument_leaves(arguments) if arguments else {}
    if not isinstance(sources, dict):
        raise ValueError("every parameter leaf needs exactly one source")
    covered, expected, errors = set(), {}, []
    for path in sources:
        if not isinstance(path, str):
            errors.append("parameter sources require JSON Pointer keys")
            continue
        matching = {leaf for leaf in leaves if path == "" or leaf == path or leaf.startswith(path + "/")}
        if not matching:
            errors.append(f"extra source path {path!r}")
            continue
        if covered & matching:
            errors.append(f"overlapping source path {path!r}")
        expected[path] = pointer(arguments, path)
        covered.update(matching)
    if covered != set(leaves):
        errors.append("missing source paths: " + ", ".join(sorted(set(leaves) - covered)))
    if errors:
        raise SourceCheckError(["every parameter leaf needs exactly one source: " + error for error in errors])
    return expected


def _schema_choice_allowed(value, schema: dict) -> bool:
    allowed = list(schema.get("enum", []))
    if "default" in schema:
        allowed.append(schema["default"])
    if any(same_source_value(value, option) for option in allowed):
        return True
    if schema.get("type") == "boolean" and type(value) is bool:
        return True
    if schema.get("type") == "object" and value == {} and not schema.get("required"):
        return True
    if schema.get("type") != "array" or not isinstance(value, list):
        return False
    if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf):
        return False
    if schema.get("uniqueItems") and any(
            same_source_value(item, earlier) for index, item in enumerate(value) for earlier in value[:index]):
        return False
    if not value:
        return True
    items = schema.get("items")
    return isinstance(items, dict) and all(_schema_choice_allowed(item, items) for item in value)


def source_value(source: dict, *, task: dict, protocol: dict, schema: dict,
                 preceding: list[dict], current_start: int | None, depth: int = 0,
                 public_files: dict[str, Path] | None = None, argument_path: str = ""):
    if not isinstance(source, dict) or depth > 4:
        raise ValueError("invalid or overly complex parameter source")
    kind = source.get("kind")
    if kind == "agent_choice":
        if (set(source) != {"kind", "value", "reason"} or not isinstance(source["reason"], str)
                or not source["reason"].strip()):
            raise ValueError("agent choices require a value and operational reason")
        if (not argument_path.startswith("/") or argument_path.count("/") != 1
                or argument_path[1:] not in CONTROL_PARAMETERS):
            raise ValueError("agent choices are limited to query, pagination, sorting, projection and display controls")
        value = source["value"]
        scalar_types = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,)}
        value_type = schema.get("type")
        if value_type == "array" and isinstance(value, list):
            item_types = scalar_types.get(schema.get("items", {}).get("type"), ())
            allowed = bool(item_types) and all(type(item) in item_types for item in value)
        else:
            allowed = type(value) in scalar_types.get(value_type, ())
        if not allowed:
            raise ValueError("agent choice must match the public scalar or scalar-array parameter type")
        return value
    if kind == "schema_choice":
        if (set(source) != {"kind", "value", "reason"} or not isinstance(source["reason"], str)
                or not source["reason"].strip()):
            raise ValueError("schema choices require a value and operational reason")
        if not _schema_choice_allowed(source["value"], schema):
            raise ValueError("schema choice must be an actual public enum, boolean or default")
        return source["value"]
    if kind == "derived":
        if (set(source) != {"kind", "operation", "inputs"} or source["operation"] not in OPERATIONS
                or not isinstance(source["inputs"], list) or len(source["inputs"]) != 2):
            raise ValueError("only two-operand basic arithmetic is supported")
        values = [source_value(item, task=task, protocol=protocol, schema=schema,
                  preceding=preceding, current_start=current_start, depth=depth + 1,
                  public_files=public_files) for item in source["inputs"]]
        if any(type(value) not in (int, float) for value in values):
            raise ValueError("basic arithmetic sources must be numbers")
        result = OPERATIONS[source["operation"]](*values)
        json.dumps(result, allow_nan=False)
        return result
    if kind == "new_identifier":
        if (set(source) != {"kind", "value", "reason"} or not isinstance(source["value"], str)
                or not source["value"].strip() or not isinstance(source["reason"], str)
                or not source["reason"].strip()):
            raise ValueError("new identifiers require an explicit value and business reason")
        return source["value"]
    if kind == "task":
        if not {"kind", "quote"} <= source.keys() or source.keys() - {"kind", "quote", "as"}:
            raise ValueError("task sources require an exact public quote")
        value = task["task_text"]
    elif kind == "file":
        if (not {"kind", "path"} <= source.keys() or source.keys() - {"kind", "path", "quote", "as"}
                or not isinstance(source["path"], str) or not public_files or source["path"] not in public_files):
            raise ValueError("file sources must reference an existing frozen public workspace file")
        if "quote" not in source:
            if "as" in source:
                raise ValueError("file content decoding requires an actual quote")
            return source["path"]
        value = public_files[source["path"]].read_text(encoding="utf-8")
    elif kind in {"schema", "protocol"}:
        if set(source) != {"kind", "pointer"}:
            raise ValueError("schema/protocol source fields differ")
        public_protocol = {field: protocol[field] for field in PUBLIC_PROTOCOL_FIELDS}
        return pointer(schema if kind == "schema" else public_protocol, source["pointer"])
    elif kind == "call":
        if not {"kind", "call", "pointer"} <= source.keys() or source.keys() - {"kind", "call", "pointer", "quote", "as"}:
            raise ValueError("tool sources require an exact earlier visible result")
        index = source["call"]
        if type(index) is not int or not 1 <= index <= len(preceding):
            raise ValueError("parameter source refers to an absent or future call")
        previous = preceding[index - 1]
        if current_start is not None and previous["completed_event_sequence"] >= current_start:
            raise ValueError("parameter source was not available when the call started")
        value = pointer(previous["visible_output"], source["pointer"])
    else:
        raise ValueError("parameter source must be public, observed, or explicitly declared")
    if "quote" in source:
        quote = source["quote"]
        if not isinstance(value, str) or not isinstance(quote, str) or not quote or quote not in value:
            raise ValueError("quoted source is not present in the actual public text")
        if source.get("as", "text") == "text":
            return quote
        if source["as"] == "frequency_hz":
            match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(Hz|kHz|MHz|GHz|THz)\s*", quote)
            if match is None:
                raise ValueError("frequency sources require a numeric SI frequency")
            result = float(match.group(1)) * UNIT_FACTORS[match.group(2)]
            return int(result) if result.is_integer() else result
        if source["as"] != "json":
            raise ValueError("quoted sources must explicitly choose text or JSON")
        return json.loads(quote)
    if "as" in source:
        if source["as"] == "json" and not isinstance(value, str):
            json.dumps(value, allow_nan=False)
            return value
        raise ValueError("source decoding is only meaningful with an actual quote")
    return value


def _value_pointers(value, expected, path=""):
    matches = []
    if same_source_value(value, expected):
        matches.append(path)
    if isinstance(value, dict):
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            matches.extend(_value_pointers(item, expected, path + "/" + escaped))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(_value_pointers(item, expected, path + "/" + str(index)))
    return matches


def check_solution(proposal: dict, original: dict, observed: list[dict], protocol: dict,
                   schemas: dict, *, validation: dict | None = None,
                   public_files: dict[str, Path] | None = None) -> tuple[list[int], dict]:
    fields = {"task_text", "real_need", "reference_answer", "solution_calls", "answer_sources", "validation_id",
              "self_check", "self_check_notes"}
    if not isinstance(proposal, dict) or set(proposal) != fields:
        raise ValueError("author must return the task, observed call list, answer, and self-check only")
    for field in ("task_text", "real_need", "reference_answer"):
        if not isinstance(proposal[field], str) or not proposal[field].strip():
            raise ValueError(f"author output requires {field}")
    if " ".join(proposal["task_text"].split()) == " ".join(original["task_text"].split()):
        raise ValueError("the task was not rewritten")
    if (not isinstance(proposal["self_check"], dict)
            or proposal["self_check"] != dict.fromkeys(SELF_CHECKS, True)
            or any(type(value) is not bool for value in proposal["self_check"].values())
            or not isinstance(proposal["self_check_notes"], list) or not proposal["self_check_notes"]
            or any(not isinstance(item, str) or not item.strip() for item in proposal["self_check_notes"])):
        raise ValueError("author self-check is incomplete; it is not an independent correctness proof")
    calls = proposal["solution_calls"]
    if not isinstance(calls, list) or not 1 <= len(calls) <= protocol["max_total_tool_calls"]:
        raise ValueError("reference call list is empty or exceeds the actual budget")
    if any(not isinstance(call, dict) or call.get("tool") in EXPLORATION_TOOLS
           or call.get("tool") not in schemas for call in calls):
        raise ValueError("reference lists contain public business MCP tools only; file tools are exploration-only")
    old_count = sum(call is None or call.get("tool") not in EXPLORATION_TOOLS
                    for call in original["reference"]["tool_calls"])
    if old_count < 18 and len(calls) <= old_count:
        raise ValueError("reference calls below the eighteen-call threshold must increase")
    if validation is not None:
        records = validation["result"].get("calls")
        if not isinstance(records, list) or len(records) != len(calls):
            raise ValueError("successful validation must retain every actual execution record")
        observed = []
        for index, (requested, record) in enumerate(zip(validation["request"]["calls"], records), 1):
            if record.get("index") != index or record.get("tool") != requested["tool"] or record.get("error"):
                raise ValueError("validation execution identity or order differs from its request")
            observed.append({"name": record["tool"], "arguments": requested["arguments"],
                             "tool_kind": "validated_execution", "is_error": False,
                             "visible_output": record["result"]})
    task, selected, indices, declarations = {"task_text": proposal["task_text"]}, [], [], []
    choices = []
    source_errors = []
    for position, call in enumerate(calls, 1):
        required = {"tool", "arguments", "argument_sources", "purpose"}
        if validation is None:
            required.add("observed_call")
        if not isinstance(call, dict) or set(call) != required:
            raise ValueError("reference call must include its real observation and parameter sources")
        number = call["observed_call"] if validation is None else position
        if type(number) is not int or not (indices[-1] if indices else 0) < number <= len(observed):
            raise ValueError("reference must select real observations once, in actual start order")
        actual = observed[number - 1]
        name = actual["name"].removeprefix("mcp__agent_world_distill__")
        if (actual["tool_kind"] == "harness_support"
                or name != call["tool"] or not same_json(call["arguments"], actual["arguments"])
                or actual["is_error"] or not isinstance(call["purpose"], str) or not call["purpose"].strip()):
            raise ValueError("reference differs from its successful actual tool call")
        sources = call["argument_sources"]
        try:
            expected_values = parameter_source_values(call["arguments"], sources)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            source_errors.extend(f"call {position}: {message}" for message in
                                 (error.errors if isinstance(error, SourceCheckError) else [str(error)]))
            expected_values = {}
        for path, expected in expected_values.items():
            source = sources[path]
            schema = schemas.get(name, {})
            if isinstance(source, dict) and source.get("kind") in {"schema_choice", "agent_choice"}:
                schema = argument_schema(schema, path)
            try:
                value = source_value(source, task=task, protocol=protocol, schema=schema,
                    preceding=selected, current_start=actual.get("started_event_sequence"), public_files=public_files,
                    argument_path=path)
                if not same_source_value(expected, value):
                    raise ValueError("differs from its declared public source")
                if source["kind"] == "new_identifier":
                    declarations.append({"call": len(selected) + 1, "argument": path, "reason": source["reason"]})
                if source["kind"] == "agent_choice":
                    choices.append({"call": len(selected) + 1, "argument": path,
                                    "value": value, "reason": source["reason"]})
            except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as error:
                source_errors.append(f"call {position} parameter {path}: {type(error).__name__}: {error}")
        indices.append(number)
        selected.append(actual)
    if not isinstance(proposal["answer_sources"], list) or not proposal["answer_sources"]:
        source_errors.append("reference answer needs actual visible evidence")
    else:
        for position, source in enumerate(proposal["answer_sources"], 1):
            try:
                if not isinstance(source, dict) or source.get("kind") not in {"call", "file"}:
                    raise ValueError("answer evidence must reference actual tool results or frozen public files")
                source_value(source, task=task, protocol=protocol, schema={}, preceding=selected,
                    current_start=max(call["completed_event_sequence"] for call in selected) + 1
                    if validation is None else None, public_files=public_files)
            except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as error:
                source_errors.append(f"answer source {position}: {type(error).__name__}: {error}")
    if source_errors:
        raise SourceCheckError(source_errors)
    return indices, {"call_count": len(calls), "original_call_count": old_count,
        "parameter_sources_match": True, "author_self_check_only": True,
        "new_identifier_declarations": declarations, "semantic_correctness_proved": False,
        "agent_choices": choices,
        "execution_evidence": "clean_state_validation" if validation is not None else "author_cli_observations"}
