from __future__ import annotations

import json
import math
import os
import random
import re
import time
import hashlib
from threading import Lock
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from email.utils import parsedate_to_datetime

from utils.io import write_json
from seed_gen.themes import CURATED_THEME_SEEDS, ThemeSeed, theme_from_catalog


SMITHERY_API = "https://api.smithery.ai/servers"
SEED_GEN_DATA = Path(__file__).resolve().parent / "data"
DEFAULT_SERVER_SNAPSHOT = SEED_GEN_DATA / "smithery_servers.json"
DEFAULT_SEED_OUTPUT = SEED_GEN_DATA / "smithery_1000_v1_0902.json"
SMITHERY_SNAPSHOT_VERSION = "2026-09-02"
MAX_REFERENCE_TOOLS = 100
_REQUEST_LOCK = Lock()
_NEXT_REQUEST_AT = 0.0
REQUEST_INTERVAL = 1.0


def _wait_request_slot() -> None:
    """Space requests across workers, including retries, to avoid request bursts."""
    global _NEXT_REQUEST_AT
    with _REQUEST_LOCK:
        remaining = _NEXT_REQUEST_AT - time.monotonic()
        while remaining > 0:
            time.sleep(min(remaining, 60.0))
            remaining = _NEXT_REQUEST_AT - time.monotonic()
        _NEXT_REQUEST_AT = time.monotonic() + REQUEST_INTERVAL


def _defer_requests(seconds: float) -> None:
    global _NEXT_REQUEST_AT
    with _REQUEST_LOCK:
        _NEXT_REQUEST_AT = max(_NEXT_REQUEST_AT, time.monotonic() + seconds)


