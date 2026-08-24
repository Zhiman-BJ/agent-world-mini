from __future__ import annotations

import base64
import json
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from agent_world_mini.schemas.models import Record, ResearchBundle, ToolSpec


class RuntimeContext:
    """Small API exposed to environment-specific Python handlers."""

    def __init__(self, runtime: "LocalToolRuntime"):
        self.runtime = runtime

    def rows_for(self, entity_type: str) -> list[dict[str, Any]]:
        return deepcopy(self.runtime.rows_for(entity_type))

    def get(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        return self.runtime.get_row(entity_type, entity_id)

    def create(self, entity_type: str, values: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.create_row(entity_type, values)

    def update(self, entity_type: str, entity_id: str, values: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.update_row(entity_type, entity_id, values)

    def delete(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        return self.runtime.delete_row(entity_type, entity_id)

    def copy_resource(self, resource_id: str, destination: str | None = None) -> dict[str, Any]:
        return self.runtime.copy_resource(resource_id, destination)

    def read_file(self, path: str) -> Any:
        return self.runtime.read_file(path)

    def write_file(self, path: str, content: Any) -> dict[str, Any]:
        return self.runtime.write_file(path, content)

    def delete_file(self, path: str) -> dict[str, Any]:
        return self.runtime.delete_file(path)


class LocalToolRuntime:
    """Shared resettable execution engine for compiled environment contracts.

    Source records are immutable.  Optional overlay records are local to one
    rollout and are deliberately reset between solver attempts.
    """

    def __init__(
        self,
        records: list[Record] | ResearchBundle,
        tools: list[ToolSpec],
        overlay_seed: list[dict[str, Any]] | None = None,
        resources: list[dict[str, Any]] | None = None,
    ):
        if isinstance(records, ResearchBundle):
            bundle = records
            source_records = bundle.records
            overlay_seed = bundle.overlay_seed
            resources = bundle.resources
        else:
            source_records = records
        self.source_records = deepcopy(source_records)
        self.resource_seed = deepcopy(resources or [])
        self.base_rows = [record.attributes | {
            "entity_id": record.entity_id,
            "entity_type": record.entity_type,
            "source_url": record.source_url,
        } for record in source_records]
        self.overlay_seed = [self._normalize_seed_row(row) for row in overlay_seed or []]
        self.resources = {
            str(resource.get("resource_id") or resource.get("id")): deepcopy(resource)
            for resource in resources or []
            if resource.get("resource_id") or resource.get("id")
        }
        self.tools = {tool.name: tool for tool in tools}
        self._python_handlers: dict[str, Any] = {}
        self.workspace = Path(tempfile.mkdtemp(prefix="agent-world-runtime-"))
        self.overlay_rows: list[dict[str, Any]] = []
        self.reset()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LocalToolRuntime":
        return cls(
            [Record(**item) for item in payload["records"]],
            [ToolSpec(**item) for item in payload["tools"]],
            overlay_seed=[dict(item) for item in payload.get("overlay_seed", [])],
            resources=[dict(item) for item in payload.get("resources", [])],
        )

    def fork(self) -> "LocalToolRuntime":
        return LocalToolRuntime(
            self.source_records,
            list(self.tools.values()),
            overlay_seed=self.overlay_seed,
            resources=self.resource_seed,
        )

    def close(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def __del__(self) -> None:
        workspace = getattr(self, "workspace", None)
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)

    @staticmethod
    def _normalize_seed_row(row: dict[str, Any]) -> dict[str, Any]:
        if isinstance(row.get("attributes"), dict):
            return deepcopy(row["attributes"]) | {
                "entity_id": str(row["entity_id"]),
                "entity_type": str(row["entity_type"]),
                "source_url": str(row.get("source_url") or "local://overlay-seed"),
            }
        normalized = deepcopy(row)
        if not normalized.get("entity_type") or not normalized.get("entity_id"):
            raise ValueError("overlay seed rows require entity_type and entity_id")
        normalized["entity_type"] = str(normalized["entity_type"])
        normalized["entity_id"] = str(normalized["entity_id"])
        normalized.setdefault("source_url", "local://overlay-seed")
        return normalized

    def reset(self) -> None:
        self.rows = deepcopy(self.base_rows) + deepcopy(self.overlay_seed)
        self.overlay_rows = deepcopy(self.overlay_seed)
        self.events: list[dict[str, Any]] = []
        for child in self.workspace.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        (self.workspace / "resources").mkdir()
        (self.workspace / "files").mkdir()

    def snapshot(self) -> dict[str, Any]:
        files = []
        for path in sorted((self.workspace / "files").rglob("*")):
            if path.is_file():
                item: dict[str, Any] = {"path": path.relative_to(self.workspace).as_posix(), "size": path.stat().st_size}
                try:
                    text = path.read_text(encoding="utf-8")
                    item["content"] = json.loads(text) if path.suffix.lower() == ".json" else text
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
                files.append(item)
        return {
            "base_record_count": len(self.base_rows),
            "records": deepcopy(self.rows),
            "overlay_records": deepcopy(self.overlay_rows),
            "files": files,
            "events": deepcopy(self.events),
        }

    @staticmethod
    def outcome(initial: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
        def rows(snapshot: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
            return {
                (str(row["entity_type"]), str(row["entity_id"])): row
                for row in snapshot.get("records", [])
            }

        before, after = rows(initial), rows(final)
        created = [deepcopy(after[key]) for key in sorted(after.keys() - before.keys())]
        deleted = [{"entity_type": key[0], "entity_id": key[1]} for key in sorted(before.keys() - after.keys())]
        updated = []
        for key in sorted(before.keys() & after.keys()):
            changes = {
                name: deepcopy(value)
                for name, value in after[key].items()
                if name not in {"entity_id", "entity_type", "source_url"} and before[key].get(name) != value
            }
            if changes:
                updated.append({"entity_type": key[0], "entity_id": key[1], "fields": changes})
        before_files = {item["path"]: item for item in initial.get("files", [])}
        after_files = {item["path"]: item for item in final.get("files", [])}
        written = [
            deepcopy(after_files[path])
            for path in sorted(after_files)
            if before_files.get(path) != after_files[path]
        ]
        removed = sorted(set(before_files) - set(after_files))
        deleted_keys = {(item["entity_type"], item["entity_id"]) for item in deleted}
        removed_paths = set(removed)
        initial_event_count = len(initial.get("events", []))
        transient_events = []
        for event in final.get("events", [])[initial_event_count:]:
            if event.get("operation") == "delete" and (
                str(event.get("entity_type")), str(event.get("entity_id"))
            ) not in deleted_keys:
                transient_events.append(deepcopy(event))
            elif event.get("operation") == "delete_file" and str(event.get("path")) not in removed_paths:
                transient_events.append(deepcopy(event))
        outcome = {
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "files_written": written,
            "files_deleted": removed,
            "events": transient_events,
        }
        return {name: value for name, value in outcome.items() if value}

    def check_outcome(self, expected: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.snapshot()
        rows = {
            (str(row["entity_type"]), str(row["entity_id"])): row
            for row in snapshot.get("records", [])
        }
        failures = []
        for item in expected.get("created", []):
            key = (str(item["entity_type"]), str(item["entity_id"]))
            actual = rows.get(key)
            if actual is None or any(actual.get(name) != value for name, value in item.items() if name != "source_url"):
                failures.append({"created": key})
        for item in expected.get("updated", []):
            key = (str(item["entity_type"]), str(item["entity_id"]))
            actual = rows.get(key)
            if actual is None or any(actual.get(name) != value for name, value in item.get("fields", {}).items()):
                failures.append({"updated": key})
        for item in expected.get("deleted", []):
            key = (str(item["entity_type"]), str(item["entity_id"]))
            if key in rows:
                failures.append({"deleted": key})
        files = {item["path"]: item for item in snapshot.get("files", [])}
        for item in expected.get("files_written", []):
            actual = files.get(str(item["path"]))
            if actual is None or ("content" in item and actual.get("content") != item["content"]):
                failures.append({"file": item["path"]})
        for path in expected.get("files_deleted", []):
            if str(path) in files:
                failures.append({"file_deleted": path})
        for event in expected.get("events", []):
            if event not in self.events:
                failures.append({"event": event})
        return {"passed": not failures, "failures": failures}

    def rows_for(self, entity_type: str) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["entity_type"] == entity_type]

    def get_row(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        row = next((item for item in self.rows_for(entity_type) if item["entity_id"] == str(entity_id)), None)
        if row is None:
            raise ValueError(f"Unknown {entity_type} id: {entity_id}")
        return deepcopy(row)

    def create_row(self, entity_type: str, values: dict[str, Any]) -> dict[str, Any]:
        row = deepcopy(values)
        entity_id = str(row.pop("entity_id", "") or self._next_id(entity_type))
        if any(item["entity_id"] == entity_id for item in self.rows_for(entity_type)):
            raise ValueError(f"Duplicate {entity_type} id: {entity_id}")
        created = row | {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "source_url": str(row.pop("source_url", "local://runtime")),
        }
        self.rows.append(created)
        self.overlay_rows.append(created)
        return deepcopy(created)

    def update_row(self, entity_type: str, entity_id: str, values: dict[str, Any]) -> dict[str, Any]:
        row = next((item for item in self.rows_for(entity_type) if item["entity_id"] == str(entity_id)), None)
        if row is None:
            raise ValueError(f"Unknown {entity_type} id: {entity_id}")
        for key, value in values.items():
            if key not in {"entity_id", "entity_type", "source_url"}:
                row[key] = deepcopy(value)
        self._remember_overlay(row)
        return deepcopy(row)

    def delete_row(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        row = next((item for item in self.rows_for(entity_type) if item["entity_id"] == str(entity_id)), None)
        if row is None:
            raise ValueError(f"Unknown {entity_type} id: {entity_id}")
        self.rows.remove(row)
        self.overlay_rows = [item for item in self.overlay_rows if not (
            item["entity_type"] == entity_type and item["entity_id"] == str(entity_id)
        )]
        result = {"deleted": True, "entity_id": str(entity_id), "entity_type": entity_type}
        self.events.append({"operation": "delete", "entity_type": entity_type, "entity_id": str(entity_id)})
        return result

    def _remember_overlay(self, row: dict[str, Any]) -> None:
        self.overlay_rows = [item for item in self.overlay_rows if not (
            item["entity_type"] == row["entity_type"] and item["entity_id"] == row["entity_id"]
        )]
        self.overlay_rows.append(deepcopy(row))

    def _next_id(self, entity_type: str) -> str:
        prefix = entity_type.replace(" ", "_")
        used = {row["entity_id"] for row in self.rows_for(entity_type)}
        index = 1
        while f"{prefix}-{index}" in used:
            index += 1
        return f"{prefix}-{index}"

    def _workspace_path(self, value: str, default_root: str = "files") -> Path:
        relative = Path(value.replace("\\", "/"))
        if relative.is_absolute():
            raise ValueError("workspace paths must be relative")
        if relative.parts and relative.parts[0] in {"files", "resources"}:
            path = self.workspace / relative
        else:
            path = self.workspace / default_root / relative
        path = path.resolve()
        if self.workspace.resolve() not in path.parents:
            raise ValueError("workspace path leaves the environment")
        return path

    @staticmethod
    def _resource_bytes(resource: dict[str, Any]) -> bytes:
        if resource.get("content_base64") is not None:
            return base64.b64decode(str(resource["content_base64"]))
        content = resource.get("content", "")
        if isinstance(content, str):
            return content.encode("utf-8")
        return json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")

    def copy_resource(self, resource_id: str, destination: str | None = None) -> dict[str, Any]:
        resource = self.resources.get(str(resource_id))
        if resource is None:
            raise ValueError(f"Unknown resource: {resource_id}")
        name = destination or str(resource.get("name") or f"{resource_id}.json")
        target = self._workspace_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._resource_bytes(resource))
        return {
            "path": target.relative_to(self.workspace).as_posix(),
            "resource_id": str(resource_id),
            "size": target.stat().st_size,
            "source_url": str(resource.get("source_url") or ""),
        }

    def read_file(self, path: str) -> Any:
        target = self._workspace_path(path)
        if not target.is_file():
            raise ValueError(f"Unknown workspace file: {path}")
        text = target.read_text(encoding="utf-8")
        if target.suffix.lower() == ".json":
            return json.loads(text)
        return text

    def write_file(self, path: str, content: Any) -> dict[str, Any]:
        target = self._workspace_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": target.relative_to(self.workspace).as_posix(), "size": target.stat().st_size}

    def delete_file(self, path: str) -> dict[str, Any]:
        target = self._workspace_path(path)
        if not target.is_file():
            raise ValueError(f"Unknown workspace file: {path}")
        target.unlink()
        relative = target.relative_to(self.workspace).as_posix()
        self.events.append({"operation": "delete_file", "path": relative})
        return {"deleted": True, "path": relative}

    @staticmethod
    def _search_projection(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
        """Discovery returns a compact hit; inspection is a separate action."""
        return {key: deepcopy(row[key]) for key in ("entity_id", "entity_type", *fields) if key in row}

    @staticmethod
    def _discovery_projection(row: dict[str, Any], extra_fields: tuple[str, ...] = ()) -> dict[str, Any]:
        fields = ("entity_id", "entity_type", "name", "title", "id", "modelId", "path", "domain", *extra_fields)
        return {key: deepcopy(row[key]) for key in dict.fromkeys(fields) if key in row}

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self.tools[name]
        if tool.backend == "python":
            return self._call_python(tool, arguments)
        if tool.operation == "create":
            return self.create_row(tool.entity_type, arguments)
        if tool.operation == "update":
            entity_id = str(arguments["entity_id"])
            updates = arguments.get("updates")
            values = dict(updates) if isinstance(updates, dict) else {
                key: value for key, value in arguments.items() if key != "entity_id"
            }
            return self.update_row(tool.entity_type, entity_id, values)
        if tool.operation == "delete":
            return self.delete_row(tool.entity_type, str(arguments["entity_id"]))
        if tool.operation == "copy_resource":
            resource_id = str(arguments.get("resource_id") or tool.config.get("resource_id") or "")
            return self.copy_resource(resource_id, arguments.get("destination"))
        if tool.operation == "read_file":
            return self.read_file(str(arguments["path"]))
        if tool.operation == "write_file":
            return self.write_file(str(arguments["path"]), arguments.get("content"))
        if tool.operation == "delete_file":
            return self.delete_file(str(arguments["path"]))
        if tool.operation == "search":
            query = str(arguments.get("query", "")).casefold()
            return [self._search_projection(row, tool.search_fields) for row in self.rows_for(tool.entity_type) if not query or any(query in str(row.get(field, "")).casefold() for field in tool.search_fields)]
        if tool.operation == "lookup":
            entity_id = arguments["entity_id"]
            return deepcopy(next(row for row in self.rows_for(tool.entity_type) if row["entity_id"] == entity_id))
        if tool.operation == "rank":
            limit = int(arguments.get("limit", 5))
            if limit < 1:
                raise ValueError("limit must be positive")
            ranked = sorted(
                self.rows_for(tool.entity_type),
                key=lambda row: (row.get(tool.sort_field or "") is not None, row.get(tool.sort_field or "", 0)),
                reverse=True,
            )[:limit]
            return [self._discovery_projection(row, (tool.sort_field or "",)) for row in ranked]
        if tool.operation == "filter":
            value = str(arguments[tool.relation_field or ""])
            limit = int(arguments.get("limit", 5))
            if limit < 1:
                raise ValueError("limit must be positive")
            return [deepcopy(row) for row in self.rows_for(tool.entity_type) if str(row.get(tool.relation_field or "")) == value][:limit]
        if tool.operation == "group_count":
            field = tool.relation_field or ""
            counts: dict[str, int] = {}
            for row in self.rows_for(tool.entity_type):
                if row.get(field) in (None, ""):
                    continue
                key = str(row[field])
                counts[key] = counts.get(key, 0) + 1
            return counts
        if tool.operation == "relation_rank":
            entity_id = arguments["entity_id"]
            limit = int(arguments.get("limit", 5))
            if limit < 1:
                raise ValueError("limit must be positive")
            related = [row for row in self.rows_for(tool.related_entity_type or "") if str(row.get(tool.relation_field or "")) == str(entity_id)]
            ranked = sorted(
                related,
                key=lambda row: (row.get(tool.sort_field or "") is not None, row.get(tool.sort_field or "", 0)),
                reverse=True,
            )[:limit]
            return [self._discovery_projection(row, (tool.sort_field or "",)) for row in ranked]
        if tool.operation == "relation":
            entity_id = arguments["entity_id"]
            limit = int(arguments.get("limit", 5))
            if limit < 1:
                raise ValueError("limit must be positive")
            if not any(row["entity_id"] == entity_id for row in self.rows_for(tool.entity_type)):
                raise ValueError(f"Unknown {tool.entity_type} id: {entity_id}")
            related = [row for row in self.rows_for(tool.related_entity_type or "") if str(row.get(tool.relation_field or "")) == str(entity_id)][:limit]
            return [self._discovery_projection(row, (tool.relation_field or "",)) for row in related]
        if tool.operation == "bridge_relation":
            entity_id = str(arguments["entity_id"])
            limit = int(arguments.get("limit", 5))
            if limit < 1:
                raise ValueError("limit must be positive")
            if not any(row["entity_id"] == entity_id for row in self.rows_for(tool.entity_type)):
                raise ValueError(f"Unknown {tool.entity_type} id: {entity_id}")
            linked_ids = [
                str(row[tool.target_relation_field or ""])
                for row in self.rows_for(tool.link_entity_type or "")
                if str(row.get(tool.source_relation_field or "")) == entity_id and row.get(tool.target_relation_field or "") is not None
            ]
            targets = {row["entity_id"]: row for row in self.rows_for(tool.related_entity_type or "")}
            return [self._discovery_projection(targets[linked_id]) for linked_id in linked_ids if linked_id in targets][:limit]
        if tool.operation == "linked_id":
            entity_id = arguments["entity_id"]
            source = next((row for row in self.rows_for(tool.entity_type) if row["entity_id"] == entity_id), None)
            if source is None:
                raise ValueError(f"Unknown {tool.entity_type} id: {entity_id}")
            linked_id = source.get(tool.relation_field or "")
            if linked_id is None or not any(row["entity_id"] == str(linked_id) for row in self.rows_for(tool.related_entity_type or "")):
                raise ValueError("linked record is absent from the local snapshot")
            return {"entity_id": str(linked_id), "entity_type": tool.related_entity_type}
        if tool.operation == "compare":
            left_id, right_id = arguments["left_id"], arguments["right_id"]
            rows = {row["entity_id"]: row for row in self.rows_for(tool.entity_type)}
            if left_id not in rows or right_id not in rows:
                raise ValueError("comparison ids must identify stored records")
            field = tool.sort_field or ""
            left, right = rows[left_id], rows[right_id]
            if not isinstance(left.get(field), (int, float)) or not isinstance(right.get(field), (int, float)):
                raise ValueError("comparison field must be numeric")
            winner = left if left[field] >= right[field] else right
            return {
                "field": field,
                "left_id": left_id,
                "left_value": left[field],
                "right_id": right_id,
                "right_value": right[field],
                "winner_id": winner["entity_id"],
                "difference": abs(left[field] - right[field]),
            }
        raise ValueError(f"Unsupported operation: {tool.operation}")

    def _call_python(self, tool: ToolSpec, arguments: dict[str, Any]) -> Any:
        handler = self._python_handlers.get(tool.name)
        if handler is None:
            namespace: dict[str, Any] = {"json": json}
            exec(tool.implementation, namespace)
            handler = namespace.get("run")
            if not callable(handler):
                raise ValueError(f"Python tool {tool.name} must define run(context, arguments)")
            self._python_handlers[tool.name] = handler
        return handler(RuntimeContext(self), deepcopy(arguments))

    def execute(self, calls: list[dict[str, Any]]) -> dict[str, Any]:
        trace = []
        for call in calls:
            result = self.call(call["tool"], call.get("arguments", {}))
            trace.append({"tool": call["tool"], "arguments": call.get("arguments", {}), "result": result})
        if not trace:
            raise ValueError("A reference plan must contain at least one tool call")
        return {"trace": trace, "final_result": trace[-1]["result"]}
