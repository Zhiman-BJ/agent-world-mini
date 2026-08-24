from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SMITHERY_SERVERS_API = "https://api.smithery.ai/servers"
DEFAULT_SEED = 20260823
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def _get_json(url: str, api_key: str, retries: int) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "agent-world-mini/1.0",
    }
    for attempt in range(retries + 1):
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Expected a JSON object from {url}")
            return payload
        except HTTPError as error:
            if error.code not in RETRYABLE_HTTP_CODES or attempt >= retries:
                detail = error.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"Smithery request failed ({error.code}) for {url}: {detail}") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            if attempt >= retries:
                raise RuntimeError(f"Smithery request failed for {url}: {error}") from error
        time.sleep(min(8.0, 0.5 * (2**attempt)))
    raise AssertionError("retry loop exited unexpectedly")


def _page_url(page: int, page_size: int, seed: int, remote: bool) -> str:
    return f"{SMITHERY_SERVERS_API}?{urlencode({
        'page': page,
        'pageSize': page_size,
        'remote': str(remote).lower(),
        'seed': seed,
    })}"


def _read_page(
    page: int,
    page_size: int,
    seed: int,
    remote: bool,
    api_key: str,
    retries: int,
) -> dict[str, Any]:
    payload = _get_json(_page_url(page, page_size, seed, remote), api_key, retries)
    servers = payload.get("servers")
    pagination = payload.get("pagination")
    if not isinstance(servers, list) or not isinstance(pagination, dict):
        raise RuntimeError(f"Smithery page {page} has an invalid response shape")
    if int(pagination.get("currentPage", -1)) != page:
        raise RuntimeError(f"Smithery returned page {pagination.get('currentPage')} while page {page} was requested")
    return payload


def _fetch_server_group(
    api_key: str,
    *,
    remote: bool,
    page_size: int = 100,
    workers: int = 8,
    seed: int = DEFAULT_SEED,
    retries: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    first = _read_page(1, page_size, seed, remote, api_key, retries)
    first_pagination = first["pagination"]
    total_pages = int(first_pagination["totalPages"])
    total_count = int(first_pagination["totalCount"])
    pages: dict[int, dict[str, Any]] = {1: first}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_read_page, page, page_size, seed, remote, api_key, retries): page
            for page in range(2, total_pages + 1)
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            page = futures[future]
            payload = future.result()
            pagination = payload["pagination"]
            if int(pagination["totalPages"]) != total_pages or int(pagination["totalCount"]) != total_count:
                raise RuntimeError(
                    "Smithery catalogue changed during pagination; rerun to obtain a consistent snapshot"
                )
            pages[page] = payload
            print(
                f"[smithery] remote={str(remote).lower()} fetched page {completed + 1}/{total_pages}",
                flush=True,
            )

    raw_servers = [server for page in sorted(pages) for server in pages[page]["servers"]]
    if len(raw_servers) != total_count:
        raise RuntimeError(
            f"Smithery reported {total_count} remote={str(remote).lower()} servers "
            f"but deep pagination returned {len(raw_servers)}"
        )
    return raw_servers, {"total_pages": total_pages, "total_count": total_count}


def fetch_all_servers(
    api_key: str,
    *,
    page_size: int = 100,
    workers: int = 8,
    seed: int = DEFAULT_SEED,
    retries: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_servers: list[dict[str, Any]] = []
    groups: dict[str, dict[str, int]] = {}
    for remote in (True, False):
        group_servers, group_pagination = _fetch_server_group(
            api_key,
            remote=remote,
            page_size=page_size,
            workers=workers,
            seed=seed,
            retries=retries,
        )
        raw_servers.extend(group_servers)
        groups[f"remote_{str(remote).lower()}"] = group_pagination

    by_name: dict[str, dict[str, Any]] = {}
    for index, server in enumerate(raw_servers, start=1):
        if not isinstance(server, dict):
            raise RuntimeError(f"Smithery record {index} is not an object")
        name = server.get("qualifiedName")
        description = server.get("description")
        uses = server.get("useCount")
        verified = server.get("verified")
        remote = server.get("remote")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError(f"Smithery record {index} has no qualifiedName")
        if not isinstance(description, str):
            raise RuntimeError(f"Smithery server {name} has no string description")
        if not isinstance(uses, int) or isinstance(uses, bool) or uses < 0:
            raise RuntimeError(f"Smithery server {name} has an invalid useCount: {uses!r}")
        if not isinstance(verified, bool):
            raise RuntimeError(f"Smithery server {name} has an invalid verified value: {verified!r}")
        if not isinstance(remote, bool):
            raise RuntimeError(f"Smithery server {name} has an invalid remote value: {remote!r}")
        if name in by_name:
            raise RuntimeError(f"Smithery deep pagination returned duplicate server: {name}")
        by_name[name] = server

    return list(by_name.values()), {
        "groups": groups,
        "total_pages": sum(group["total_pages"] for group in groups.values()),
        "total_count": sum(group["total_count"] for group in groups.values()),
    }


def build_seed_records(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(server) for server in servers),
        key=lambda server: (
            -int(server["useCount"]),
            str(server["qualifiedName"]).casefold(),
            str(server["qualifiedName"]),
        ),
    )


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    default_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Fetch remote=true and remote=false Smithery servers with stable deep pagination"
    )
    parser.add_argument("--output", type=Path, default=default_root / "smithery_servers.json")
    parser.add_argument("--report", type=Path, default=default_root / "smithery_servers_report.json")
    parser.add_argument("--page-size", type=int, default=100, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--retries", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.retries < 0:
        parser.error("--retries must not be negative")

    workspace_env = default_root.parent / ".env"
    load_dotenv(workspace_env)
    api_key = os.environ.get("SMITHERY_API_KEY", "").strip()
    if not api_key:
        parser.error(f"SMITHERY_API_KEY is not set in the environment or {workspace_env}")

    servers, pagination = fetch_all_servers(
        api_key,
        page_size=args.page_size,
        workers=args.workers,
        seed=args.seed,
        retries=args.retries,
    )
    records = build_seed_records(servers)
    write_json_atomic(args.output, records)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "https://smithery.ai/servers",
        "api": SMITHERY_SERVERS_API,
        "filter": {"remote": [True, False]},
        "stable_pagination_seed": args.seed,
        "page_size": args.page_size,
        "pages": pagination["total_pages"],
        "pagination_groups": pagination["groups"],
        "reported_servers": pagination["total_count"],
        "written_servers": len(records),
        "unique_server_names": len({record["qualifiedName"] for record in records}),
        "verified_true": sum(record["verified"] is True for record in records),
        "verified_false": sum(record["verified"] is False for record in records),
        "remote_true": sum(record["remote"] is True for record in records),
        "remote_false": sum(record["remote"] is False for record in records),
        "record_fields": sorted({key for record in records for key in record}),
        "order": ["useCount descending", "qualifiedName ascending"],
        "output": str(args.output),
        "output_sha256": digest,
    }
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
