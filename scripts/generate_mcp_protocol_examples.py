"""基于本地 Smithery MCP 种子生成三套可执行协议示例环境。"""

from __future__ import annotations

import json
import copy
import re
import shutil
import tempfile
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from typing import Any

from jsonschema import Draft202012Validator, RefResolver


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_ROOT / "seed_gen" / "data" / "prepared_environments.json"
SCHEMA_DIR = PROJECT_ROOT / "schemas"
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "mcp_protocol_examples_3env_20260824"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def closed_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def array_of(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "array",
        "items": closed_object(properties, required),
    }


def normalize_entity_schema(entity_schema: dict[str, Any]) -> dict[str, Any]:
    """把示例中的 fields 类型简写展开为带说明的字段 Schema。"""

    normalized: dict[str, Any] = {}
    for entity_name, definition in entity_schema.items():
        if not isinstance(definition, dict):
            continue
        fields = definition.get("fields")
        if isinstance(fields, dict):
            normalized[entity_name] = {
                "description": definition.get("description", f"{entity_name} 实体记录。"),
                "fields": {
                    field: {
                        "type": field_type,
                        "description": f"{entity_name} 实体记录中的 {field} 业务字段。",
                    }
                    for field, field_type in fields.items()
                },
            }
        else:
            normalized[entity_name] = definition
    return normalized


def error_codes_from_code(code: str) -> list[str]:
    """从示例实现中的返回对象提取稳定业务错误码。"""

    values = re.findall(r"(?:_fail\(\s*['\"]|['\"]code['\"]\s*:\s*['\"])([a-z][a-z0-9_]*)", code)
    return sorted(set(values)) or ["internal_error"]


ERROR_SCHEMA = closed_object(
    {
        "code": {"type": "string"},
        "path": {"type": "string"},
        "message": {"type": "string", "minLength": 1},
        "retryable": {"type": "boolean"},
    },
    ["code", "path", "message", "retryable"],
)


def result_schema(data_properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "oneOf": [
            closed_object(
                {
                    "success": {"type": "boolean", "const": True},
                    "data": closed_object(data_properties, required),
                },
                ["success", "data"],
            ),
            closed_object(
                {
                    "success": {"type": "boolean", "const": False},
                    "error": json.loads(json.dumps(ERROR_SCHEMA)),
                },
                ["success", "error"],
            ),
        ]
    }


def tool(
    name: str,
    description: str,
    input_properties: dict[str, Any],
    required_inputs: list[str],
    output_properties: dict[str, Any],
    required_outputs: list[str],
    code: str,
) -> dict[str, Any]:
    output_schema = result_schema(output_properties, required_outputs)
    output_schema["oneOf"][1]["properties"]["error"]["properties"]["code"]["enum"] = error_codes_from_code(code)
    input_properties = copy.deepcopy(input_properties)
    for key, value in input_properties.items():
        if isinstance(value, dict) and "description" not in value:
            value["description"] = f"工具参数 {key}。"
    return {
        "name": name,
        "description": description,
        "inputSchema": closed_object(input_properties, required_inputs),
        "outputSchema": output_schema,
        "internal": {"code": dedent(code).strip() + "\n"},
    }


def seed_record(qualified_name: str) -> dict[str, Any]:
    entries = read_json(CATALOG_PATH)["environments"]
    item = next(entry for entry in entries if entry.get("qualifiedName") == qualified_name)
    return {
        "qualifiedName": item["qualifiedName"],
        "displayName": item.get("displayName"),
        "description": item.get("description"),
        "homepage": item.get("homepage"),
        "repository": item.get("repository"),
        "tools": [
            {
                "name": value.get("name"),
                "description": value.get("description"),
                "inputSchema": value.get("inputSchema"),
            }
            for value in item.get("tools", [])
        ],
        "dataDirections": item.get("dataDirections", []),
    }


def resource(
    resource_id: str,
    name: str,
    description: str,
    data_type: str,
    storage_type: str,
    path: str,
    format_name: str,
    writable: bool,
    *,
    source_resources: list[str] | None = None,
    entity_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "resource_id": resource_id,
        "name": name,
        "description": description,
        "data_type": data_type,
        "storage_type": storage_type,
        "path": path,
        "format": format_name,
        "writable": writable,
    }
    if source_resources is not None:
        value["source_resources"] = source_resources
    if entity_schema is not None:
        value["entity_schema"] = normalize_entity_schema(entity_schema)
    return value


