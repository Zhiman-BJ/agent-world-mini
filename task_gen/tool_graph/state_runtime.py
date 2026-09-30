"""新版 Record Set API 与逻辑状态差异；工具在沙箱内使用同一份 Context。"""

import json
import sqlite3
from collections import Counter
from pathlib import Path


from utils.record_store import RecordStore, _validate, _json, _quote
from env_gen.tool_gen.resources import ResourceCatalog

class Context:
    def __init__(self, root, environment, read_only=False):
        self.state_root = Path(root).resolve()
        self.environment = environment or {}
        self.read_only = read_only
        self.records = RecordStore(self.state_root / 'records.sqlite', self.environment, read_only)
        self.resources = ResourceCatalog(self.environment, self.scope_root)

    def resolve_resource(self, ref, must_exist=False):
        return self.resources.resolve(ref, must_exist=must_exist)

    def scope_root(self, scope_id):
        if (
            not isinstance(scope_id, str)
            or not scope_id
            or "/" in scope_id
            or "\\" in scope_id
        ):
            raise ValueError("invalid scope")
        declared = {
            d.get("scope_id"): d for d in self.environment.get("filesystem_scopes", [])
        }
        if scope_id not in declared:
            raise KeyError("unknown scope")
        base = (self.state_root / "filesystem_scopes").resolve()
        p = (base / scope_id).resolve()
        if p.parent != base:
            raise ValueError("scope escape")
        return p


def snapshot_state(root, environment):
    root = Path(root)
    sets = {}
    db = root / "records.sqlite"
    if db.exists():
        c = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        try:
            declared = {d['record_set_id'] for d in environment.get('record_sets', [])}
            tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables != declared:
                raise ValueError('SQLite tables differ from declared Record Sets: ' + str(sorted(tables ^ declared)))
            for d in environment.get("record_sets", []):
                rid = d["record_set_id"]
                rows = [dict(r) for r in c.execute("SELECT * FROM " + _quote(rid))]
                fields = d["fields"]
                for r in rows:
                    for k, s in fields.items():
                        if s.get("type") in ("object", "array") and r[k] is not None:
                            r[k] = json.loads(r[k])
                        if s.get("type") == "boolean" and r[k] is not None:
                            r[k] = bool(r[k])
                keys = d.get("key_fields", [])
                rows.sort(
                    key=lambda r: _json([r[k] for k in keys]) if keys else _json(r)
                )
                sets[rid] = {"records": rows, "key_fields": keys}
        finally:
            c.close()
    from task_gen.program.utils.tool_runtime import snapshot_state as base_snapshot

    snapshot = base_snapshot(root, environment, package_format="v2")
    # 保留逐字段信息，供状态差异报告使用。
    snapshot["record_sets"] = sets
    for path in root.rglob("*"):
        if path.is_dir():
            relative = path.relative_to(root)
            parts = relative.parts
            if (
                len(parts) >= 2
                and parts[0] == "filesystem_scopes"
                and parts[1] in snapshot["filesystem_scopes"]
            ):
                snapshot["filesystem_scopes"][parts[1]]["files"][
                    str(Path(*parts[2:])) + "/"
                ] = {"sha256": "directory"}
            else:
                snapshot["undeclared_files"][str(relative) + "/"] = {
                    "sha256": "directory"
                }
    return snapshot


def state_diff(before, after):
    changes = {}
    for rid in set(before.get("record_sets", {})) | set(after.get("record_sets", {})):
        b = before.get("record_sets", {}).get(rid, {})
        a = after.get("record_sets", {}).get(rid, {})
        keys = b.get("key_fields") or a.get("key_fields") or []

        def indexed(rows):
            seen = Counter()
            out = {}
            for r in rows:
                base = _json([r.get(k) for k in keys]) if keys else _json(r)
                seen[base] += 1
                out[base if keys else base + "#" + str(seen[base])] = r
            return out

        bm = indexed(b.get("records", []))
        am = indexed(a.get("records", []))
        ins = sorted(set(am) - set(bm))
        dele = sorted(set(bm) - set(am))
        upd = []
        for k in sorted(set(bm) & set(am)):
            fields = sorted(
                x for x in set(bm[k]) | set(am[k]) if bm[k].get(x) != am[k].get(x)
            )
            if fields:
                upd.append({"key": k, "changed_fields": fields})
        if ins or dele or upd:
            changes[rid] = {"inserted": ins, "updated": upd, "deleted": dele}
    from task_gen.program.utils.tool_runtime import workspace_diff

    metadata_diff = workspace_diff(
        {**before, "record_sets": {}}, {**after, "record_sets": {}}
    )
    assets = sorted(set(changes) | set(metadata_diff["changed_assets"]))
    return {**metadata_diff, "changed_assets": assets, "record_sets": changes}
