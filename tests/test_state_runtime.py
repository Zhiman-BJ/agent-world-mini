import sqlite3
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / "task_gen/tool_graph/state_runtime.py"
spec = importlib.util.spec_from_file_location("state_runtime", MODULE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
Context, snapshot_state, state_diff = (
    runtime.Context,
    runtime.snapshot_state,
    runtime.state_diff,
)


ENV = {
    "schema_version": "2.0",
    "record_sets": [
        {
            "record_set_id": "items",
            "access": "copy_on_write",
            "key_fields": ["id", "part"],
            "fields": {
                "id": {"type": "integer", "nullable": False},
                "part": {"type": "string", "nullable": False},
                "ok": {"type": "boolean", "nullable": False},
                "data": {"type": "object", "nullable": True},
                "score": {"type": "number", "minimum": 0, "nullable": False},
            },
        },
        {
            "record_set_id": "logs",
            "access": "copy_on_write",
            "key_fields": [],
            "fields": {"message": {"type": "string", "nullable": False}},
        },
        {
            "record_set_id": "fixed",
            "access": "read_only",
            "key_fields": ["id"],
            "fields": {"id": {"type": "integer", "nullable": False}},
        },
    ],
    "filesystem_scopes": [{"scope_id": "exports", "access": "copy_on_write"}],
}


def setup_state(root: Path):
    (root / "filesystem_scopes" / "exports").mkdir(parents=True)
    with sqlite3.connect(root / "records.sqlite") as c:
        c.execute(
            "CREATE TABLE items (id INTEGER, part TEXT, ok INTEGER, data TEXT, score REAL, PRIMARY KEY(id,part))"
        )
        c.execute("CREATE TABLE logs (message TEXT)")
        c.execute("CREATE TABLE fixed (id INTEGER PRIMARY KEY)")
        c.execute('INSERT INTO items VALUES (1,"a",1,\'{"x":[1]}\',2.5)')


def test_nested_nullable_and_undeclared_tables(tmp_path):
    runtime._validate({'nested': [None, 'value']}, {
        'type': 'object', 'properties': {'nested': {
            'type': 'array', 'items': {'type': 'string', 'nullable': True}}}}, 'data')
    setup_state(tmp_path)
    with sqlite3.connect(tmp_path / 'records.sqlite') as connection:
        connection.execute('CREATE TABLE undeclared(value TEXT)')
    with pytest.raises(ValueError, match='declared Record Sets'):
        snapshot_state(tmp_path, ENV)


def test_records_validate_decode_and_mutate(tmp_path):
    setup_state(tmp_path)
    r = Context(tmp_path, ENV).records
    assert r.get("items", {"id": 1, "part": "a"}) == {
        "id": 1,
        "part": "a",
        "ok": True,
        "data": {"x": [1]},
        "score": 2.5,
    }
    r.create("items", {"id": 2, "part": "b", "ok": False, "data": None, "score": 0})
    assert r.list("items", {"data": None}, order_by="id")[-1]["ok"] is False
    assert r.update("items", {"id": 2, "part": "b"}, {"data": {"y": 2}}) == 1
    assert r.delete("items", {"id": 2, "part": "b"}) == 1
    with pytest.raises(ValueError):
        r.get("items", {"id": 1})
    with pytest.raises(ValueError):
        r.get("logs", {})
    with pytest.raises(ValueError):
        r.create("items", {"id": True, "part": "x", "ok": True, "data": {}, "score": 1})


def test_permissions_scope_and_failed_write_rollback(tmp_path):
    setup_state(tmp_path)
    context = Context(tmp_path, ENV)
    assert (
        context.scope_root("exports")
        == (tmp_path / "filesystem_scopes" / "exports").resolve()
    )
    with pytest.raises(KeyError):
        context.scope_root("missing")
    with pytest.raises(PermissionError):
        context.records.create("fixed", {"id": 1})
    with pytest.raises(PermissionError):
        Context(tmp_path, ENV, True).records.delete("items", {"id": 1, "part": "a"})
    with pytest.raises(sqlite3.IntegrityError):
        context.records.create(
            "items", {"id": 1, "part": "a", "ok": False, "data": None, "score": 1}
        )
    assert len(context.records.list("items")) == 1


def test_snapshot_diff_keyless_duplicates_and_files(tmp_path):
    setup_state(tmp_path)
    c = Context(tmp_path, ENV)
    c.records.create("logs", {"message": "same"})
    c.records.create("logs", {"message": "same"})
    before = snapshot_state(tmp_path, ENV)
    c.records.create("logs", {"message": "same"})
    c.records.update("items", {"id": 1, "part": "a"}, {"score": 3})
    (c.scope_root("exports") / "out.txt").write_text("ok")
    diff = state_diff(before, snapshot_state(tmp_path, ENV))
    assert diff["changed_assets"] == ["exports", "items", "logs"]
    assert len(diff["record_sets"]["logs"]["inserted"]) == 1
    assert diff["record_sets"]["items"]["updated"][0]["changed_fields"] == ["score"]


def test_sandbox_rolls_back_failed_and_undeclared_writes(tmp_path):
    from task_gen.tool_graph.step_3_chain_execute import _call_tool

    setup_state(tmp_path)
    before = snapshot_state(tmp_path, ENV)
    prefix = "def run(arguments, context):\n    context.records.update('items', {'id': 1, 'part': 'a'}, {'score': 10})\n"
    for body in [
        "    return {'success': False}\n",
        "    (context.state_root / 'unexpected').write_text('no')\n    return {'success': True}\n",
    ]:
        result = _call_tool(
            prefix + body, {}, tmp_path, 15, 512 * 1024**2, 1024**2, ENV
        )
        assert result["error"] or result["result"]["success"] is False
        assert snapshot_state(tmp_path, ENV) == before
    result = _call_tool(
        prefix + "    return {'success': True}\n",
        {},
        tmp_path,
        15,
        512 * 1024**2,
        1024**2,
        ENV,
    )
    assert result["error"] is None
    assert (
        Context(tmp_path, ENV).records.get(
            record_set_id="items", key={"id": 1, "part": "a"}
        )["score"]
        == 10
    )


def test_declared_keys_remain_readable_for_independent_verification(tmp_path):

    setup_state(tmp_path)
    with sqlite3.connect(tmp_path / "records.sqlite") as c:
        c.execute("DROP TABLE items")
        c.execute(
            "CREATE TABLE items (id INTEGER, part TEXT, ok INTEGER, data TEXT, score REAL)"
        )
    records = Context(tmp_path, ENV).records
    item = {"id": 1, "part": "a", "ok": True, "data": {}, "score": 1}
    records.create("items", item)
    with pytest.raises(sqlite3.IntegrityError):
        records.create("items", item)
    assert len(records.list("items")) == 1
    assert Context(tmp_path, ENV, read_only=True).records.get("items", {"id": 1, "part": "a"}) == item
