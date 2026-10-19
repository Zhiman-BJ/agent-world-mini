import json

import pytest

from task_gen.program.utils.io import read_jsonl


def test_missing_jsonl_is_an_empty_history(tmp_path):
    assert read_jsonl(tmp_path / "missing.jsonl") == []


def test_jsonl_preserves_order_and_duplicate_events(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"value": 1}\n\n{"value": 1}\n{"value": 2}\n', encoding="utf-8")
    assert read_jsonl(path) == [{"value": 1}, {"value": 1}, {"value": 2}]


def test_jsonl_does_not_hide_corrupted_evidence(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"value": 1}\nbroken\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_jsonl(path)