def select_reference_tools(
    tools: list[dict[str, object]], max_tools: int = MAX_REFERENCE_TOOLS,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Cap tools using complete tool-level usage counts, otherwise source order.

    Server popularity is not a tool ranking. Partial, invalid, or mixed usage
    fields are deliberately not filled with zeros. Python's stable sort keeps
    source order for ties; retained tool records are never rewritten here.
    """
    if isinstance(max_tools, bool) or not isinstance(max_tools, int) or not 1 <= max_tools <= MAX_REFERENCE_TOOLS:
        raise ValueError(f"max_tools must be an integer from 1 to {MAX_REFERENCE_TOOLS}")
    selected = tools[:]
    strategy = "all"
    ranking_field = None
    if len(tools) > max_tools:
        strategy = "source_order"
        for field in ("useCount", "use_count"):
            values = [tool.get(field) for tool in tools]
            if all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and value >= 0 and (isinstance(value, int) or math.isfinite(value))
                for value in values
            ):
                selected = sorted(tools, key=lambda tool: tool[field], reverse=True)
                strategy, ranking_field = "tool_usage_desc", field
                break
        selected = selected[:max_tools]
    return selected, {
        "limit": max_tools,
        "eligible_count": len(tools),
        "retained_count": len(selected),
        "removed_count": len(tools) - len(selected),
        "strategy": strategy,
        "ranking_field": ranking_field,
    }


def _get_json(url: str) -> dict[str, object]:
    _wait_request_slot()
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


def _read_server_detail(
    server: dict[str, object], attempts: int = 5, retry_delay: float = 1.0,
    audit: dict[str, object] | None = None,
) -> dict[str, object] | None:
    qualified_name = str(server.get("qualifiedName") or "").strip()
    if not qualified_name:
        return None
    if attempts < 1 or retry_delay < 0:
        raise ValueError("attempts must be positive and retry_delay nonnegative")
    events = []
    if audit is not None:
        audit.update({"name": qualified_name, "events": events})
    merged = None
    for attempt in range(attempts):
        delay = min(30.0, retry_delay * 2 ** min(attempt, 10))
        try:
            detail = _get_json(f"{SMITHERY_API}/{quote(qualified_name, safe='')}")
            if not isinstance(detail, dict):
                raise ValueError("detail response is not an object")
            if detail.get("qualifiedName") and detail["qualifiedName"] != qualified_name:
                raise ValueError("detail response names a different server")
            tools = detail.get("tools") or []
            if not isinstance(tools, list):
                tools = []
            valid = [t for t in tools if isinstance(t, dict) and str(t.get("name") or "").strip()
                     and isinstance(t.get("description"), str) and t["description"].strip()]
            merged = dict(server)
            merged.update({key: value for key, value in detail.items() if value not in (None, "", [], {})})
            merged["tools"] = [tool for tool in tools if isinstance(tool, dict) and tool.get("name")]
            events.append({"attempt": attempt + 1, "status": "tools_found" if valid else "invalid_tools" if tools else "empty_tools",
                           "received_tools": len(tools), "valid_tools": len(valid)})
            if valid:
                return merged
        except HTTPError as exc:
            events.append({"attempt": attempt + 1, "status": "http_error", "http_status": exc.code})
            if exc.code in {401, 403, 404, 410}:
                break
            # Respect a server-specified delay. Long cooldowns are deferred to a later run.
            retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
            cooldown = 0.0
            if retry_after:
                try:
                    cooldown = float(retry_after) if retry_after.isdigit() else max(
                        0.0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
            if cooldown:
                _defer_requests(cooldown)
                if cooldown > 60:
                    events[-1]["retry_after_seconds"] = cooldown
                    break
                delay = max(delay, cooldown)
            elif exc.code == 429:
                delay = max(delay, 30.0)
                _defer_requests(delay)
        except (OSError, ValueError) as exc:
            events.append({"attempt": attempt + 1, "status": "request_error", "error_type": type(exc).__name__})
        if attempt + 1 < attempts and delay:
            time.sleep(delay)
    return merged


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
    os.replace(temporary, path)


def retry_empty_smithery_tools(
    output_file: Path = DEFAULT_SEED_OUTPUT, workers: int = 8, attempts: int = 5,
    retry_delay: float = 1.0, max_tools: int = MAX_REFERENCE_TOOLS,
    report_dir: Path = Path("reports/smithery_tools_retry_20260911"),
) -> dict[str, object]:
    """Resume tools-only recovery, preserving populated seeds and failed entries."""
    select_reference_tools([], max_tools)
    if workers < 1 or attempts < 1 or retry_delay < 0:
        raise ValueError("workers/attempts must be positive; retry_delay must be nonnegative")
    original = output_file.read_bytes()
    entries = json.loads(original)
    targets = [(i, s) for i, s in enumerate(entries) if not s["init_ref_tools"]]
    run_dir = report_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "before.json").write_bytes(original)
    expected_hash = hashlib.sha256(original).hexdigest()
    report = {"source": str(output_file), "source_sha256": expected_hash,
              "started_at": datetime.now(timezone.utc).isoformat(), "target_count": len(targets),
              "completed": 0, "recovered": 0, "added_tools": 0, "records": [],
              "attempts": attempts, "workers": workers, "max_tools": max_tools,
              "retry_delay": retry_delay, "request_interval": REQUEST_INTERVAL,
              "backup": str(run_dir / "before.json"), "run_dir": str(run_dir), "status": "running"}

    def fetch(item):
        i, seed = item
        basic = seed["environment"]["basic_info"]
        audit = {"global_id": seed["global_id"], "source_index": basic["index"]}
        detail = _read_server_detail({"qualifiedName": basic["name"],
                                      "description": seed["environment"]["description"]},
                                     attempts, retry_delay, audit)
        return i, detail, audit

    def checkpoint():
        nonlocal expected_hash
        if hashlib.sha256(output_file.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError("Seed file changed during recovery; refusing to overwrite concurrent edits")
        _atomic_json(output_file, entries)
        expected_hash = hashlib.sha256(output_file.read_bytes()).hexdigest()
        report["output_sha256"] = expected_hash
        report["remaining_empty"] = sum(not s["init_ref_tools"] for s in entries)
        _atomic_json(run_dir / "report.json", report)

    _atomic_json(run_dir / "report.json", report)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, item) for item in targets]
        for future in as_completed(futures):
            i, detail, audit = future.result()
            audit["fetched_at"] = datetime.now(timezone.utc).isoformat()
            if detail is not None:
                raw_path = run_dir / "details" / f"{entries[i]['global_id']}.json"
                _atomic_json(raw_path, detail)
                audit["raw_detail"] = str(raw_path)
                projected = _seed_from_detail(detail, entries[i]["environment"]["basic_info"]["index"], max_tools)
                if projected["init_ref_tools"]:
                    entries[i]["init_ref_tools"] = projected["init_ref_tools"]
                    entries[i]["environment"]["nums"] = projected["environment"]["nums"]
                    entries[i]["others"]["tool_selection"] = projected["others"]["tool_selection"]
                    entries[i]["others"]["tools_refresh"] = {
                        "fetched_at": audit["fetched_at"], "source_url": f"{SMITHERY_API}/{quote(audit['name'], safe='')}",
                        "attempts": len(audit["events"]), "raw_detail": str(raw_path)}
                    audit["retained_tools"] = len(projected["init_ref_tools"])
                    report["recovered"] += 1
                    report["added_tools"] += audit["retained_tools"]
            audit["outcome"] = "recovered" if audit.get("retained_tools") else "unresolved"
            report["records"].append(audit)
            report["completed"] += 1
            print(f"[retry-empty] {report['completed']}/{len(targets)} recovered={report['recovered']} {audit['name']} {audit['outcome']}", flush=True)
            if report["completed"] % 10 == 0:
                checkpoint()
    report["status"] = "completed"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    checkpoint()
    return {key: value for key, value in report.items() if key != "records"}


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


def _schema_properties(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not isinstance(value.get("properties"), dict):
        return {}
    return deepcopy(value["properties"])


def _reference_tool(tool: dict[str, object]) -> dict[str, object]:
    """Convert a Smithery MCP tool to the unified seed-tool representation."""
    return {
        "name": str(tool["name"]),
        "type": "function",
        "module": None,
        "description": str(tool["description"]),
        "input": _schema_properties(tool.get("inputSchema")),
        "output": _schema_properties(tool.get("outputSchema")),
    }


def _seed_from_detail(
    detail: dict[str, object], index: int, max_tools: int = MAX_REFERENCE_TOOLS,
) -> dict[str, object]:
    """Project a Smithery detail response into the environment-seed contract."""
    qualified_name = str(detail.get("qualifiedName") or "").strip()
    description = str(detail.get("description") or "").strip()
    if not qualified_name or not description:
        raise ValueError("Smithery detail must contain qualifiedName and description")
    raw_tools = detail.get("tools") or []
    if not isinstance(raw_tools, list):
        raw_tools = []
    source_tools = [
        tool
        for tool in raw_tools
        if isinstance(tool, dict)
        and str(tool.get("name") or "").strip()
        and isinstance(tool.get("description"), str)
        and tool["description"].strip()
    ]
    selected_tools, selection = select_reference_tools(source_tools, max_tools)
    selection["received_count"] = len(raw_tools)
    selection["invalid_count"] = len(raw_tools) - len(source_tools)
    tools = [_reference_tool(tool) for tool in selected_tools]
    metadata: dict[str, object] = {}
    for key, value in detail.items():
        if key in {"qualifiedName", "description", "tools", "iconUrl"}:
            continue
        metadata[_snake_key(key)] = deepcopy(value)
    return {
        "global_id": f"smithery_{_global_id_name(qualified_name)}_{index}",
        "schema_version": "1.1",
        "environment": {
            "basic_info": {
                "source": "smithery",
                "url": [f"https://smithery.ai/servers/{qualified_name}"],
                "name": qualified_name,
                "version": SMITHERY_SNAPSHOT_VERSION,
                "index": index,
            },
            "description": description,
            "domain": {"level1": "general", "level2": None, "level3": None},
            "nums": {
                "class": 0,
                "function": len(tools),
                "class_func": 0,
                "all_func": len(tools),
            },
        },
        "init_ref_tools": tools,
        "init_ref_tasks": [],
        "others": {
            "source_metadata": metadata,
            "tool_selection": selection,
        },
    }


def prepare_smithery_catalog(
    output_file: Path = DEFAULT_SEED_OUTPUT,
    source_file: Path = DEFAULT_SERVER_SNAPSHOT,
    limit: int = 1000,
    workers: int = 8,
    max_tools: int = MAX_REFERENCE_TOOLS,
    attempts: int = 5,
    retry_delay: float = 1.0,
) -> dict[str, object]:
    """Crawl details for the top snapshot entries and write seed-contract JSON.

    This path is deliberately deterministic and does not instantiate or call an
    LLM.  The pre-crawled list controls ordering; only per-server detail pages
    are fetched from Smithery.
    """
    select_reference_tools([], max_tools)  # Reject invalid limits before network requests.
    if attempts < 1 or retry_delay < 0:
        raise ValueError("attempts must be positive and retry_delay nonnegative")
    servers = _load_server_snapshot(source_file, limit)
    details: list[dict[str, object] | None] = [None] * len(servers)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_read_server_detail, server, attempts, retry_delay): index for index, server in enumerate(servers)}
        for completed, future in enumerate(as_completed(futures), start=1):
            details[futures[future]] = future.result()
            print(f"[catalog] fetched detail {completed}/{len(servers)}", flush=True)
    entries: list[dict[str, object]] = []
    fallback_count = 0
    skipped_count = 0
    for rank, detail in enumerate(details, start=1):
        if detail is None:
            # Keep every one of the selected top-ranked environments even when
            # its detail endpoint is unavailable.  The list record still gives
            # us a traceable description and an honest empty tool reference.
            detail = servers[rank - 1]
            fallback_count += 1
        try:
            # Keep the rank in the sorted snapshot as the seed index, even when
            # an individual detail request fails and the output has a gap.
            entries.append(_seed_from_detail(detail, rank, max_tools))
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
        "max_tools_per_environment": max_tools,
        "truncated_environments": sum(item["others"]["tool_selection"]["removed_count"] > 0 for item in entries),
        "removed_tools": sum(item["others"]["tool_selection"]["removed_count"] for item in entries),
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

    parser = argparse.ArgumentParser(description="Crawl Smithery detail pages into environment-seed v1.1 JSON")
    parser.add_argument("--source", type=Path, default=DEFAULT_SERVER_SNAPSHOT, help="pre-crawled Smithery list JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_SEED_OUTPUT, help="environment-seed output JSON")
    parser.add_argument("--limit", type=int, default=1000, help="number of top useCount entries to continue crawling")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--retry-empty", action="store_true", help="only refill empty toolsets in --output")
    parser.add_argument("--attempts", type=int, default=5, help="attempts per empty seed, including empty responses")
    parser.add_argument("--retry-delay", type=float, default=1.0, help="initial exponential retry delay in seconds")
    parser.add_argument("--report-dir", type=Path, default=Path("reports/smithery_tools_retry_20260911"))
    parser.add_argument("--request-interval", type=float, default=1.0, help="minimum seconds between requests across workers")
    parser.add_argument("--max-tools", type=int, default=MAX_REFERENCE_TOOLS, choices=range(1, MAX_REFERENCE_TOOLS + 1),
                        metavar="1..100", help="maximum reference tools per environment (default: 100)")
    args = parser.parse_args()
    if args.limit < 1 or args.workers < 1 or args.attempts < 1 or args.retry_delay < 0 or args.request_interval < 0:
        parser.error("--limit, --workers and --attempts must be positive; --retry-delay must be nonnegative")
    REQUEST_INTERVAL = args.request_interval
    if args.retry_empty:
        result = retry_empty_smithery_tools(args.output, args.workers, args.attempts, args.retry_delay,
                                           args.max_tools, args.report_dir)
    else:
        result = prepare_smithery_catalog(args.output, args.source, args.limit, args.workers, args.max_tools,
                                          args.attempts, args.retry_delay)
    print(json.dumps(result, ensure_ascii=False, indent=2))