def context7_environment(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    workspace = root / "workspace"
    write_json(workspace / "raw" / "mcp_seed.json", seed_record("upstash/context7-mcp"))

    raw_snapshot = {
        "libraries": [
            {"library_id": "/python/cpython", "name": "Python", "version": "3.13", "summary": "Python standard library reference snapshot."},
            {"library_id": "/pallets/flask", "name": "Flask", "version": "3.1", "summary": "Flask application and request handling snapshot."},
            {"library_id": "/pandas-dev/pandas", "name": "pandas", "version": "2.3", "summary": "pandas tabular data processing snapshot."},
        ],
        "sections": [
            {"section_id": "python_pathlib", "library_id": "/python/cpython", "version": "3.13", "title": "Path objects", "content": "pathlib.Path represents filesystem paths and provides read_text, write_text, glob, and resolve operations."},
            {"section_id": "python_json", "library_id": "/python/cpython", "version": "3.13", "title": "JSON encoding", "content": "json.dumps serializes Python values and json.loads parses JSON text into Python values."},
            {"section_id": "flask_routes", "library_id": "/pallets/flask", "version": "3.1", "title": "Routing", "content": "Use app.route or app.get to bind URL rules to view functions. Route variables are passed as function arguments."},
            {"section_id": "flask_testing", "library_id": "/pallets/flask", "version": "3.1", "title": "Testing applications", "content": "The Flask test client sends requests without running a live server and returns response objects for assertions."},
            {"section_id": "pandas_merge", "library_id": "/pandas-dev/pandas", "version": "2.3", "title": "Database-style joins", "content": "pandas.merge combines DataFrame rows using one or more key columns and supports inner, left, right, and outer joins."},
            {"section_id": "pandas_groupby", "library_id": "/pandas-dev/pandas", "version": "2.3", "title": "Group by operations", "content": "DataFrame.groupby splits rows into groups before aggregation, transformation, or filtering."},
        ],
    }
    write_json(workspace / "raw" / "documentation_snapshot.json", raw_snapshot)

    catalog = {
        "library": [dict(item) for item in raw_snapshot["libraries"]],
        "documentation_section": [dict(item) for item in raw_snapshot["sections"]],
    }
    write_json(workspace / "entities" / "documentation_catalog.json", catalog)

    search_index: dict[str, list[str]] = {}
    for section in raw_snapshot["sections"]:
        words = set(re.findall(r"[a-z0-9_]+", (section["title"] + " " + section["content"]).lower()))
        for word in words:
            if len(word) >= 4:
                search_index.setdefault(word, []).append(section["section_id"])
    write_json(workspace / "derived" / "search_index.json", search_index)
    (workspace / "reports").mkdir(parents=True, exist_ok=True)

    library_fields = {
        "library_id": {"type": "string"},
        "name": {"type": "string"},
        "version": {"type": "string"},
        "summary": {"type": "string"},
    }
    section_fields = {
        "section_id": {"type": "string"},
        "library_id": {"type": "string"},
        "version": {"type": "string"},
        "title": {"type": "string"},
        "content": {"type": "string"},
    }
    tools = [
        tool(
            "search_libraries",
            "Search the local versioned documentation catalogue by library name, ID, or summary. Returns only catalogue matches and does not expose the full environment state.",
            {"query": {"type": "string", "minLength": 1}},
            ["query"],
            {"items": array_of(library_fields, list(library_fields)), "count": {"type": "integer"}},
            ["items", "count"],
            """
            def run(arguments, context):
                import json
                path = context.workspace_root / "entities/documentation_catalog.json"
                catalog = json.loads(path.read_text(encoding="utf-8"))
                query = arguments["query"].strip().lower()
                items = [item for item in catalog["library"] if query in (item["library_id"] + " " + item["name"] + " " + item["summary"]).lower()]
                return {"success": True, "data": {"items": items, "count": len(items)}}
            """,
        ),
        tool(
            "list_library_versions",
            "List the documentation versions available for one exact library ID returned by search_libraries.",
            {"library_id": {"type": "string", "minLength": 1}},
            ["library_id"],
            {"library_id": {"type": "string"}, "versions": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}},
            ["library_id", "versions"],
            """
            def run(arguments, context):
                import json
                path = context.workspace_root / "entities/documentation_catalog.json"
                catalog = json.loads(path.read_text(encoding="utf-8"))
                library_id = arguments["library_id"]
                if not any(item["library_id"] == library_id for item in catalog["library"]):
                    return {"success": False, "error": {"code": "not_found", "path": "$.library_id", "message": "Library ID not found.", "retryable": False}}
                versions = sorted({item["version"] for item in catalog["documentation_section"] if item["library_id"] == library_id})
                return {"success": True, "data": {"library_id": library_id, "versions": versions}}
            """,
        ),
        tool(
            "query_documentation",
            "Search documentation sections for one exact library ID and an optional exact version. Returns focused excerpts rather than the complete catalogue.",
            {
                "library_id": {"type": "string", "minLength": 1},
                "query": {"type": "string", "minLength": 1},
                "version": {"type": "string", "minLength": 1},
            },
            ["library_id", "query"],
            {
                "items": array_of(
                    {"section_id": {"type": "string"}, "title": {"type": "string"}, "version": {"type": "string"}, "excerpt": {"type": "string"}},
                    ["section_id", "title", "version", "excerpt"],
                ),
                "count": {"type": "integer"},
            },
            ["items", "count"],
            """
            def run(arguments, context):
                import json
                catalog_path = context.workspace_root / "entities/documentation_catalog.json"
                index_path = context.workspace_root / "derived/search_index.json"
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                index = json.loads(index_path.read_text(encoding="utf-8"))
                library_id = arguments["library_id"]
                if not any(item["library_id"] == library_id for item in catalog["library"]):
                    return {"success": False, "error": {"code": "not_found", "path": "$.library_id", "message": "Library ID not found.", "retryable": False}}
                words = {word for word in arguments["query"].lower().split() if len(word) >= 4}
                candidate_ids = set().union(*(set(index.get(word, [])) for word in words)) if words else set()
                items = []
                for section in catalog["documentation_section"]:
                    if section["library_id"] != library_id or (arguments.get("version") and section["version"] != arguments["version"]):
                        continue
                    text = (section["title"] + " " + section["content"]).lower()
                    if section["section_id"] in candidate_ids or any(word in text for word in words):
                        items.append({"section_id": section["section_id"], "title": section["title"], "version": section["version"], "excerpt": section["content"][:240]})
                return {"success": True, "data": {"items": items, "count": len(items)}}
            """,
        ),
        tool(
            "get_document_section",
            "Read one documentation section by section ID after locating it with query_documentation.",
            {"section_id": {"type": "string", "minLength": 1}},
            ["section_id"],
            {"section": closed_object(section_fields, list(section_fields))},
            ["section"],
            """
            def run(arguments, context):
                import json
                path = context.workspace_root / "entities/documentation_catalog.json"
                catalog = json.loads(path.read_text(encoding="utf-8"))
                section = next((item for item in catalog["documentation_section"] if item["section_id"] == arguments["section_id"]), None)
                if section is None:
                    return {"success": False, "error": {"code": "not_found", "path": "$.section_id", "message": "Documentation section not found.", "retryable": False}}
                return {"success": True, "data": {"section": section}}
            """,
        ),
    ]

    environment = {
        "schema_version": "1.0",
        "environment_id": "context7_documentation_workspace_001",
        "name": "Context7 版本化开发文档工作区",
        "description": "基于 Context7 MCP 能力种子构建的本地开发文档检索环境，包含原始文档快照、规范化库与章节实体以及词项索引。",
        "resources": [
            resource("mcp_seed", "MCP 种子", "Context7 MCP 的原始描述、能力线索和输入 Schema。", "raw", "file", "raw/mcp_seed.json", "json", False),
            resource("documentation_snapshot", "文档快照", "用于本地检索的版本化开发文档小型原始快照。", "raw", "file", "raw/documentation_snapshot.json", "json", False),
            resource(
                "documentation_catalog", "规范化文档目录", "从文档快照整理出的 library 和 documentation_section 实体。", "entity", "file", "entities/documentation_catalog.json", "json", False,
                source_resources=["documentation_snapshot"],
                entity_schema={
                    "library": {"description": "可检索的软件库及其版本。", "fields": {"library_id": "string", "name": "string", "version": "string", "summary": "string"}},
                    "documentation_section": {"description": "一个版本化文档章节。", "fields": {"section_id": "string", "library_id": "string", "version": "string", "title": "string", "content": "string"}},
                },
            ),
            resource("documentation_search_index", "文档词项索引", "从规范化章节生成的词项到章节 ID 索引。", "derived", "file", "derived/search_index.json", "json", False, source_resources=["documentation_catalog"]),
            resource("research_reports", "调研报告目录", "工具可写入的开发文档调研结果目录。", "output", "directory", "reports/", "directory", True),
        ],
        "rules": [
            {"description": "documentation_section.library_id 必须引用存在的 library.library_id。", "resources": ["documentation_catalog"]},
            {"description": "search_index 中的每个 section_id 必须引用存在的 documentation_section.section_id。", "resources": ["documentation_catalog", "documentation_search_index"]},
        ],
        "tools": tools,
    }
    smoke = {
        "search_libraries": {"query": "Flask"},
        "list_library_versions": {"library_id": "/pallets/flask"},
        "query_documentation": {"library_id": "/pallets/flask", "query": "testing client"},
        "get_document_section": {"section_id": "flask_testing"},
    }
    return environment, smoke


