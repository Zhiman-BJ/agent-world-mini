import sqlite3

import pytest

from env_gen.tool_gen.runtime import RecordStore
from task_gen.tool_graph.state_runtime import Context


@pytest.fixture
def state(tmp_path):
    environment = {'record_sets': [
        {'record_set_id': 'parents', 'access': 'copy_on_write', 'key_fields': ['id'],
         'fields': {'id': {'type': 'string', 'nullable': False},
                    'enabled': {'type': 'boolean', 'nullable': False}}},
        {'record_set_id': 'children', 'access': 'copy_on_write', 'key_fields': ['id'],
         'fields': {'id': {'type': 'string', 'nullable': False},
                    'parent_id': {'type': 'string', 'nullable': False}}},
    ]}
    with sqlite3.connect(tmp_path / 'records.sqlite') as connection:
        connection.execute('CREATE TABLE parents(id TEXT PRIMARY KEY, enabled INTEGER)')
        connection.execute('CREATE TABLE children(id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parents(id))')
        connection.execute("INSERT INTO parents VALUES('p1',1)")
        connection.execute("INSERT INTO children VALUES('c1','p1')")
    return tmp_path, environment


@pytest.mark.parametrize('factory', [
    lambda root, env: RecordStore(root / 'records.sqlite', env),
    lambda root, env: Context(root, env).records,
])
def test_shared_record_behavior(state, factory):
    root, env = state
    records = factory(root, env)
    assert records.list('parents', limit=10000)[0]['enabled'] is True
    assert records.create('parents', {'id': 'p2', 'enabled': False}) == {'id': 'p2', 'enabled': False}
    with pytest.raises(ValueError):
        records.list('parents', filters={'enabled': 0})
    with pytest.raises(ValueError):
        records.update('parents', {}, {'enabled': False})
    with pytest.raises(ValueError):
        records.delete('parents', {})
    with pytest.raises(sqlite3.IntegrityError):
        records.delete('parents', {'id': 'p1'})
    with pytest.raises(sqlite3.IntegrityError):
        records.update('children', {'id': 'c1'}, {'parent_id': 'missing'})
    assert records.get('children', {'id': 'c1'})['parent_id'] == 'p1'
    assert records.delete('children', {'id': 'c1'}) == 1
    assert records.delete('parents', {'id': 'p1'}) == 1
