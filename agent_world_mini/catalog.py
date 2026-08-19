from __future__ import annotations

import json
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .io_utils import extract_json_object, write_json
from .llm import LLMClient
from .themes import CURATED_THEME_SEEDS, ThemeSeed, theme_from_catalog


SMITHERY_API = "https://api.smithery.ai/servers"


def _get_json(url: str) -> dict[str, object]:
    headers = {"User-Agent": "agent-world-mini/1.0"}
    if os.environ.get("SMITHERY_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['SMITHERY_API_KEY']}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _normal_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def _normal_name(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    ignored = {"mcp", "server", "tool", "tools", "official", "integration"}
    return " ".join(word for word in words if word not in ignored)


def _existing_themes(output_root: Path) -> tuple[set[str], set[str]]:
    urls = {_normal_url(seed.source_url) for seed in CURATED_THEME_SEEDS.values() if seed.source_url}
    names = {_normal_name(seed.seed_label) for seed in CURATED_THEME_SEEDS.values()}
    if not output_root.exists():
        return urls, names
    for path in output_root.glob("*/theme_registry.json"):
        try:
            selected = json.loads(path.read_text(encoding="utf-8"))["selected_theme"]
            if selected.get("source_url"):
                urls.add(_normal_url(str(selected["source_url"])))
            if selected.get("seed_label"):
                names.add(_normal_name(str(selected["seed_label"])))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return urls, names


def _same_name(name: str, existing_names: set[str]) -> bool:
    normalized = _normal_name(name)
    if not normalized:
        return True
    return any(normalized == other or SequenceMatcher(None, normalized, other).ratio() >= 0.9 for other in existing_names if other)


def _smithery_servers(query: str = "") -> list[dict[str, object]]:
    params = {"pageSize": 100, "verified": "true"}
    if query:
        params["q"] = query
    first = _get_json(f"{SMITHERY_API}?{urlencode(params)}")
    servers = [item for item in first.get("servers", []) if isinstance(item, dict)]
    total_pages = int(first.get("pagination", {}).get("totalPages", 1))
    for page in range(2, total_pages + 1):
        page_params = params | {"page": page}
        servers.extend(
            item for item in _get_json(f"{SMITHERY_API}?{urlencode(page_params)}").get("servers", [])
            if isinstance(item, dict)
        )
    return servers


def _read_server_detail(server: dict[str, object]) -> dict[str, object] | None:
    qualified_name = str(server.get("qualifiedName") or "").strip()
    if not qualified_name:
        return None
    try:
        detail = _get_json(f"{SMITHERY_API}/{quote(qualified_name, safe='')}")
    except (OSError, ValueError):
        return None
    tools = detail.get("tools") or []
    if not isinstance(tools, list) or not any(isinstance(tool, dict) and tool.get("name") for tool in tools):
        return None
    merged = dict(server)
    merged.update({key: value for key, value in detail.items() if value not in (None, "", [], {})})
    merged["tools"] = [tool for tool in tools if isinstance(tool, dict) and tool.get("name")]
    return merged


def _organize_environment(item: dict[str, object], llm: LLMClient) -> dict[str, object]:
    if not llm.enabled:
        return item | {"organizationStatus": "raw_catalog_record"}
    tool_clues = [
        {
            "name": str(tool.get("name") or ""),
            "description": str(tool.get("description") or "")[:500],
        }
        for tool in item.get("tools", [])
        if isinstance(tool, dict) and tool.get("name")
    ]
    try:
        organized = extract_json_object(llm.complete_json(
            "Organize this MCP description for a later research agent and return JSON. Its tools are capability clues, not a required final tool list.",
            json.dumps({
                "name": item.get("displayName") or item.get("qualifiedName"),
                "description": item.get("description", ""),
                "tools": tool_clues,
                "return": {
                    "business_description": "brief description of the real service or workflow",
                    "data_directions": ["types of real public data likely to support this environment"],
                },
            }, ensure_ascii=False),
        ))
    except (OSError, RuntimeError, ValueError, TypeError, KeyError):
        return item | {"organizationStatus": "agent_failed"}
    result = dict(item)
    result["description"] = str(organized.get("business_description") or item.get("description") or "")
    result["dataDirections"] = [str(value) for value in organized.get("data_directions", []) if str(value).strip()]
    result["organizationStatus"] = "agent_organized"
    return result


def prepare_smithery_catalog(
    output_file: Path,
    query: str = "",
    limit: int = 0,
    llm: LLMClient | None = None,
) -> dict[str, object]:
    servers: list[dict[str, object]] = []
    seen_themes: set[str] = set()
    for item in _smithery_servers(query):
        theme_key = _normal_name(str(item.get("displayName") or item.get("qualifiedName") or ""))
        if (
            item.get("inactive")
            or item.get("unlisted")
            or len(str(item.get("description") or "")) < 40
            or not theme_key
            or theme_key in seen_themes
        ):
            continue
        seen_themes.add(theme_key)
        servers.append(item)
    if limit > 0:
        servers = servers[:limit]
    with ThreadPoolExecutor(max_workers=8) as pool:
        detailed = [item for item in pool.map(_read_server_detail, servers) if item is not None]
    organizer = llm or LLMClient()
    entries: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for index, entry in enumerate(pool.map(lambda item: _organize_environment(item, organizer), detailed), start=1):
            entries.append(entry)
            print(f"[catalog] organized {index}/{len(detailed)}: {entry.get('qualifiedName', 'unknown')}", flush=True)
    payload = {
        "catalog": "smithery",
        "prepared": len(entries),
        "agent_organized": sum(item.get("organizationStatus") == "agent_organized" for item in entries),
        "environments": entries,
    }
    write_json(output_file, payload)
    return {key: value for key, value in payload.items() if key != "environments"}


def load_prepared_catalog(path: Path) -> list[ThemeSeed]:
    if not path.is_file():
        raise FileNotFoundError(f"Prepared environment catalog not found: {path}. Run --prepare-catalog first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("environments", [])
    if not isinstance(entries, list):
        raise ValueError(f"Prepared environment catalog has no environments list: {path}")
    return [
        theme_from_catalog(item)
        for item in entries
        if isinstance(item, dict) and item.get("organizationStatus") not in {"agent_failed", "raw_catalog_record"}
    ]


def select_prepared_themes(
    path: Path,
    count: int,
    output_root: Path,
    selection_seed: int | None = None,
) -> tuple[list[ThemeSeed], dict[str, object]]:
    environments = load_prepared_catalog(path)
    random.Random(selection_seed).shuffle(environments)
    existing_urls, existing_names = _existing_themes(output_root)
    selected: list[ThemeSeed] = []
    skipped = 0
    for environment in environments:
        if _normal_url(environment.source_url) in existing_urls or _same_name(environment.seed_label, existing_names):
            skipped += 1
            continue
        selected.append(environment)
        existing_urls.add(_normal_url(environment.source_url))
        existing_names.add(_normal_name(environment.seed_label))
        if len(selected) >= count:
            break
    return selected, {
        "catalog": str(path),
        "catalog_candidates": len(environments),
        "selected": len(selected),
        "skipped_existing_or_duplicate": skipped,
    }


def discover_smithery_themes(
    count: int,
    output_root: Path,
    query: str = "",
    selection_seed: int | None = None,
) -> tuple[list[ThemeSeed], dict[str, object]]:
    servers = _smithery_servers(query)

    random.Random(selection_seed).shuffle(servers)
    existing_urls, existing_names = _existing_themes(output_root)
    selected: list[ThemeSeed] = []
    skipped_existing = 0
    skipped_unusable = 0
    eligible: list[dict[str, object]] = []

    for server in servers:
        if not isinstance(server, dict) or server.get("inactive") or server.get("unlisted"):
            skipped_unusable += 1
            continue
        qualified_name = str(server.get("qualifiedName") or "").strip()
        display_name = str(server.get("displayName") or qualified_name).strip()
        description = str(server.get("description") or "").strip()
        source_url = f"https://smithery.ai/servers/{qualified_name}"
        if not qualified_name or len(description) < 40:
            skipped_unusable += 1
            continue
        if _normal_url(source_url) in existing_urls or _same_name(display_name, existing_names):
            skipped_existing += 1
            continue
        eligible.append(server)

    for offset in range(0, len(eligible), 16):
        chunk = eligible[offset:offset + 16]
        with ThreadPoolExecutor(max_workers=8) as pool:
            details = list(pool.map(_read_server_detail, chunk))
        for detail in details:
            if detail is None:
                skipped_unusable += 1
                continue
            seed = theme_from_catalog(detail)
            if _normal_url(seed.source_url) in existing_urls or _same_name(seed.seed_label, existing_names):
                skipped_existing += 1
                continue
            selected.append(seed)
            existing_urls.add(_normal_url(seed.source_url))
            existing_names.add(_normal_name(seed.seed_label))
            if len(selected) >= count:
                break
        if len(selected) >= count:
            break

    report = {
        "catalog": "smithery",
        "catalog_candidates": len(servers),
        "selected": len(selected),
        "skipped_existing_or_duplicate": skipped_existing,
        "skipped_unusable": skipped_unusable,
    }
    return selected, report


def output_slug(seed: ThemeSeed) -> str:
    return re.sub(r"[^a-z0-9]+", "-", seed.theme_id.lower()).strip("-")[:80]
