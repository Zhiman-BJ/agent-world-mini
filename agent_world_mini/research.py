from __future__ import annotations

import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from .io_utils import extract_json_object
from .llm import LLMClient
from .models import Record, ResearchBundle
from .themes import ThemeSeed, resolve_theme


class _ReadableTextParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "svg", "noscript", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data.strip():
            self.parts.append(data.strip())


class WebResearchAgent:
    """Web-first research agent that turns a theme into auditable local state."""

    def __init__(self, llm: LLMClient, max_sources: int = 12, research_calls_per_round: int = 10):
        self.llm = llm
        self.max_sources = max_sources
        self.research_calls_per_round = research_calls_per_round

    def _fetch(self, url: str, limit: int = 60_000) -> tuple[str, str]:
        url = quote(url, safe=":/?&=#%;")
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 agent-world-mini research bot", "Accept-Language": "en-US,en;q=0.9"})
        try:
            with urlopen(request, timeout=25) as response:
                content_type = response.headers.get_content_type()
                raw = response.read(limit).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        except OSError as original_error:
            curl = shutil.which("curl")
            if not curl:
                raise
            try:
                result = subprocess.run(
                    [curl, "-L", "-sS", "--max-time", "25", "--range", f"0-{limit - 1}", "-A", "Mozilla/5.0 agent-world-mini research bot", url],
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as curl_error:
                raise original_error from curl_error
            if result.returncode != 0 or not result.stdout:
                raise original_error
            raw = result.stdout[:limit].decode("utf-8", errors="replace")
            content_type = "application/json" if raw.lstrip().startswith(("{", "[")) else "text/html"
        if content_type == "application/json":
            return content_type, raw
        parser = _ReadableTextParser()
        parser.feed(raw)
        return content_type, re.sub(r"\s+", " ", " ".join(parser.parts))

    def gather(self, theme: str | ThemeSeed, complexify_rounds: int = 2) -> ResearchBundle:
        seed = theme if isinstance(theme, ThemeSeed) else resolve_theme(theme)
        if seed.adapter == "world_bank":
            return self._gather_world_bank(seed)
        if seed.adapter == "github_issues":
            return self._gather_github_issues(seed, complexify_rounds)
        if seed.adapter == "citybikes":
            return self._gather_citybikes(seed)
        if seed.adapter == "open_library":
            return self._gather_open_library(seed)
        if seed.adapter == "openalex":
            return self._gather_openalex(seed)
        if seed.adapter == "github_triage":
            return self._gather_github_triage(seed, complexify_rounds)
        return self._gather_generic(seed, complexify_rounds)

    def _gather_generic(self, seed: ThemeSeed, complexify_rounds: int) -> ResearchBundle:
        theme = seed.seed_label
        seen: set[str] = set()
        failures: list[str] = []
        theme_source: dict[str, str] | None = None
        # Prepared catalogue entries already contain the MCP description and
        # complete tool schemas. Do not revisit the catalogue during a run.
        if seed.source_url and not seed.documented_tools:
            try:
                content_type, text = self._fetch(seed.source_url)
                theme_source = {
                    "name": seed.seed_label,
                    "url": seed.source_url,
                    "content_type": content_type,
                    "role": "theme_source",
                    "retrieved_excerpt": text[:8_000],
                }
                seen.add(seed.source_url)
            except (OSError, UnicodeError) as error:
                failures.append(f"theme source {seed.source_url}: {type(error).__name__}: {error}")
        if not self.llm.enabled:
            raise RuntimeError("Generic environment research requires an enabled Research Agent")

        complexification: list[dict[str, object]] = []
        candidates: list[dict[str, str]] = []
        gaps: list[str] = []
        # Discover a useful starting point first. Later rounds inspect the
        # extracted state and target an actual missing entity or relation.
        rounds = 1
        for round_index in range(rounds):
            round_call_budget = min(self.research_calls_per_round, 6)
            prompt = {
                "environment": {
                    "name": seed.seed_label,
                    "description": seed.source_description,
                    "official_homepage": seed.catalog_metadata.get("homepage", ""),
                    "official_repository": seed.catalog_metadata.get("repository", ""),
                    "documented_tools": list(seed.documented_tools),
                    "data_directions": list(seed.data_directions),
                },
                "already_selected_sources": [item["url"] for item in candidates],
                "remaining_gaps": gaps,
                "round_goal": "Find additional concrete URLs that return domain records; do not repeat overview or documentation pages." if round_index else "Find a diverse first set of real record sources and relationships, not many leaf records of one kind.",
                "return": {
                    "sources": [{"url": "exact public URL", "title": "source title", "reason": "data it contributes"}],
                    "remaining_data_gaps": ["important missing entity or relation"],
                },
            }
            raw, usage = self.llm.research_json(
                "Research real public data for this environment. Search and fetch as needed. Prefer a small connected sample spanning several useful entity types over exhaustive records of one type. Never download model weights, media, archives, binaries, or every child item under one parent. Put official data APIs, datasets, feeds, or small real data files first; when docs reveal an API, return a concrete query URL that yields records. Documentation and Wikipedia may explain the domain but cannot be the environment state. Do not invent records.",
                json.dumps(prompt, ensure_ascii=False),
                max_tool_calls=round_call_budget,
            )
            try:
                parsed = extract_json_object(raw)
            except ValueError:
                parsed = {}
            if isinstance(parsed, list):
                source_items = parsed
                gaps = []
            elif isinstance(parsed, dict):
                source_items = parsed.get("sources", [])
                gaps = [str(value) for value in parsed.get("remaining_data_gaps", []) if str(value).strip()]
            else:
                source_items = []
                gaps = []
            for annotation in usage.get("annotations", []):
                citation = annotation.get("url_citation", {}) if isinstance(annotation, dict) else {}
                if citation.get("url"):
                    source_items.append({
                        "url": citation["url"],
                        "title": citation.get("title", ""),
                        "reason": citation.get("content", "")[:500],
                    })
            new_sources = 0
            for item in source_items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                domain = urlparse(url).netloc.lower()
                if not url.startswith(("http://", "https://")) or not domain or url in seen or "{" in url or "}" in url:
                    continue
                seen.add(url)
                candidates.append({
                    "url": url,
                    "title": str(item.get("title") or domain),
                    "snippet": str(item.get("reason") or ""),
                    "query": f"research round {round_index + 1}",
                })
                new_sources += 1
            complexification.append({
                "round": round_index + 1,
                "new_source_candidates": new_sources,
                "remaining_data_gaps": gaps,
                "server_tool_use": usage.get("server_tool_use", {}),
            })
            print(
                f"[research] round {round_index + 1}: {new_sources} new sources, "
                f"{len(gaps)} remaining data gaps",
                flush=True,
            )
            if round_index > 0 and new_sources == 0:
                break

        official_domains = {
            urlparse(str(seed.catalog_metadata.get(key) or "")).netloc.lower()
            for key in ("homepage", "repository")
            if seed.catalog_metadata.get(key)
        }
        if official_domains:
            candidates.sort(key=lambda item: urlparse(item["url"]).netloc.lower() not in official_domains)

        def fetch_candidate(candidate: dict[str, str]) -> tuple[dict[str, str] | None, str | None]:
            try:
                content_type, text = self._fetch(candidate["url"], limit=500_000)
                if len(text) < 200:
                    return None, None
                return {
                    "name": candidate["title"] or urlparse(candidate["url"]).netloc,
                    "url": candidate["url"],
                    "content_type": content_type,
                    "role": "data_source",
                    "retrieved_excerpt": text if content_type == "application/json" else text[:8_000],
                    "query": candidate.get("query", theme),
                }, None
            except (OSError, UnicodeError) as error:
                return None, f"fetch {candidate['url']}: {type(error).__name__}: {error}"

        sources: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for source, failure in pool.map(fetch_candidate, candidates[:self.max_sources]):
                if source is not None:
                    sources.append(source)
                if failure:
                    failures.append(failure)
        print(f"[research] fetched {len(sources)}/{min(len(candidates), self.max_sources)} selected sources", flush=True)
        if not sources:
            detail = "; ".join(failures[:3]) or "search returned no readable candidates"
            raise RuntimeError(f"Research agent could not retrieve readable sources for this theme: {detail}")

        expansion_candidates: list[dict[str, str]] = []
        try:
            expansion = extract_json_object(self.llm.complete_json(
                "Turn the discovered API documentation into concrete public read-only GET URLs that return real domain records. Fill required query parameters with representative values. Return no URL when authentication or a state-changing request is required.",
                json.dumps({
                    "environment": theme,
                    "source_material": [
                        {"url": source["url"], "content": source["retrieved_excerpt"][:5_000]}
                        for source in sources[:6]
                    ],
                    "return": {"data_urls": [{"url": "concrete GET URL", "title": "records returned"}]},
                }, ensure_ascii=False),
            ))
            for item in expansion.get("data_urls", []) if isinstance(expansion, dict) else []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url.startswith(("http://", "https://")) or not urlparse(url).netloc or url in seen:
                    continue
                seen.add(url)
                expansion_candidates.append({
                    "url": url,
                    "title": str(item.get("title") or urlparse(url).netloc),
                    "query": "api data expansion",
                })
        except (RuntimeError, ValueError, KeyError, TypeError):
            expansion_candidates = []

        expanded_sources: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for source, failure in pool.map(fetch_candidate, expansion_candidates[:self.max_sources]):
                if source is not None:
                    expanded_sources.append(source)
                if failure:
                    failures.append(failure)
        if expanded_sources:
            sources = (expanded_sources + sources)[:self.max_sources]
        complexification.append({
            "round": "api_data_expansion",
            "data_urls_proposed": len(expansion_candidates),
            "data_sources_fetched": len(expanded_sources),
        })
        print(f"[research] API expansion fetched {len(expanded_sources)}/{len(expansion_candidates)} data sources", flush=True)

        records, extraction = self._extract_sources(theme, sources)
        complexification.append({
            "round": "initial_extraction",
            "sources_used": len(sources),
            "records_created": len(records),
            "entity_types": sorted({record.entity_type for record in records}),
            "relations_created": len(self._relation_pairs(records)),
            "structured_sources": extraction["structured_sources"],
        })

        remaining_gaps: list[str] = []
        stagnant_rounds = 0
        for round_index in range(min(max(0, complexify_rounds), 4)):
            before_records = len(records)
            before_types = {record.entity_type for record in records}
            before_relations = self._relation_pairs(records)
            summary = self._state_summary(records)
            try:
                raw, usage = self.llm.research_json(
                    "Continue researching this environment on the web. Find a small number of concrete public GET URLs that improve entity diversity, relationship coverage, or reachable depth in the current local data. Prefer connecting currently isolated IDs and broadening coverage across several parent records. Do not collect more same-type leaves under an already covered parent. Never download weights, media, archives, binaries, or exhaustive file lists. Do not return overview pages, documentation-only URLs, or invented records.",
                    json.dumps({
                        "environment": {
                            "name": seed.seed_label,
                            "description": seed.source_description,
                            "documented_capabilities": [
                                {
                                    "name": str(tool.get("name") or ""),
                                    "description": str(tool.get("description") or "")[:240],
                                }
                                for tool in seed.documented_tools
                                if isinstance(tool, dict)
                            ],
                            "data_directions": list(seed.data_directions),
                        },
                        "current_local_data": summary,
                        "already_used_urls": [source["url"] for source in sources],
                        "discovered_official_material": [
                            {"url": source["url"], "content": source.get("retrieved_excerpt", "")[:3_000]}
                            for source in sources[:8]
                        ],
                        "remaining_gaps": remaining_gaps,
                        "return": {
                            "data_urls": [{
                                "url": "exact public GET URL returning JSON records",
                                "title": "short title",
                                "reason": "new entity or relation contributed",
                                "entity_type_hint": "optional singular entity type",
                                "records_path_hint": "optional JSON list path",
                                "id_field_hint": "optional stable ID field",
                                "link_from": {
                                    "entity_type": "existing entity type when the URL is scoped by one current ID",
                                    "entity_id": "exact current ID embedded in or used to build the URL",
                                },
                            }],
                            "remaining_data_gaps": ["important missing entity or relation"],
                        },
                    }, ensure_ascii=False),
                    max_tool_calls=min(self.research_calls_per_round, 5),
                )
                parsed = extract_json_object(raw)
            except (RuntimeError, ValueError, KeyError, TypeError):
                parsed, usage = {}, {}

            proposed = parsed.get("data_urls", []) if isinstance(parsed, dict) else []
            remaining_gaps = [
                str(value) for value in parsed.get("remaining_data_gaps", [])
                if str(value).strip()
            ] if isinstance(parsed, dict) else []
            hints: dict[str, dict[str, Any]] = {}
            candidates_for_round: list[dict[str, str]] = []
            for item in proposed[:4] if isinstance(proposed, list) else []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url.startswith(("http://", "https://")) or url in seen or "{" in url or "}" in url:
                    continue
                seen.add(url)
                hints[url] = item
                candidates_for_round.append({
                    "url": url,
                    "title": str(item.get("title") or urlparse(url).netloc),
                    "query": f"state-guided expansion round {round_index + 1}",
                })

            new_sources: list[dict[str, str]] = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                for source, failure in pool.map(fetch_candidate, candidates_for_round):
                    if source is not None:
                        new_sources.append(source)
                    if failure:
                        failures.append(failure)
            new_records, round_extraction = self._extract_sources(theme, new_sources, records, hints)
            records = self._merge_records(records, new_records)
            link_records = self._link_records(records, new_records, hints)
            records = self._merge_records(records, link_records)
            sources.extend(new_sources)

            after_types = {record.entity_type for record in records}
            after_relations = self._relation_pairs(records)
            after_summary = self._state_summary(records)
            coverage_gains = self._coverage_gains(summary, after_summary)
            structural_progress = bool(
                after_types - before_types
                or after_relations - before_relations
                or len(coverage_gains) >= 2
                or after_summary["topology"]["max_relation_hops"] > summary["topology"]["max_relation_hops"]
            )
            event = {
                "round": round_index + 1,
                "goal": "expand extracted state",
                "urls_proposed": len(candidates_for_round),
                "data_sources_fetched": len(new_sources),
                "records_added": len(records) - before_records,
                "entity_types_added": sorted(after_types - before_types),
                "relations_added": sorted(after_relations - before_relations),
                "connected_entity_types_improved": coverage_gains,
                "structural_progress": structural_progress,
                "structured_sources": round_extraction["structured_sources"],
                "remaining_data_gaps": remaining_gaps,
                "server_tool_use": usage.get("server_tool_use", {}),
            }
            complexification.append(event)
            print(
                f"[research] state expansion {round_index + 1}: "
                f"{event['records_added']} records, {len(event['entity_types_added'])} entity types, "
                f"{len(event['relations_added'])} relations, structural progress={structural_progress}",
                flush=True,
            )
            stagnant_rounds = 0 if structural_progress else stagnant_rounds + 1
            if stagnant_rounds >= 2:
                break

        # Do not retain fetched page excerpts as state; they are research evidence
        # only. The record-level source URL remains in the output manifest.
        all_sources = ([theme_source] if theme_source else []) + sources
        public_sources = [{key: value for key, value in source.items() if key != "retrieved_excerpt"} for source in all_sources]
        return self._bundle_from_records(
            seed,
            "generic_web_research",
            public_sources,
            records,
            complexification + [{
                "round": "extraction",
                "sources_used": len(sources),
                "records_created": len(records),
                "entity_types": sorted({record.entity_type for record in records}),
                "relations_created": len(self._relation_pairs(records)),
                "retrieval_failures": failures,
            }],
        )

    def _extract_sources(
        self,
        theme: str,
        sources: list[dict[str, str]],
        existing_records: list[Record] | None = None,
        hints: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[list[Record], dict[str, Any]]:
        json_sources = [source for source in sources if source.get("content_type") == "application/json"]
        structured = self._extract_json_records(theme, json_sources, existing_records or [], hints or {})
        return structured, {
            "structured_sources": len({record.source_url for record in structured}),
            "text_sources": 0,
        }

    def _extract_json_records(
        self,
        theme: str,
        sources: list[dict[str, str]],
        existing_records: list[Record],
        hints: dict[str, dict[str, Any]],
    ) -> list[Record]:
        collections: dict[tuple[str, str], list[dict[str, Any]]] = {}
        inventories: list[dict[str, Any]] = []
        for source in sources:
            try:
                payload = json.loads(source["retrieved_excerpt"])
            except (json.JSONDecodeError, TypeError):
                continue
            for path, rows in self._json_collections(payload).items():
                if not rows:
                    continue
                collections[(source["url"], path)] = rows
                inventories.append({
                    "url": source["url"],
                    "path": path,
                    "row_count": len(rows),
                    "keys": sorted({str(key) for row in rows[:8] for key in row})[:40],
                    "sample": rows[0],
                    "hint": hints.get(source["url"], {}),
                })
        if not inventories:
            return []
        try:
            mapped = extract_json_object(self.llm.complete_json(
                "Identify the real domain-record arrays in these public JSON responses. Return only collections containing concrete entities, not wrappers, facets, error messages, or API schemas. Choose a stable ID field already present in each row. Keep entity type names short, singular, and domain-specific.",
                json.dumps({
                    "theme": theme,
                    "existing_entity_types": sorted({record.entity_type for record in existing_records}),
                    "collections": inventories,
                    "return": {"mappings": [{
                        "url": "exact supplied URL",
                        "path": "exact supplied path",
                        "entity_type": "domain entity type",
                        "id_field": "stable row field",
                    }]},
                }, ensure_ascii=False),
            ))
            mappings = mapped.get("mappings", []) if isinstance(mapped, dict) else []
        except (RuntimeError, ValueError, KeyError, TypeError):
            mappings = []

        selected = {
            (str(mapping.get("url") or ""), str(mapping.get("path") or ""))
            for mapping in mappings if isinstance(mapping, dict)
        }
        # Root arrays with an ordinary public ID are unambiguous enough to
        # preserve without asking the model to repeat every collection.
        for inventory in inventories:
            url, path = inventory["url"], inventory["path"]
            if path != "$" or (url, path) in selected:
                continue
            rows = collections[(url, path)]
            keys = {str(key) for row in rows[:8] for key in row}
            id_field = next((field for field in ("id", "entity_id", "slug", "code", "name") if field in keys), "")
            segment = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
            if not id_field or segment in {"search", "query", "api", "json"}:
                continue
            entity_type = segment[:-3] + "y" if segment.endswith("ies") else segment[:-1] if segment.endswith("s") else segment
            mappings.append({"url": url, "path": path, "entity_type": entity_type, "id_field": id_field})
            selected.add((url, path))

        records: list[Record] = []
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            url = str(mapping.get("url") or "")
            path = str(mapping.get("path") or "")
            rows = collections.get((url, path))
            entity_type = self._normal_entity_type(str(mapping.get("entity_type") or ""))
            id_field = str(mapping.get("id_field") or "")
            if not rows or not entity_type or not id_field:
                continue
            if id_field == "_id" and any("id" in row for row in rows[:8]):
                id_field = "id"
            parent = hints.get(url, {}).get("link_from", {})
            scoped_parent_type = self._normal_entity_type(str(parent.get("entity_type") or "")) if isinstance(parent, dict) else ""
            scoped_parent_id = str(parent.get("entity_id") or "") if isinstance(parent, dict) else ""
            row_limit = 20 if scoped_parent_type and scoped_parent_id else 250
            for row in rows[:row_limit]:
                entity_id = self._nested_value(row, id_field)
                attributes = self._scalar_attributes(row)
                if entity_id in (None, "") or not attributes:
                    continue
                local_id = str(entity_id)
                if scoped_parent_type and scoped_parent_id and id_field.lower() in {"path", "rfilename", "filename"}:
                    attributes.setdefault("local_id", local_id)
                    entity_id = f"{scoped_parent_type}:{scoped_parent_id}:{local_id}"
                records.append(Record(entity_type, str(entity_id), attributes, url))
        return self._merge_records([], records)

    @staticmethod
    def _json_collections(payload: Any) -> dict[str, list[dict[str, Any]]]:
        found: dict[str, list[dict[str, Any]]] = {}

        def visit(value: Any, path: str) -> None:
            if isinstance(value, list):
                dict_rows = [item for item in value if isinstance(item, dict)]
                if dict_rows:
                    found.setdefault(path or "$", []).extend(dict_rows)
                for item in dict_rows:
                    visit(item, f"{path}[]" if path else "$[]")
            elif isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}" if path and path != "$" else (f"$.{key}" if path == "$" else str(key))
                    visit(child, child_path)

        visit(payload, "$")
        return found

    @staticmethod
    def _nested_value(row: dict[str, Any], field: str) -> Any:
        value: Any = row
        for part in field.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value

    @staticmethod
    def _scalar_attributes(row: dict[str, Any]) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                attributes[str(key)] = value
            elif isinstance(value, list) and all(isinstance(item, (str, int, float, bool, type(None))) for item in value):
                attributes[str(key)] = value
            elif isinstance(value, dict):
                for child_key, child in value.items():
                    if isinstance(child, (str, int, float, bool, type(None))):
                        attributes[f"{key}_{child_key}"] = child
        return attributes

    @staticmethod
    def _normal_entity_type(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")

    @staticmethod
    def _merge_records(existing: list[Record], added: list[Record]) -> list[Record]:
        merged: dict[tuple[str, str], Record] = {
            (record.entity_type, record.entity_id): record for record in existing
        }
        for record in added:
            key = (record.entity_type, record.entity_id)
            if key in merged:
                current = merged[key]
                current.attributes = current.attributes | record.attributes
            else:
                merged[key] = record
        return list(merged.values())

    def _link_records(
        self,
        all_records: list[Record],
        new_records: list[Record],
        hints: dict[str, dict[str, Any]],
    ) -> list[Record]:
        existing = {(record.entity_type, record.entity_id) for record in all_records}
        links: list[Record] = []
        for record in new_records:
            hint = hints.get(record.source_url, {})
            parent = hint.get("link_from", {}) if isinstance(hint, dict) else {}
            if not isinstance(parent, dict):
                continue
            parent_type = self._normal_entity_type(str(parent.get("entity_type") or ""))
            parent_id = str(parent.get("entity_id") or "")
            if not parent_type or not parent_id or (parent_type, parent_id) not in existing or parent_type == record.entity_type:
                continue
            link_type = f"{parent_type}_{record.entity_type}_link"
            links.append(Record(
                link_type,
                f"{parent_id}|{record.entity_id}",
                {f"{parent_type}_id": parent_id, f"{record.entity_type}_id": record.entity_id},
                record.source_url,
            ))
        return links

    @staticmethod
    def _relation_pairs(records: list[Record]) -> set[tuple[str, str]]:
        ids: dict[str, set[str]] = {}
        for record in records:
            ids.setdefault(record.entity_type, set()).add(record.entity_id)
        relations: set[tuple[str, str]] = set()
        for record in records:
            for field, value in record.attributes.items():
                if not field.endswith("_id") or value is None:
                    continue
                for target_type, target_ids in ids.items():
                    if str(value) in target_ids:
                        relations.add((f"{record.entity_type}.{field}", f"{target_type}.entity_id"))
        return relations

    def _state_summary(self, records: list[Record]) -> dict[str, Any]:
        by_type: dict[str, list[Record]] = {}
        for record in records:
            by_type.setdefault(record.entity_type, []).append(record)
        topology = self._topology_summary(records)
        return {
            "entities": [
                {
                    "entity_type": entity_type,
                    "record_count": len(rows),
                    "connected_record_count": topology["connected_ids_by_type"].get(entity_type, 0),
                    "unconnected_sample_ids": topology["unconnected_sample_ids_by_type"].get(entity_type, []),
                    "fields": sorted({field for row in rows for field in row.attributes}),
                    "sample_ids": [row.entity_id for row in rows[:3]],
                }
                for entity_type, rows in sorted(by_type.items())
            ],
            "relations": [
                {"from": source, "to": target}
                for source, target in sorted(self._relation_pairs(records))
            ],
            "relation_coverage": self._relation_coverage(records),
            "topology": topology,
        }

    @staticmethod
    def _relation_coverage(records: list[Record]) -> list[dict[str, Any]]:
        ids: dict[str, set[str]] = {}
        by_type: dict[str, list[Record]] = {}
        for record in records:
            ids.setdefault(record.entity_type, set()).add(record.entity_id)
            by_type.setdefault(record.entity_type, []).append(record)
        coverage: list[dict[str, Any]] = []
        for source_type, rows in sorted(by_type.items()):
            fields = sorted({field for row in rows for field in row.attributes if field.endswith("_id")})
            for field in fields:
                values = {str(row.attributes[field]) for row in rows if row.attributes.get(field) not in (None, "")}
                for target_type, target_ids in sorted(ids.items()):
                    matched = values & target_ids
                    if not matched:
                        continue
                    linked_rows = sum(str(row.attributes.get(field)) in target_ids for row in rows)
                    coverage.append({
                        "from": f"{source_type}.{field}",
                        "to": f"{target_type}.entity_id",
                        "linked_source_records": linked_rows,
                        "source_records": len(rows),
                        "distinct_target_ids_covered": len(matched),
                        "target_records": len(target_ids),
                        "uncovered_target_sample_ids": sorted(target_ids - matched)[:5],
                    })
        return coverage

    @staticmethod
    def _topology_summary(records: list[Record]) -> dict[str, Any]:
        nodes = {(record.entity_type, record.entity_id) for record in records if not record.entity_type.endswith("_link")}
        ids: dict[str, set[str]] = {}
        for entity_type, entity_id in nodes:
            ids.setdefault(entity_type, set()).add(entity_id)
        adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {node: set() for node in nodes}

        for record in records:
            linked_nodes: list[tuple[str, str]] = []
            for field, value in record.attributes.items():
                if not field.endswith("_id") or value in (None, ""):
                    continue
                for target_type, target_ids in ids.items():
                    if str(value) in target_ids:
                        linked_nodes.append((target_type, str(value)))
            if record.entity_type.endswith("_link"):
                for left_index, left in enumerate(linked_nodes):
                    for right in linked_nodes[left_index + 1:]:
                        if left != right:
                            adjacency[left].add(right)
                            adjacency[right].add(left)
            else:
                source = (record.entity_type, record.entity_id)
                for target in linked_nodes:
                    if source != target:
                        adjacency[source].add(target)
                        adjacency[target].add(source)

        connected = {node for node, neighbors in adjacency.items() if neighbors}
        components: list[set[tuple[str, str]]] = []
        unseen = set(connected)
        while unseen:
            root = unseen.pop()
            component = {root}
            frontier = [root]
            while frontier:
                current = frontier.pop()
                for neighbor in adjacency[current]:
                    if neighbor not in component:
                        component.add(neighbor)
                        unseen.discard(neighbor)
                        frontier.append(neighbor)
            components.append(component)

        max_hops = 0
        for component in components:
            for start in component:
                distances = {start: 0}
                frontier = [start]
                for current in frontier:
                    for neighbor in adjacency[current]:
                        if neighbor not in distances:
                            distances[neighbor] = distances[current] + 1
                            frontier.append(neighbor)
                max_hops = max(max_hops, max(distances.values(), default=0))

        connected_by_type: dict[str, int] = {}
        unconnected_by_type: dict[str, list[str]] = {}
        for entity_type, entity_ids in sorted(ids.items()):
            connected_ids = {entity_id for kind, entity_id in connected if kind == entity_type}
            connected_by_type[entity_type] = len(connected_ids)
            unconnected_by_type[entity_type] = sorted(entity_ids - connected_ids)[:5]
        return {
            "connected_components": len(components),
            "largest_component_records": max((len(component) for component in components), default=0),
            "max_relation_hops": max_hops,
            "connected_ids_by_type": connected_by_type,
            "unconnected_sample_ids_by_type": unconnected_by_type,
        }

    @staticmethod
    def _coverage_gains(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
        old = before.get("topology", {}).get("connected_ids_by_type", {})
        new = after.get("topology", {}).get("connected_ids_by_type", {})
        return sorted(entity_type for entity_type, count in new.items() if count > old.get(entity_type, 0))

    def _gather_world_bank(self, seed: ThemeSeed) -> ResearchBundle:
        country_codes = "USA;CHN;IND;BRA;ZAF;DEU;JPN;MEX;IDN;NGA;FRA;CAN"
        country_url = f"https://api.worldbank.org/v2/country/{country_codes}?format=json&per_page=100"
        country_payload = self._fetch_json(country_url)
        countries = country_payload[1] if isinstance(country_payload, list) and len(country_payload) > 1 else []
        records: list[Record] = []
        for item in countries:
            country_id = str(item.get("id", ""))
            if not country_id:
                continue
            attributes = {
                "name": item.get("name"),
                "region": item.get("region", {}).get("value"),
                "income_level": item.get("incomeLevel", {}).get("value"),
                "capital_city": item.get("capitalCity"),
                "longitude": self._number_or_none(item.get("longitude")),
                "latitude": self._number_or_none(item.get("latitude")),
            }
            records.append(Record("country", country_id, {key: value for key, value in attributes.items() if value not in (None, "")}, country_url))

        indicator_urls = [
            ("population", f"https://api.worldbank.org/v2/country/{country_codes}/indicator/SP.POP.TOTL?format=json&date=2023&per_page=100"),
            ("gdp_per_capita_usd", f"https://api.worldbank.org/v2/country/{country_codes}/indicator/NY.GDP.PCAP.CD?format=json&date=2023&per_page=100"),
        ]
        sources = [{"name": "World Bank country metadata", "url": country_url, "content_type": "application/json"}]
        for indicator_name, indicator_url in indicator_urls:
            payload = self._fetch_json(indicator_url)
            rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
            sources.append({"name": f"World Bank {indicator_name}", "url": indicator_url, "content_type": "application/json"})
            for item in rows:
                country_id = str(item.get("countryiso3code", ""))
                value = item.get("value")
                if not country_id or not isinstance(value, (int, float)):
                    continue
                records.append(Record(
                    "indicator_observation",
                    f"{country_id.lower()}_{indicator_name}_{item.get('date')}",
                    {"country_id": country_id, "indicator": indicator_name, "year": int(item["date"]), "value": value},
                    indicator_url,
                ))
        return self._bundle_from_records(seed, "world_bank_open_data", sources, records, [
            {"round": 0, "added_entity_type": "country", "records_created": sum(record.entity_type == "country" for record in records)},
            {"round": 1, "added_entity_type": "indicator_observation", "relation": "indicator_observation.country_id -> country.entity_id", "records_created": sum(record.entity_type == "indicator_observation" for record in records)},
        ])

    def _gather_github_issues(self, seed: ThemeSeed, complexify_rounds: int) -> ResearchBundle:
        return self._gather_github_workspace(seed, complexify_rounds, "github_public_api")

    def _gather_citybikes(self, seed: ThemeSeed) -> ResearchBundle:
        network_id = "bixi-montreal"
        source_url = f"https://api.citybik.es/v2/networks/{network_id}"
        payload = self._fetch_json(source_url)
        network = payload.get("network", {}) if isinstance(payload, dict) else {}
        location = network.get("location", {}) if isinstance(network.get("location"), dict) else {}
        records = [Record(
            "bike_network",
            network_id,
            {
                "name": network.get("name"),
                "city": location.get("city"),
                "country": location.get("country"),
                "company": network.get("company", []),
            },
            source_url,
        )]
        for station in network.get("stations", []):
            station_id = str(station.get("id", ""))
            if not station_id:
                continue
            attributes = {
                "network_id": network_id,
                "name": station.get("name"),
                "latitude": station.get("latitude"),
                "longitude": station.get("longitude"),
                "free_bikes": station.get("free_bikes"),
                "empty_slots": station.get("empty_slots"),
                "timestamp": station.get("timestamp"),
            }
            records.append(Record("bike_station", station_id, {key: value for key, value in attributes.items() if value is not None}, source_url))
        return self._bundle_from_records(seed, "citybikes_public_api", [{
            "name": "CityBikes BIXI Montreal network snapshot",
            "url": source_url,
            "content_type": "application/json",
        }], records, [
            {"round": 0, "added_entity_type": "bike_network", "records_created": 1},
            {"round": 1, "added_entity_type": "bike_station", "relation": "bike_station.network_id -> bike_network.entity_id", "records_created": max(0, len(records) - 1)},
        ])

    def _gather_open_library(self, seed: ThemeSeed) -> ResearchBundle:
        subject_key = "science"
        subject_url = f"https://openlibrary.org/subjects/{subject_key}.json?limit=24"
        payload = self._fetch_json(subject_url)
        works = payload.get("works", []) if isinstance(payload, dict) else []
        records: list[Record] = [Record(
            "library_subject", subject_key,
            {"name": payload.get("name", subject_key), "work_count": payload.get("work_count", len(works))},
            subject_url,
        )]
        author_keys: dict[str, str] = {}
        for item in works:
            work_key = str(item.get("key", "")).strip("/")
            authors = item.get("authors", []) if isinstance(item.get("authors"), list) else []
            primary = authors[0] if authors and isinstance(authors[0], dict) else {}
            author_key = str(primary.get("key", "")).strip("/")
            if not work_key or not author_key:
                continue
            author_keys[author_key] = str(primary.get("name", author_key))
            records.append(Record(
                "library_work", work_key,
                {
                    "title": item.get("title"), "edition_count": item.get("edition_count", 0),
                    "first_publish_year": item.get("first_publish_year"), "subject_id": subject_key,
                    "primary_author_id": author_key,
                }, subject_url,
            ))
        # The subject response already contains stable author ids and names.
        # Keep that source-grounded relation in the compact run; optional
        # per-author profile enrichment belongs to a separately budgeted round.
        source_rows = [{"name": "Open Library subject catalogue", "url": subject_url, "content_type": "application/json"}]
        for author_key, fallback_name in author_keys.items():
            records.append(Record("library_author", author_key, {"name": fallback_name}, subject_url))
        return self._bundle_from_records(seed, "open_library_api", source_rows, records, [
            {"round": 0, "data_gap": "subject and works", "added_entity_type": "library_subject", "records_created": 1},
            {"round": 1, "data_gap": "work to author relation", "added_entity_type": "library_work", "relation": "library_work.subject_id -> library_subject.entity_id; library_work.primary_author_id -> library_author.entity_id", "records_created": sum(record.entity_type == "library_work" for record in records)},
            {"round": 2, "data_gap": "author details", "added_entity_type": "library_author", "records_created": sum(record.entity_type == "library_author" for record in records)},
        ])

    def _gather_openalex(self, seed: ThemeSeed) -> ResearchBundle:
        query = "climate change"
        source_url = "https://api.openalex.org/works?search=climate%20change&per-page=24"
        payload = self._fetch_json(source_url)
        results = payload.get("results", []) if isinstance(payload, dict) else []
        topic_id = "climate_change"
        records: list[Record] = [Record("research_topic", topic_id, {"name": query, "result_count": payload.get("meta", {}).get("count", len(results))}, source_url)]
        authors: dict[str, dict[str, object]] = {}
        for item in results:
            work_id = str(item.get("id", "")).rsplit("/", 1)[-1]
            authorships = item.get("authorships", []) if isinstance(item.get("authorships"), list) else []
            primary = authorships[0].get("author", {}) if authorships and isinstance(authorships[0], dict) else {}
            author_id = str(primary.get("id", "")).rsplit("/", 1)[-1]
            if not work_id or not author_id:
                continue
            authors[author_id] = {
                "name": primary.get("display_name"),
                "orcid": primary.get("orcid"),
                "works_count": primary.get("works_count"),
                "cited_by_count": primary.get("cited_by_count"),
            }
            records.append(Record("scholarly_work", work_id, {
                "title": item.get("title"), "cited_by_count": item.get("cited_by_count", 0),
                "publication_year": item.get("publication_year"), "work_type": item.get("type"),
                "topic_id": topic_id, "primary_author_id": author_id, "doi": item.get("doi"),
            }, source_url))
        for author_id, attributes in authors.items():
            records.append(Record("research_author", author_id, {key: value for key, value in attributes.items() if value is not None}, source_url))
        return self._bundle_from_records(seed, "openalex_api", [{"name": "OpenAlex climate change work query", "url": source_url, "content_type": "application/json"}], records, [
            {"round": 0, "data_gap": "research topic", "added_entity_type": "research_topic", "records_created": 1},
            {"round": 1, "data_gap": "topic to work relation", "added_entity_type": "scholarly_work", "relation": "scholarly_work.topic_id -> research_topic.entity_id", "records_created": sum(record.entity_type == "scholarly_work" for record in records)},
            {"round": 2, "data_gap": "work to author relation", "added_entity_type": "research_author", "relation": "scholarly_work.primary_author_id -> research_author.entity_id", "records_created": sum(record.entity_type == "research_author" for record in records)},
        ])

    def _gather_github_triage(self, seed: ThemeSeed, complexify_rounds: int) -> ResearchBundle:
        return self._gather_github_workspace(seed, complexify_rounds, "github_public_api_triage")

    def _gather_github_workspace(self, seed: ThemeSeed, complexify_rounds: int, adapter: str) -> ResearchBundle:
        """Mine a multi-repository triage workspace through real API relations."""
        repository_ids = ["microsoft/vscode", "python/cpython", "pytorch/pytorch"]
        rounds = max(1, min(len(repository_ids), complexify_rounds))
        records: list[Record] = []
        sources: list[dict[str, str]] = []
        complexification: list[dict[str, object]] = []
        contributors: dict[str, dict[str, object]] = {}
        for round_index, repository_id in enumerate(repository_ids[:rounds]):
            repo_url = f"https://api.github.com/repos/{repository_id}"
            issues_url = f"https://api.github.com/repos/{repository_id}/issues?state=all&per_page=30"
            labels_url = f"https://api.github.com/repos/{repository_id}/labels?per_page=100"
            repo = self._fetch_json(repo_url)
            issues = self._fetch_json(issues_url)
            labels = self._fetch_json(labels_url)
            records.append(Record("github_repository", repository_id, {
                "name": repo.get("name"), "full_name": repo.get("full_name"), "description": repo.get("description"),
                "language": repo.get("language"), "stargazers_count": repo.get("stargazers_count"), "open_issues_count": repo.get("open_issues_count"),
            }, repo_url))
            known_labels: set[str] = set()
            for label in labels if isinstance(labels, list) else []:
                name = str(label.get("name", ""))
                if not name:
                    continue
                known_labels.add(name)
                records.append(Record("github_label", f"{repository_id}:{name}", {
                    "name": name, "color": label.get("color"), "description": label.get("description"), "repository_id": repository_id,
                }, labels_url))
            issue_count, link_count = 0, 0
            for item in issues if isinstance(issues, list) else []:
                if "pull_request" in item:
                    continue
                user = item.get("user", {}) if isinstance(item.get("user"), dict) else {}
                login = str(user.get("login", ""))
                if not login:
                    continue
                contributors[login] = {"login": login, "type": user.get("type"), "site_admin": user.get("site_admin", False), "profile_url": user.get("html_url")}
                issue_id = f"{repository_id}#{item['number']}"
                records.append(Record("github_issue", issue_id, {
                    "number": item["number"], "title": item.get("title"), "state": item.get("state"), "comments": item.get("comments", 0),
                    "created_at": item.get("created_at"), "repository_id": repository_id, "author_id": login,
                }, issues_url))
                issue_count += 1
                for label in item.get("labels", []) if isinstance(item.get("labels"), list) else []:
                    label_name = str(label.get("name", "")) if isinstance(label, dict) else ""
                    if not label_name:
                        continue
                    label_id = f"{repository_id}:{label_name}"
                    if label_name not in known_labels:
                        records.append(Record("github_label", label_id, {"name": label_name, "repository_id": repository_id}, issues_url))
                        known_labels.add(label_name)
                    records.append(Record("github_issue_label", f"{issue_id}|{label_id}", {"issue_id": issue_id, "label_id": label_id}, issues_url))
                    link_count += 1
            sources.extend([
                {"name": f"GitHub repository metadata: {repository_id}", "url": repo_url, "content_type": "application/json"},
                {"name": f"GitHub issues: {repository_id}", "url": issues_url, "content_type": "application/json"},
                {"name": f"GitHub labels: {repository_id}", "url": labels_url, "content_type": "application/json"},
            ])
            complexification.append({"round": round_index, "data_gap": "repository, issue, label, and author relations", "repository": repository_id, "github_issues_added": issue_count, "issue_label_links_added": link_count})
        for login, attributes in contributors.items():
            records.append(Record("github_contributor", login, attributes, "https://api.github.com/"))
        complexification.append({"round": "contributors", "data_gap": "issue to contributor relation", "github_contributors_added": len(contributors)})
        return self._bundle_from_records(seed, adapter, sources, records, complexification)

    def _fetch_json(self, url: str) -> object:
        content_type, payload = self._fetch(url, limit=500_000)
        if content_type != "application/json":
            raise RuntimeError(f"Expected JSON from {url}, received {content_type}")
        return json.loads(payload)

    @staticmethod
    def _number_or_none(value: object) -> int | float | None:
        if value in (None, ""):
            return None
        try:
            number = float(str(value))
            return int(number) if number.is_integer() else number
        except ValueError:
            return None

    def _bundle_from_records(self, seed: ThemeSeed, adapter: str, sources: list[dict[str, str]], records: list[Record], complexification: list[dict[str, object]]) -> ResearchBundle:
        entity_fields: dict[str, set[str]] = {}
        entity_ids: dict[str, set[str]] = {}
        for record in records:
            entity_fields.setdefault(record.entity_type, set()).update(record.attributes)
            entity_ids.setdefault(record.entity_type, set()).add(record.entity_id)
        relations: list[dict[str, str]] = []
        for record in records:
            for field, value in record.attributes.items():
                if not field.endswith("_id") or value is None:
                    continue
                for target_type, ids in entity_ids.items():
                    if str(value) in ids:
                        relations.append({"from": f"{record.entity_type}.{field}", "to": f"{target_type}.entity_id"})
        state_contract = {
            "state_classes": {"source_records": "immutable_source", "overlay_records": "local_overlay"},
            "entities": [{"entity_type": entity_type, "fields": sorted(fields), "record_count": len(entity_ids[entity_type])} for entity_type, fields in sorted(entity_fields.items())],
            "relations": sorted({(item["from"], item["to"]) for item in relations}),
            "invariants": ["entity ids are unique within an entity type", "source records are immutable during a rollout", "local overlay is reset before each rollout"],
        }
        return ResearchBundle(
            theme=seed.seed_label,
            adapter=adapter,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            sources=sources,
            records=records,
            derived_datasets={"operational_entities": [record.attributes | {"entity_id": record.entity_id, "entity_type": record.entity_type} for record in records]},
            theme_metadata=seed.to_dict(),
            complexification=complexification,
            state_contract=state_contract,
        )

    def _extract(self, theme: str, sources: list[dict[str, str]]) -> tuple[list[Record], list[dict[str, object]]]:
        allowed_urls = {source["url"] for source in sources}
        if self.llm.enabled:
            prompt = {
                "theme": theme,
                "sources": [{"url": item["url"], "title": item["name"], "content": item["retrieved_excerpt"]} for item in sources],
                "instructions": [
                    "Extract concrete operational entities and relations supported by the sources. Use the amount and structure the evidence naturally supports.",
                    "An entity can be a product, listing, location, organization, dataset row, policy, event, transaction, or other concrete domain record.",
                    "Prefer actual domain records. Do not turn documentation pages, API methods, schemas, or repositories into the main database unless the environment itself is a documentation service.",
                    "Each attributes object must consist only of facts stated in its cited source. Use scalar values or short scalar arrays.",
                    "Every source_url must exactly equal one supplied URL. Do not merge facts from different sources into one entity.",
                    "Return JSON {entities:[{entity_type,entity_id,attributes,source_url}]}. entity_id must be a concise stable slug.",
                ],
            }
            try:
                raw_entities = extract_json_object(self.llm.complete_json(
                    "You are a rigorous web research agent. Extract grounded structured data only; never invent values.",
                    json.dumps(prompt, ensure_ascii=False),
                ))["entities"]
                records = []
                for item in raw_entities:
                    if item["source_url"] not in allowed_urls or not isinstance(item["attributes"], dict):
                        continue
                    attributes = {str(key): value for key, value in item["attributes"].items() if isinstance(value, (str, int, float, bool, type(None), list))}
                    if item.get("entity_type") and item.get("entity_id") and attributes:
                        records.append(Record(str(item["entity_type"]), str(item["entity_id"]), attributes, item["source_url"]))
                if records:
                    return records, [record.attributes | {"entity_id": record.entity_id, "entity_type": record.entity_type} for record in records]
            except (RuntimeError, ValueError, KeyError, TypeError):
                pass

        # Transparent non-LLM fallback: source pages themselves are usable
        # operational objects for discovery and inspection, never fabricated data.
        records = [Record(
            entity_type="research_source",
            entity_id=f"source_{index:02d}",
            attributes={
                "title": source["name"],
                "url": source["url"],
                "content_type": source["content_type"],
                "source_domain": urlparse(source["url"]).netloc,
                "search_rank": index,
                "query_context": source.get("query", theme),
            },
            source_url=source["url"],
        ) for index, source in enumerate(sources, start=1)]
        return records, [record.attributes | {"entity_id": record.entity_id, "entity_type": record.entity_type} for record in records]
