from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ThemeSeed:
    theme_id: str
    seed_label: str
    source_type: str
    source_url: str
    license_or_access_note: str
    coarse_route_label: str
    adapter: str = "generic_web"
    candidate_entities: tuple[str, ...] = ()
    candidate_operations: tuple[str, ...] = ()
    source_description: str = ""
    documented_tools: tuple[dict[str, Any], ...] = ()
    data_directions: tuple[str, ...] = ()
    catalog_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


THEME_SOURCE_FILE = Path(__file__).with_name("theme_sources.json")


def _load_theme_sources() -> tuple[dict[str, ThemeSeed], list[dict[str, str]]]:
    payload = json.loads(THEME_SOURCE_FILE.read_text(encoding="utf-8"))
    themes = {}
    for item in payload["themes"]:
        item = dict(item)
        item["candidate_entities"] = tuple(item.get("candidate_entities", ()))
        item["candidate_operations"] = tuple(item.get("candidate_operations", ()))
        item["documented_tools"] = tuple(item.get("documented_tools", ()))
        item["data_directions"] = tuple(item.get("data_directions", ()))
        seed = ThemeSeed(**item)
        themes[seed.theme_id] = seed
    return themes, payload.get("catalogs", [])


CURATED_THEME_SEEDS, THEME_SOURCE_CATALOGS = _load_theme_sources()


def resolve_theme(theme: str | None, theme_id: str | None = None, source_url: str | None = None) -> ThemeSeed:
    if theme_id:
        try:
            return CURATED_THEME_SEEDS[theme_id]
        except KeyError as error:
            raise ValueError(f"Unknown theme_id: {theme_id}") from error
    if source_url:
        parsed = urlparse(source_url)
        label = theme or parsed.path.strip("/").replace("/", " ").replace("-", " ") or parsed.netloc
        source_slug = "-".join(part for part in (parsed.netloc + parsed.path).lower().replace(".", "-").split("/") if part)
        return ThemeSeed(
            theme_id="source-" + source_slug[:64],
            seed_label=label,
            source_type="mcp_or_tool_documentation",
            source_url=source_url,
            license_or_access_note="Research agent must record the licences of retrieved data sources.",
            coarse_route_label="unclassified",
            adapter="generic_web",
        )
    if not theme:
        raise ValueError("A theme or theme_id is required")
    return ThemeSeed(
        theme_id="custom-" + "-".join(theme.lower().split())[:64],
        seed_label=theme,
        source_type="custom_seed",
        source_url="",
        license_or_access_note="Research agent must record the licences of retrieved sources.",
        coarse_route_label="unclassified",
        adapter="generic_web",
    )


def theme_from_catalog(item: dict[str, object]) -> ThemeSeed:
    qualified_name = str(item["qualifiedName"])
    documented_tools = tuple(tool for tool in item.get("tools", []) if isinstance(tool, dict))
    return ThemeSeed(
        theme_id="smithery-" + qualified_name.lower().replace("/", "-")[:64],
        seed_label=str(item.get("displayName") or qualified_name),
        source_type="smithery_mcp",
        source_url=f"https://smithery.ai/servers/{qualified_name}",
        license_or_access_note="Use the MCP page as theme evidence; record licences for all retrieved data sources.",
        coarse_route_label="unclassified",
        adapter="generic_web",
        candidate_operations=tuple(
            str(tool.get("name")) for tool in documented_tools if tool.get("name")
        ) or tuple(str(name) for name in item.get("toolNames", [])),
        source_description=str(item.get("description") or ""),
        documented_tools=documented_tools,
        data_directions=tuple(str(value) for value in item.get("dataDirections", []) if value),
        catalog_metadata={
            key: item[key]
            for key in ("qualifiedName", "homepage", "repository", "useCount", "verified")
            if item.get(key) not in (None, "")
        },
    )
