from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import Record, ToolSpec


class LocalToolRuntime:
    """Shared resettable execution engine for compiled environment contracts.

    Source records are immutable.  Optional overlay records are local to one
    rollout and are deliberately reset between solver attempts.
    """

    def __init__(self, records: list[Record], tools: list[ToolSpec]):
        self.base_rows = [record.attributes | {
            "entity_id": record.entity_id,
            "entity_type": record.entity_type,
            "source_url": record.source_url,
        } for record in records]
        self.tools = {tool.name: tool for tool in tools}
        self.overlay_rows: list[dict[str, Any]] = []
        self.reset()

    def reset(self) -> None:
        self.rows = deepcopy(self.base_rows)
        self.overlay_rows = []

    def snapshot(self) -> dict[str, Any]:
        return {"base_record_count": len(self.base_rows), "overlay_records": deepcopy(self.overlay_rows)}

    def rows_for(self, entity_type: str) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["entity_type"] == entity_type]

    @staticmethod
    def _search_projection(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
        """Discovery returns a compact hit; inspection is a separate action."""
        return {key: deepcopy(row[key]) for key in ("entity_id", "entity_type", *fields) if key in row}

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self.tools[name]
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
            return deepcopy(sorted(
                self.rows_for(tool.entity_type),
                key=lambda row: (row.get(tool.sort_field or "") is not None, row.get(tool.sort_field or "", 0)),
                reverse=True,
            )[:limit])
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
            return deepcopy(sorted(
                related,
                key=lambda row: (row.get(tool.sort_field or "") is not None, row.get(tool.sort_field or "", 0)),
                reverse=True,
            )[:limit])
        if tool.operation == "relation":
            entity_id = arguments["entity_id"]
            limit = int(arguments.get("limit", 5))
            if limit < 1:
                raise ValueError("limit must be positive")
            if not any(row["entity_id"] == entity_id for row in self.rows_for(tool.entity_type)):
                raise ValueError(f"Unknown {tool.entity_type} id: {entity_id}")
            return [deepcopy(row) for row in self.rows_for(tool.related_entity_type or "") if str(row.get(tool.relation_field or "")) == str(entity_id)][:limit]
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
            return deepcopy({
                "field": field,
                "left": left,
                "right": right,
                "winner_id": winner["entity_id"],
                "difference": abs(left[field] - right[field]),
            })
        raise ValueError(f"Unsupported operation: {tool.operation}")

    def execute(self, calls: list[dict[str, Any]]) -> dict[str, Any]:
        trace = []
        for call in calls:
            result = self.call(call["tool"], call.get("arguments", {}))
            trace.append({"tool": call["tool"], "arguments": call.get("arguments", {}), "result": result})
        if not trace:
            raise ValueError("A reference plan must contain at least one tool call")
        return {"trace": trace, "final_result": trace[-1]["result"]}