def url_safety_environment(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    workspace = root / "workspace"
    write_json(workspace / "raw" / "mcp_seed.json", seed_record("OjasKord/url-safety-validator-mcp"))
    observations = [
        {"observation_id": "obs_001", "url": "https://merchant.example.com/checkout", "domain": "merchant.example.com", "risk_score": 5, "verdict": "safe"},
        {"observation_id": "obs_002", "url": "http://paypa1.example/login", "domain": "paypa1.example", "risk_score": 72, "verdict": "unsafe"},
        {"observation_id": "obs_003", "url": "http://192.0.2.10/verify-account", "domain": "192.0.2.10", "risk_score": 85, "verdict": "unsafe"},
        {"observation_id": "obs_004", "url": "https://supplier.example.org/catalog", "domain": "supplier.example.org", "risk_score": 8, "verdict": "safe"},
    ]
    write_json(workspace / "raw" / "url_observations.json", observations)
    risk_catalog = {
        "domain": [
            {"domain_id": "domain_merchant", "domain": "merchant.example.com", "trusted": True, "brand": "Merchant Example"},
            {"domain_id": "domain_paypa1", "domain": "paypa1.example", "trusted": False, "brand": ""},
            {"domain_id": "domain_supplier", "domain": "supplier.example.org", "trusted": True, "brand": "Supplier Example"},
        ],
        "url_observation": observations,
    }
    write_json(workspace / "entities" / "risk_catalog.json", risk_catalog)
    trusted = {"trusted_domains": ["merchant.example.com", "supplier.example.org", "paypal.com"]}
    write_json(workspace / "derived" / "trusted_domains.json", trusted)
    (workspace / "assessments").mkdir(parents=True, exist_ok=True)

    verdict_props = {
        "normalized_url": {"type": "string"},
        "domain": {"type": "string"},
        "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["safe", "caution", "unsafe"]},
        "signals": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
    }
    check_code = """
        def run(arguments, context):
            import ipaddress
            import json
            from difflib import SequenceMatcher
            from urllib.parse import urlsplit, urlunsplit
            value = arguments["url"].strip()
            if "://" not in value:
                value = "https://" + value
            parsed = urlsplit(value)
            domain = (parsed.hostname or "").lower()
            if not domain:
                return {"success": False, "error": {"code": "invalid_url", "path": "$.url", "message": "URL has no hostname.", "retryable": False}}
            normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
            catalog = json.loads((context.workspace_root / "entities/risk_catalog.json").read_text(encoding="utf-8"))
            trusted = json.loads((context.workspace_root / "derived/trusted_domains.json").read_text(encoding="utf-8"))["trusted_domains"]
            score = 0
            signals = []
            if parsed.scheme.lower() != "https":
                score += 15
                signals.append("unencrypted_http")
            try:
                ipaddress.ip_address(domain)
                score += 40
                signals.append("ip_literal_hostname")
            except ValueError:
                pass
            if "xn--" in domain:
                score += 25
                signals.append("punycode_hostname")
            suspicious = {"login", "verify", "payment", "wallet", "account", "secure"}
            matched_words = sorted(word for word in suspicious if word in (domain + parsed.path).lower())
            if matched_words:
                score += min(25, 8 * len(matched_words))
                signals.extend("suspicious_keyword:" + word for word in matched_words)
            known = [item for item in catalog["url_observation"] if item["domain"] == domain]
            if known:
                score = max(score, max(item["risk_score"] for item in known))
                signals.append("local_observation_match")
            closest = max((SequenceMatcher(None, domain, item).ratio(), item) for item in trusted)
            if domain not in trusted and closest[0] >= 0.72:
                score += 30
                signals.append("similar_to_trusted_domain:" + closest[1])
            score = min(score, 100)
            verdict = "unsafe" if score >= 50 else "caution" if score >= 21 else "safe"
            return {"success": True, "data": {"normalized_url": normalized, "domain": domain, "risk_score": score, "verdict": verdict, "signals": sorted(set(signals))}}
    """
    tools = [
        tool(
            "normalize_url",
            "Normalize an externally supplied URL without navigating to it. Returns the scheme, hostname, and canonical URL used by later checks.",
            {"url": {"type": "string", "minLength": 1}},
            ["url"],
            {"normalized_url": {"type": "string"}, "domain": {"type": "string"}, "scheme": {"type": "string"}},
            ["normalized_url", "domain", "scheme"],
            """
            def run(arguments, context):
                from urllib.parse import urlsplit, urlunsplit
                value = arguments["url"].strip()
                if "://" not in value:
                    value = "https://" + value
                parsed = urlsplit(value)
                domain = (parsed.hostname or "").lower()
                if not domain:
                    return {"success": False, "error": {"code": "invalid_url", "path": "$.url", "message": "URL has no hostname.", "retryable": False}}
                normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
                return {"success": True, "data": {"normalized_url": normalized, "domain": domain, "scheme": parsed.scheme.lower()}}
            """,
        ),
        tool(
            "get_domain_observations",
            "Return local risk observations for one exact domain. An empty list means no local observation, not that the domain is safe.",
            {"domain": {"type": "string", "minLength": 1}},
            ["domain"],
            {
                "items": array_of(
                    {"observation_id": {"type": "string"}, "url": {"type": "string"}, "risk_score": {"type": "integer"}, "verdict": {"type": "string"}},
                    ["observation_id", "url", "risk_score", "verdict"],
                ),
                "count": {"type": "integer"},
            },
            ["items", "count"],
            """
            def run(arguments, context):
                import json
                path = context.workspace_root / "entities/risk_catalog.json"
                catalog = json.loads(path.read_text(encoding="utf-8"))
                domain = arguments["domain"].strip().lower()
                items = [{key: item[key] for key in ("observation_id", "url", "risk_score", "verdict")} for item in catalog["url_observation"] if item["domain"] == domain]
                return {"success": True, "data": {"items": items, "count": len(items)}}
            """,
        ),
        tool(
            "compare_domain_similarity",
            "Compare a candidate domain with one trusted domain using a deterministic character similarity score. This is one signal and not a final verdict.",
            {"candidate_domain": {"type": "string", "minLength": 1}, "trusted_domain": {"type": "string", "minLength": 1}},
            ["candidate_domain", "trusted_domain"],
            {"similarity": {"type": "number", "minimum": 0, "maximum": 1}, "suspicious_similarity": {"type": "boolean"}},
            ["similarity", "suspicious_similarity"],
            """
            def run(arguments, context):
                from difflib import SequenceMatcher
                candidate = arguments["candidate_domain"].strip().lower()
                trusted = arguments["trusted_domain"].strip().lower()
                score = round(SequenceMatcher(None, candidate, trusted).ratio(), 4)
                suspicious = candidate != trusted and score >= 0.72
                return {"success": True, "data": {"similarity": score, "suspicious_similarity": suspicious}}
            """,
        ),
        tool(
            "check_url",
            "Screen a URL before navigation using local observations, transport, hostname, keyword, IP-literal, punycode, and trusted-domain similarity signals. Returns an explainable local risk verdict.",
            {"url": {"type": "string", "minLength": 1}},
            ["url"],
            verdict_props,
            list(verdict_props),
            check_code,
        ),
        tool(
            "write_url_assessment",
            "Run the same deterministic local URL screening and write the resulting assessment to the writable assessments directory.",
            {"url": {"type": "string", "minLength": 1}, "assessment_name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"}},
            ["url", "assessment_name"],
            {"path": {"type": "string"}, "risk_score": {"type": "integer"}, "verdict": {"type": "string"}},
            ["path", "risk_score", "verdict"],
            """
            def run(arguments, context):
                import json
                from urllib.parse import urlsplit
                value = arguments["url"].strip()
                if "://" not in value:
                    value = "https://" + value
                parsed = urlsplit(value)
                domain = (parsed.hostname or "").lower()
                if not domain:
                    return {"success": False, "error": {"code": "invalid_url", "path": "$.url", "message": "URL has no hostname.", "retryable": False}}
                catalog = json.loads((context.workspace_root / "entities/risk_catalog.json").read_text(encoding="utf-8"))
                known = [item for item in catalog["url_observation"] if item["domain"] == domain]
                score = max((item["risk_score"] for item in known), default=0)
                signals = ["local_observation_match"] if known else []
                if parsed.scheme.lower() != "https":
                    score = max(score, 15)
                    signals.append("unencrypted_http")
                score = min(score, 100)
                verdict = "unsafe" if score >= 50 else "caution" if score >= 21 else "safe"
                relative = "assessments/" + arguments["assessment_name"] + ".json"
                output = context.workspace_root / relative
                output.write_text(json.dumps({"url": value, "domain": domain, "risk_score": score, "verdict": verdict, "signals": signals}, ensure_ascii=False, indent=2), encoding="utf-8")
                return {"success": True, "data": {"path": relative, "risk_score": score, "verdict": verdict}}
            """,
        ),
    ]

    environment = {
        "schema_version": "1.0",
        "environment_id": "url_safety_screening_workspace_001",
        "name": "URL 风险筛查工作区",
        "description": "基于 URL Safety Validator MCP 能力种子构建的本地风险分析环境，包含URL观察记录、可信域名和可解释风险评估输出。",
        "resources": [
            resource("mcp_seed", "MCP 种子", "URL Safety Validator MCP 的原始描述和 check_url 能力线索。", "raw", "file", "raw/mcp_seed.json", "json", False),
            resource("url_observations", "URL 观察快照", "用于环境内确定性复现的原始 URL 风险观察样本。", "raw", "file", "raw/url_observations.json", "json", False),
            resource(
                "risk_catalog", "规范化风险目录", "从 URL 观察快照整理出的 domain 和 url_observation 实体。", "entity", "file", "entities/risk_catalog.json", "json", False,
                source_resources=["url_observations"],
                entity_schema={
                    "domain": {"description": "接受风险分析的域名。", "fields": {"domain_id": "string", "domain": "string", "trusted": "boolean", "brand": "string"}},
                    "url_observation": {"description": "一条 URL 风险观察。", "fields": {"observation_id": "string", "url": "string", "domain": "string", "risk_score": "integer", "verdict": "string"}},
                },
            ),
            resource("trusted_domains", "可信域名集合", "由风险目录和业务基线形成的可信域名比较集合。", "derived", "file", "derived/trusted_domains.json", "json", False, source_resources=["risk_catalog"]),
            resource("assessments", "风险评估输出", "工具生成的 URL 风险评估 JSON 文件目录。", "output", "directory", "assessments/", "directory", True),
        ],
        "rules": [
            {"description": "url_observation.domain 必须对应一条可解析的域名，并与 URL hostname 一致。", "resources": ["risk_catalog"]},
            {"description": "风险分数必须位于 0 到 100，且 unsafe 分数不得低于 50。", "resources": ["risk_catalog", "assessments"]},
        ],
        "tools": tools,
    }
    smoke = {
        "normalize_url": {"url": "paypa1.example/login"},
        "get_domain_observations": {"domain": "paypa1.example"},
        "compare_domain_similarity": {"candidate_domain": "paypa1.com", "trusted_domain": "paypal.com"},
        "check_url": {"url": "http://192.0.2.10/verify-account"},
        "write_url_assessment": {"url": "http://paypa1.example/login", "assessment_name": "paypa1_review"},
    }
    return environment, smoke


