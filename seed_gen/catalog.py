from __future__ import annotations

import json
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from utils.io import write_json
from seed_gen.themes import CURATED_THEME_SEEDS, ThemeSeed, theme_from_catalog


SMITHERY_API = "https://api.smithery.ai/servers"
SEED_GEN_DATA = Path(__file__).resolve().parent / "data"
DEFAULT_SERVER_SNAPSHOT = SEED_GEN_DATA / "smithery_servers.json"
DEFAULT_SEED_OUTPUT = SEED_GEN_DATA / "smithery_1000_v1_0902.json"


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


def _global_id_name(value: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z]+", "_", value).strip("_").lower()
    if not normalized:
        raise ValueError(f"qualifiedName cannot form a global ID: {value!r}")
    return normalized


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
    detail: dict[str, object] | None = None
    for attempt in range(3):
        try:
            detail = _get_json(f"{SMITHERY_API}/{quote(qualified_name, safe='')}")
            break
        except (OSError, ValueError):
            if attempt == 2:
                return None
    tools = detail.get("tools") or []
    if not isinstance(tools, list):
        tools = []
    merged = dict(server)
    merged.update({key: value for key, value in detail.items() if value not in (None, "", [], {})})
    merged["tools"] = [tool for tool in tools if isinstance(tool, dict) and tool.get("name")]
    return merged


def _load_server_snapshot(path: Path, limit: int = 1000) -> list[dict[str, object]]:
    """Load the existing list snapshot; never request the list endpoint again."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("servers") or payload.get("environments")
    if not isinstance(payload, list):
        raise ValueError(f"Smithery snapshot must be a JSON list: {path}")
    servers = [item for item in payload if isinstance(item, dict) and str(item.get("qualifiedName") or "").strip()]
    servers.sort(key=lambda item: (-int(item.get("useCount") or 0), str(item["qualifiedName"]).casefold()))
    return servers[:limit] if limit > 0 else servers


def _snake_key(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _seed_from_detail(
    detail: dict[str, object], index: int, *, organization_status: str = "catalog_detail"
) -> dict[str, object]:
    """Project a Smithery detail response into the environment-seed contract."""
    qualified_name = str(detail.get("qualifiedName") or "").strip()
    description = str(detail.get("description") or "").strip()
    if not qualified_name or not description:
        raise ValueError("Smithery detail must contain qualifiedName and description")
    tools = [
        deepcopy(tool)
        for tool in detail.get("tools", [])
        if isinstance(tool, dict)
        and str(tool.get("name") or "").strip()
        and isinstance(tool.get("description"), str)
        and tool["description"].strip()
    ]
    metadata: dict[str, object] = {}
    for key, value in detail.items():
        if key in {"qualifiedName", "description", "tools", "iconUrl"}:
            continue
        metadata[_snake_key(key)] = deepcopy(value)
    return {
        "global_id": f"smithery_{_global_id_name(qualified_name)}_{index}",
        "schema_version": "1.0",
        "environment": {
            "basic_info": {
                "source": "smithery",
                "url": f"https://smithery.ai/servers/{qualified_name}",
                "name": qualified_name,
                "index": index,
            },
            "description": description,
            "domain": {"level1": "general", "level2": None, "level3": None},
        },
        "init_ref_tools": tools,
        "init_ref_tasks": [],
        "others": {
            "source_metadata": metadata,
            "data_directions": deepcopy(detail.get("dataDirections") or []),
            "organization_status": organization_status,
        },
    }


def prepare_smithery_catalog(
    output_file: Path = DEFAULT_SEED_OUTPUT,
    source_file: Path = DEFAULT_SERVER_SNAPSHOT,
    limit: int = 1000,
    workers: int = 8,
) -> dict[str, object]:
    """Crawl details for the top snapshot entries and write seed-contract JSON.

    This path is deliberately deterministic and does not instantiate or call an
    LLM.  The pre-crawled list controls ordering; only per-server detail pages
    are fetched from Smithery.
    """
    servers = _load_server_snapshot(source_file, limit)
    details: list[dict[str, object] | None] = [None] * len(servers)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_read_server_detail, server): index for index, server in enumerate(servers)}
        for completed, future in enumerate(as_completed(futures), start=1):
            details[futures[future]] = future.result()
            print(f"[catalog] fetched detail {completed}/{len(servers)}", flush=True)
    entries: list[dict[str, object]] = []
    fallback_count = 0
    skipped_count = 0
    for rank, detail in enumerate(details, start=1):
        status = "catalog_detail"
        if detail is None:
            # Keep every one of the selected top-ranked environments even when
            # its detail endpoint is unavailable.  The list record still gives
            # us a traceable description and an honest empty tool reference.
            detail = servers[rank - 1]
            status = "catalog_list_fallback"
            fallback_count += 1
        try:
            # Keep the rank in the sorted snapshot as the seed index, even when
            # an individual detail request fails and the output has a gap.
            entries.append(_seed_from_detail(detail, rank, organization_status=status))
        except (TypeError, ValueError):
            skipped_count += 1
            continue
    write_json(output_file, entries)
    return {
        "source": str(source_file),
        "output": str(output_file),
        "snapshot_candidates": len(servers),
        "prepared": len(entries),
        "reference_tools": sum(len(item["init_ref_tools"]) for item in entries),
        "detail_successes": len(entries) - fallback_count,
        "list_fallbacks": fallback_count,
        "skipped_invalid_records": skipped_count,
    }


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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Crawl Smithery detail pages into environment-seed v1 JSON")
    parser.add_argument("--source", type=Path, default=DEFAULT_SERVER_SNAPSHOT, help="pre-crawled Smithery list JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_SEED_OUTPUT, help="environment-seed output JSON")
    parser.add_argument("--limit", type=int, default=1000, help="number of top useCount entries to continue crawling")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.limit < 1 or args.workers < 1:
        parser.error("--limit and --workers must be positive")
    print(json.dumps(prepare_smithery_catalog(args.output, args.source, args.limit, args.workers), ensure_ascii=False, indent=2))
