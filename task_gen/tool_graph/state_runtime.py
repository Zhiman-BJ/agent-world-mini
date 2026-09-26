"""新版 Record Set API 与逻辑状态差异；工具在沙箱内使用同一份 Context。"""

import json
import sqlite3
from collections import Counter
from pathlib import Path


def _json(v):
    return json.dumps(
        v, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _quote(s):
    if not isinstance(s, str) or not s or "\x00" in s:
        raise ValueError("invalid identifier")
    return '"' + s.replace('"', '""') + '"'


def _native(v):
    return json.loads(json.dumps(v, ensure_ascii=False, allow_nan=False))


def _encode(v, spec):
    return _json(v) if v is not None and spec.get("type") in ("object", "array") else v


def _field_schema(spec):
    schema = dict(spec)
    nullable = schema.pop('nullable', False)
    if 'properties' in schema:
        schema['properties'] = {k: _field_schema(v) for k, v in schema['properties'].items()}
    for key in ('items', 'additionalProperties'):
        if isinstance(schema.get(key), dict):
            schema[key] = _field_schema(schema[key])
    return {'anyOf': [schema, {'type': 'null'}]} if nullable else schema


def _validate(v, spec, name):
    from jsonschema import Draft202012Validator

    schema = _field_schema(spec)
    error = next(Draft202012Validator(schema).iter_errors(v), None)
    if error:
        raise ValueError(f"{name}: {error.message}")
    _native(v)


class _Records:
    def __init__(self, root, env, readonly):
        self.root, self.env, self.readonly = root, env, readonly

    def _decl(self, rid):
        for d in self.env.get("record_sets", []):
            if d.get("record_set_id") == rid:
                return d
        raise KeyError("unknown record set")

    def _conn(self):
        mode = "ro" if self.readonly else "rw"
        c = sqlite3.connect(
            (self.root / "records.sqlite").as_uri() + "?mode=" + mode, uri=True
        )
        c.row_factory = sqlite3.Row
        return c

    def _key(self, d, key, required=True):
        if not isinstance(key, dict):
            raise TypeError("key must be object")
        fields = d.get("key_fields", [])
        if required and not fields:
            raise ValueError("record set has no key")
        if set(key) != set(fields):
            raise ValueError("key fields mismatch")
        for k, v in key.items():
            _validate(v, d["fields"][k], k)
        return fields

    def _values(self, d, record, complete):
        if not isinstance(record, dict):
            raise TypeError("record must be object")
        fields = d["fields"]
        unknown = set(record) - set(fields)
        if unknown:
            raise ValueError("unknown fields")
        if complete and set(record) != set(fields):
            raise ValueError("complete record required")
        for k, v in record.items():
            _validate(v, fields[k], k)

    def _decode(self, row, d):
        out = {}
        for k, s in d["fields"].items():
            v = row[k]
            if v is not None and s.get("type") in ("object", "array"):
                v = json.loads(v)
            if v is not None and s.get("type") == "boolean":
                v = bool(v)
            out[k] = v
        return out

    def list(
        self,
        record_set_id,
        filters=None,
        limit=100,
        offset=0,
        order_by=None,
        descending=False,
    ):
        rid = record_set_id
        d = self._decl(rid)
        filters = filters or {}
        self._values(d, filters, False)
        if (
            type(limit) is not int
            or limit < 0
            or type(offset) is not int
            or offset < 0
            or type(descending) is not bool
        ):
            raise ValueError("invalid pagination")
        if order_by is not None and order_by not in d["fields"]:
            raise ValueError("invalid order_by")
        c = self._conn()
        try:
            q = "SELECT * FROM " + _quote(rid)
            vals = []
            if filters:
                terms = []
                for k, v in filters.items():
                    terms.append(_quote(k) + (" IS NULL" if v is None else " = ?"))
                    v is None or vals.append(_encode(v, d["fields"][k]))
                q += " WHERE " + " AND ".join(terms)
            if order_by:
                q += (
                    " ORDER BY "
                    + _quote(order_by)
                    + (" DESC" if descending else " ASC")
                )
            q += " LIMIT ? OFFSET ?"
            vals += [limit, offset]
            return [self._decode(r, d) for r in c.execute(q, vals)]
        finally:
            c.close()

    def get(self, record_set_id, key):
        rid = record_set_id
        d = self._decl(rid)
        self._key(d, key)
        rows = self.list(rid, key, 2, 0)
        if len(rows) > 1:
            raise ValueError("duplicate record key")
        return rows[0] if rows else None

    def _write(self, d):
        if self.readonly or d.get("access") != "copy_on_write":
            raise PermissionError("state is read-only")

    def _commit(self, connection, declaration):
        keys = declaration.get("key_fields", [])
        if keys:
            query = (
                "SELECT 1 FROM "
                + _quote(declaration["record_set_id"])
                + " GROUP BY "
                + ",".join(map(_quote, keys))
                + " HAVING COUNT(*) > 1 LIMIT 1"
            )
            if connection.execute(query).fetchone():
                raise sqlite3.IntegrityError("duplicate declared record key")
        connection.commit()

    def create(self, record_set_id, record):
        rid = record_set_id
        d = self._decl(rid)
        self._write(d)
        self._values(d, record, True)
        c = self._conn()
        try:
            cols = list(d["fields"])
            vals = [_encode(record[x], d["fields"][x]) for x in cols]
            c.execute(
                "INSERT INTO "
                + _quote(rid)
                + " ("
                + ",".join(map(_quote, cols))
                + ") VALUES ("
                + ",".join("?" * len(cols))
                + ")",
                vals,
            )
            self._commit(c, d)
            return self._native_record(d, record)
        finally:
            c.close()

    def _native_record(self, d, r):
        return _native(r)

    def update(self, record_set_id, key, changes):
        rid = record_set_id
        d = self._decl(rid)
        self._write(d)
        self._key(d, key)
        self._values(d, changes, False)
        if not changes:
            raise ValueError("empty changes")
        c = self._conn()
        try:
            sets = []
            vals = []
            for k, v in changes.items():
                sets.append(_quote(k) + " = ?")
                vals.append(_encode(v, d["fields"][k]))
            where = " AND ".join(
                _quote(k) + (" IS NULL" if v is None else " = ?")
                for k, v in key.items()
            )
            vals += [v for v in key.values() if v is not None]
            cur = c.execute(
                "UPDATE " + _quote(rid) + " SET " + ",".join(sets) + " WHERE " + where,
                vals,
            )
            self._commit(c, d)
            return cur.rowcount
        finally:
            c.close()

    def delete(self, record_set_id, key):
        rid = record_set_id
        d = self._decl(rid)
        self._write(d)
        self._key(d, key)
        c = self._conn()
        try:
            where = " AND ".join(
                _quote(k) + (" IS NULL" if v is None else " = ?")
                for k, v in key.items()
            )
            vals = [v for v in key.values() if v is not None]
            cur = c.execute("DELETE FROM " + _quote(rid) + " WHERE " + where, vals)
            c.commit()
            return cur.rowcount
        finally:
            c.close()


class Context:
    def __init__(self, root, environment, read_only=False):
        self.state_root = Path(root).resolve()
        self.environment = environment or {}
        self.read_only = read_only
        self.records = _Records(self.state_root, self.environment, read_only)

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