def reportflow_environment(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    workspace = root / "workspace"
    write_json(workspace / "raw" / "mcp_seed.json", seed_record("re-port-flow/reportflow-mcp"))
    snapshot = {
        "templates": [
            {"design_id": "design_invoice_standard", "name": "Standard Invoice", "version": 3, "description": "Invoice with seller, customer, date, currency, subtotal, tax, and total."},
            {"design_id": "design_contract_summary", "name": "Contract Summary", "version": 2, "description": "Summary of parties, effective date, scope, term, and governing law."},
        ],
        "parameters": [
            {"parameter_id": "invoice_seller", "design_id": "design_invoice_standard", "name": "seller_name", "value_type": "string", "required": True},
            {"parameter_id": "invoice_customer", "design_id": "design_invoice_standard", "name": "customer_name", "value_type": "string", "required": True},
            {"parameter_id": "invoice_total", "design_id": "design_invoice_standard", "name": "total_minor", "value_type": "integer", "required": True},
            {"parameter_id": "invoice_currency", "design_id": "design_invoice_standard", "name": "currency", "value_type": "string", "required": True},
            {"parameter_id": "contract_parties", "design_id": "design_contract_summary", "name": "parties", "value_type": "string", "required": True},
            {"parameter_id": "contract_scope", "design_id": "design_contract_summary", "name": "scope", "value_type": "string", "required": True},
            {"parameter_id": "contract_law", "design_id": "design_contract_summary", "name": "governing_law", "value_type": "string", "required": False},
        ],
    }
    write_json(workspace / "raw" / "template_snapshot.json", snapshot)
    template_catalog = {
        "template": snapshot["templates"],
        "parameter_definition": snapshot["parameters"],
    }
    write_json(workspace / "entities" / "template_catalog.json", template_catalog)
    request_examples = [
        {"request_id": "request_invoice_001", "design_id": "design_invoice_standard", "file_name": "invoice_acme.html", "status": "ready"},
        {"request_id": "request_contract_001", "design_id": "design_contract_summary", "file_name": "contract_summary.html", "status": "missing_parameters"},
    ]
    write_json(workspace / "raw" / "document_requests.json", request_examples)
    (workspace / "generated").mkdir(parents=True, exist_ok=True)

    template_props = {"design_id": {"type": "string"}, "name": {"type": "string"}, "version": {"type": "integer"}, "description": {"type": "string"}}
    parameter_props = {"name": {"type": "string"}, "value_type": {"type": "string"}, "required": {"type": "boolean"}}
    tools = [
        tool(
            "list_templates",
            "List document templates in the local workspace with stable design IDs and latest versions.",
            {},
            [],
            {"items": array_of(template_props, list(template_props)), "count": {"type": "integer"}},
            ["items", "count"],
            """
            def run(arguments, context):
                import json
                path = context.workspace_root / "entities/template_catalog.json"
                items = json.loads(path.read_text(encoding="utf-8"))["template"]
                return {"success": True, "data": {"items": items, "count": len(items)}}
            """,
        ),
        tool(
            "get_design_parameters",
            "Return the declared parameters for one exact design ID. Call this before validating or generating a document.",
            {"design_id": {"type": "string", "minLength": 1}},
            ["design_id"],
            {"design_id": {"type": "string"}, "parameters": array_of(parameter_props, list(parameter_props))},
            ["design_id", "parameters"],
            """
            def run(arguments, context):
                import json
                path = context.workspace_root / "entities/template_catalog.json"
                catalog = json.loads(path.read_text(encoding="utf-8"))
                design_id = arguments["design_id"]
                if not any(item["design_id"] == design_id for item in catalog["template"]):
                    return {"success": False, "error": {"code": "not_found", "path": "$.design_id", "message": "Template not found.", "retryable": False}}
                parameters = [{key: item[key] for key in ("name", "value_type", "required")} for item in catalog["parameter_definition"] if item["design_id"] == design_id]
                return {"success": True, "data": {"design_id": design_id, "parameters": parameters}}
            """,
        ),
        tool(
            "validate_document_params",
            "Validate supplied parameter names and primitive value types against one design before document generation. Does not invent missing values.",
            {"design_id": {"type": "string", "minLength": 1}, "params": {"type": "object", "additionalProperties": True}},
            ["design_id", "params"],
            {
                "valid": {"type": "boolean"},
                "missing": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "unexpected": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "type_errors": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
            ["valid", "missing", "unexpected", "type_errors"],
            """
            def run(arguments, context):
                import json
                path = context.workspace_root / "entities/template_catalog.json"
                catalog = json.loads(path.read_text(encoding="utf-8"))
                definitions = [item for item in catalog["parameter_definition"] if item["design_id"] == arguments["design_id"]]
                if not definitions:
                    return {"success": False, "error": {"code": "not_found", "path": "$.design_id", "message": "Template or parameter definitions not found.", "retryable": False}}
                params = arguments["params"]
                expected = {item["name"]: item for item in definitions}
                missing = sorted(name for name, item in expected.items() if item["required"] and name not in params)
                unexpected = sorted(set(params) - set(expected))
                python_types = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
                type_errors = sorted(name for name, value in params.items() if name in expected and not isinstance(value, python_types[expected[name]["value_type"]]))
                return {"success": True, "data": {"valid": not (missing or unexpected or type_errors), "missing": missing, "unexpected": unexpected, "type_errors": type_errors}}
            """,
        ),
        tool(
            "generate_document_preview",
            "Validate user-supplied parameters and generate an escaped HTML preview in the writable output directory. Missing values cause a business error and no file is written.",
            {
                "design_id": {"type": "string", "minLength": 1},
                "file_name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+\\.html$"},
                "params": {"type": "object", "additionalProperties": True},
            },
            ["design_id", "file_name", "params"],
            {"path": {"type": "string"}, "media_type": {"type": "string", "const": "text/html"}, "size": {"type": "integer", "minimum": 1}},
            ["path", "media_type", "size"],
            """
            def run(arguments, context):
                import html
                import json
                path = context.workspace_root / "entities/template_catalog.json"
                catalog = json.loads(path.read_text(encoding="utf-8"))
                design_id = arguments["design_id"]
                template = next((item for item in catalog["template"] if item["design_id"] == design_id), None)
                definitions = [item for item in catalog["parameter_definition"] if item["design_id"] == design_id]
                if template is None:
                    return {"success": False, "error": {"code": "not_found", "path": "$.design_id", "message": "Template not found.", "retryable": False}}
                params = arguments["params"]
                missing = sorted(item["name"] for item in definitions if item["required"] and item["name"] not in params)
                if missing:
                    return {"success": False, "error": {"code": "missing_parameters", "path": "$.params", "message": "Missing required parameters: " + ", ".join(missing), "retryable": False}}
                rows = "".join("<tr><th>" + html.escape(str(key)) + "</th><td>" + html.escape(str(value)) + "</td></tr>" for key, value in sorted(params.items()))
                document = "<!doctype html><html><head><meta charset='utf-8'><title>" + html.escape(template["name"]) + "</title></head><body><h1>" + html.escape(template["name"]) + "</h1><table>" + rows + "</table></body></html>"
                relative = "generated/" + arguments["file_name"]
                output = context.workspace_root / relative
                output.write_text(document, encoding="utf-8")
                return {"success": True, "data": {"path": relative, "media_type": "text/html", "size": len(document.encode("utf-8"))}}
            """,
        ),
        tool(
            "list_generated_documents",
            "List HTML document previews currently present in the writable generated directory.",
            {},
            [],
            {"items": array_of({"path": {"type": "string"}, "size": {"type": "integer"}}, ["path", "size"]), "count": {"type": "integer"}},
            ["items", "count"],
            """
            def run(arguments, context):
                root = context.workspace_root / "generated"
                items = [{"path": path.relative_to(context.workspace_root).as_posix(), "size": path.stat().st_size} for path in sorted(root.glob("*.html"))]
                return {"success": True, "data": {"items": items, "count": len(items)}}
            """,
        ),
    ]

    environment = {
        "schema_version": "1.0",
        "environment_id": "reportflow_document_workspace_001",
        "name": "ReportFlow 文档自动化工作区",
        "description": "基于 ReportFlow MCP 能力种子构建的模板驱动文档环境，支持模板发现、参数检查和本地 HTML 预览生成。",
        "resources": [
            resource("mcp_seed", "MCP 种子", "ReportFlow MCP 的原始描述、模板工具线索和输入 Schema。", "raw", "file", "raw/mcp_seed.json", "json", False),
            resource("template_snapshot", "模板原始快照", "模板和参数定义的原始工作区快照。", "raw", "file", "raw/template_snapshot.json", "json", False),
            resource("document_requests", "文档请求样例", "用户提供的文档生成请求状态样例，不包含工具生成结果。", "raw", "file", "raw/document_requests.json", "json", False),
            resource(
                "template_catalog", "规范化模板目录", "从模板快照整理出的 template 和 parameter_definition 实体。", "entity", "file", "entities/template_catalog.json", "json", False,
                source_resources=["template_snapshot"],
                entity_schema={
                    "template": {"description": "可用于文档生成的模板版本。", "fields": {"design_id": "string", "name": "string", "version": "integer", "description": "string"}},
                    "parameter_definition": {"description": "模板要求的一个参数。", "fields": {"parameter_id": "string", "design_id": "string", "name": "string", "value_type": "string", "required": "boolean"}},
                },
            ),
            resource("generated_documents", "生成文档目录", "工具生成的 HTML 文档预览目录。", "output", "directory", "generated/", "directory", True),
        ],
        "rules": [
            {"description": "parameter_definition.design_id 必须引用存在的 template.design_id。", "resources": ["template_catalog"]},
            {"description": "生成文档前必须提供该模板声明的全部 required 参数，缺失时不得写入输出文件。", "resources": ["template_catalog", "generated_documents"]},
            {"description": "工具只能在 generated_documents 资源中创建 HTML 文件，不能修改模板快照和模板目录。", "resources": ["template_snapshot", "template_catalog", "generated_documents"]},
        ],
        "tools": tools,
    }
    valid_params = {"seller_name": "Example Seller Ltd.", "customer_name": "Example Customer Inc.", "total_minor": 125000, "currency": "USD"}
    smoke = {
        "list_templates": {},
        "get_design_parameters": {"design_id": "design_invoice_standard"},
        "validate_document_params": {"design_id": "design_invoice_standard", "params": valid_params},
        "generate_document_preview": {"design_id": "design_invoice_standard", "file_name": "invoice_preview.html", "params": valid_params},
        "list_generated_documents": {},
    }
    return environment, smoke


def schema_validator() -> Draft202012Validator:
    validation_dir = SCHEMA_DIR / "validation"
    environment_schema = read_json(validation_dir / "environment.schema.json")
    tool_schema = read_json(validation_dir / "tool.schema.json")
    complete_schema = read_json(validation_dir / "complete_environment.schema.json")
    store = {
        environment_schema["$id"]: environment_schema,
        tool_schema["$id"]: tool_schema,
        complete_schema["$id"]: complete_schema,
    }
    return Draft202012Validator(
        complete_schema,
        resolver=RefResolver.from_schema(complete_schema, store=store),
    )


def validate_resources(environment: dict[str, Any], root: Path) -> None:
    workspace = root / "workspace"
    resource_ids = {item["resource_id"] for item in environment["resources"]}
    if len(resource_ids) != len(environment["resources"]):
        raise ValueError("resource_id must be unique")
    for item in environment["resources"]:
        unknown = set(item.get("source_resources", [])) - resource_ids
        if unknown:
            raise ValueError(f"unknown source_resources: {sorted(unknown)}")
        path = item["path"]
        if item["storage_type"] == "file_collection":
            if not list(workspace.glob(path)):
                raise ValueError(f"file_collection is empty: {path}")
        elif not (workspace / path).exists():
            raise ValueError(f"resource path does not exist: {path}")
    for rule in environment["rules"]:
        unknown = set(rule["resources"]) - resource_ids
        if unknown:
            raise ValueError(f"rule references unknown resources: {sorted(unknown)}")


def validate_tools(environment: dict[str, Any], root: Path, smoke_calls: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    workspace = root / "workspace"
    reports: list[dict[str, Any]] = []
    names = [item["name"] for item in environment["tools"]]
    if len(names) != len(set(names)):
        raise ValueError("tool names must be unique")
    for item in environment["tools"]:
        namespace: dict[str, Any] = {}
        compiled = compile(item["internal"]["code"], f"<tool:{item['name']}>", "exec")
        exec(compiled, namespace)
        run = namespace.get("run")
        if not callable(run):
            raise ValueError(f"tool has no callable run: {item['name']}")
        with tempfile.TemporaryDirectory(prefix=f"validate-{item['name']}-") as temporary:
            copied = Path(temporary) / "workspace"
            shutil.copytree(workspace, copied)
            arguments = smoke_calls[item["name"]]
            Draft202012Validator(item["inputSchema"]).validate(arguments)
            result = run(arguments, SimpleNamespace(workspace_root=copied))
            Draft202012Validator(item["outputSchema"]).validate(result)
            reports.append({"tool": item["name"], "arguments": arguments, "result": result})
    return reports


def main() -> None:
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True)
    validator = schema_validator()
    builders = (
        ("context7", context7_environment),
        ("url_safety", url_safety_environment),
        ("reportflow", reportflow_environment),
    )
    index: list[dict[str, Any]] = []
    for slug, builder in builders:
        root = OUTPUT_ROOT / slug
        environment, smoke_calls = builder(root)
        validator.validate(environment)
        validate_resources(environment, root)
        reports = validate_tools(environment, root, smoke_calls)
        write_json(root / "environment.json", environment)
        write_json(root / "validation.json", {"schema_valid": True, "resource_valid": True, "tool_smoke_tests": reports})
        index.append({
            "environment_id": environment["environment_id"],
            "name": environment["name"],
            "path": f"{slug}/environment.json",
            "resources": len(environment["resources"]),
            "tools": len(environment["tools"]),
        })
    write_json(
        OUTPUT_ROOT / "index.json",
        {"schema_version": "1.0", "environments": index},
    )
    print(f"generated {len(index)} environments at {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
