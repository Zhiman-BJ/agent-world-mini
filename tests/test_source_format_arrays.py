from copy import deepcopy
import json
from pathlib import Path

import pytest

from task_gen.program.proposal_sources import parameter_source_values, same_source_value, source_value


@pytest.fixture
def example():
    guide_path = Path(__file__).resolve().parents[1] / "task_gen/program/AUTHOR_SOLUTION_FORMAT.json"
    guide = json.loads(guide_path.read_text(encoding="utf-8"))
    return deepcopy(guide["parameter_binding_examples"][0])


def resolve_example(example):
    expected = parameter_source_values(example["arguments"], example["argument_sources"])
    preceding = [{"visible_output": result, "completed_event_sequence": index}
                 for index, result in enumerate(example["previous_results"], 1)]
    resolved = {
        path: source_value(source, task={"task_text": example["task_text"]}, protocol={}, schema={},
                           preceding=preceding, current_start=len(preceding) + 1, argument_path=path)
        for path, source in example["argument_sources"].items()
    }
    return expected, resolved


def test_documented_array_selection_uses_real_prior_values_without_task_literals(example):
    expected, resolved = resolve_example(example)

    assert len(expected) == 4
    assert set(expected) == set(resolved)
    assert all(same_source_value(value, resolved[path]) for path, value in expected.items())
    assert all(source["kind"] == "call" for source in example["argument_sources"].values())
    assert not any(character.isdigit() for character in example["task_text"])


@pytest.mark.parametrize("mutation", ["missing", "overlap", "future"])
def test_documented_array_example_preserves_source_checks(example, mutation):
    if mutation == "missing":
        example["argument_sources"].pop("/object_ids/1")
    elif mutation == "overlap":
        example["argument_sources"]["/object_ids"] = {
            "kind": "call", "call": 1, "pointer": "/data/objects"}
    else:
        example["argument_sources"]["/object_ids/0"]["call"] = len(example["previous_results"]) + 1

    with pytest.raises(ValueError):
        resolve_example(example)
