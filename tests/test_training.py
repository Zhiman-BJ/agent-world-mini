from __future__ import annotations

import json
import unittest

from training.evaluate_internal import parse_answer
from training.prepare_data import (
    SPLIT_COUNTS,
    compact_reference_answer,
    rebalance_split,
    request_with_sections,
    stratified_split,
)
from training.prepare_mcp_atlas_subset import envfactory_rows, used_tools
from training.reward import compute_score, score_one


class TrainingDataTests(unittest.TestCase):
    def test_parse_answer_accepts_plain_or_fenced_json(self) -> None:
        self.assertEqual(parse_answer('{"result": 1}'), {"result": 1})
        self.assertEqual(parse_answer('```json\n{"result": 1}\n```'), {"result": 1})
        self.assertEqual(parse_answer('Here is the answer: {"result": 1}'), {"result": 1})
        self.assertIsNone(parse_answer("No structured answer was returned."))

    def test_mcp_atlas_subset_uses_reference_trajectory_servers(self) -> None:
        clean = {
            "TASK": "clean",
            "TRAJECTORY": json.dumps(
                [{"tool_calls": [{"function": {"name": "filesystem_read_file"}}]}]
            ),
        }
        excluded = {
            "TASK": "excluded",
            "TRAJECTORY": json.dumps(
                [{"tool_calls": [{"function": {"name": "mongodb_find"}}]}]
            ),
        }
        self.assertEqual(used_tools(clean), ["filesystem_read_file"])
        self.assertEqual([row["TASK"] for row in envfactory_rows([clean, excluded])], ["clean"])

    def test_request_names_required_json_sections(self) -> None:
        request = request_with_sections(
            "Find the records.",
            [{"name": "result", "description": "The grounded records", "step_indices": [0]}],
        )
        self.assertIn('"name": "result"', request)
        self.assertNotIn("step_indices", request)

    def test_environment_split_is_exact_and_balanced(self) -> None:
        environments = []
        for index in range(120):
            environments.append(
                {
                    "global_environment_id": f"batch/env-{index}",
                    "domain": f"domain-{index % 6}",
                    "complexity": ("short", "medium", "long")[index % 3],
                    "tasks": 1 + index % 12,
                    "trajectory_count": 2 + index % 24,
                }
            )
        split = rebalance_split(environments, stratified_split(environments, 7))
        self.assertEqual({name: list(split.values()).count(name) for name in SPLIT_COUNTS}, SPLIT_COUNTS)
        self.assertEqual(len(split), len(set(split)))

    def test_reward_requires_answer_and_observation_support(self) -> None:
        truth = {
            "request": "Report the unemployment rate for June 2026.",
            "reference_answer": {"rate": {"period": "2026-06", "value": 4.4, "unit": "percent"}},
        }
        solution = (
            '<tool_call>{"name":"lookup","arguments":{}}</tool_call>'
            '<tool_response>{"period":"2026-06","value":4.4,"unit":"percent"}</tool_response>'
            '{"rate":"The June 2026 rate was 4.4 percent."}'
        )
        self.assertEqual(score_one(solution, json.dumps(truth))["score"], 1.0)
        self.assertEqual(
            compute_score(
                data_source="agentworld",
                solution_str=solution,
                ground_truth=json.dumps(truth),
                extra_info={},
            )["score"],
            1.0,
        )
        unsupported = '{"rate":"The June 2026 rate was 4.4 percent."}'
        self.assertEqual(score_one(unsupported, truth)["score"], 0.0)

    def test_reward_does_not_reward_more_calls(self) -> None:
        truth = {"request": "Return item Alpha.", "reference_answer": {"item": {"name": "Alpha"}}}
        base = (
            '<tool_call>{"name":"lookup","arguments":{"id":"a"}}</tool_call>'
            '<tool_response>{"name":"Alpha"}</tool_response>{"item":"Alpha"}'
        )
        repeated = base.replace("<tool_response>", base.split("<tool_response>", 1)[0] + "<tool_response>", 1)
        self.assertEqual(score_one(base, truth)["score"], 1.0)
        self.assertLessEqual(score_one(repeated, truth)["score"], 1.0)

    def test_reward_uses_entity_coverage_for_grounding(self) -> None:
        truth = {
            "request": "Report Alpha and its linked Beta record.",
            "reference_answer": {
                "result": [
                    {"entity_id": "item:alpha", "name": "Alpha"},
                    {"entity_id": "item:beta", "name": "Beta"},
                ]
            },
        }
        partial = (
            '<tool_response>{"entity_id":"item:alpha","name":"Alpha"}</tool_response>'
            '{"result":"Alpha and Beta"}'
        )
        complete = (
            '<tool_response>[{"entity_id":"item:alpha"},{"entity_id":"item:beta"}]</tool_response>'
            '{"result":"Alpha and Beta"}'
        )
        self.assertEqual(score_one(partial, truth)["grounding"], 0.5)
        self.assertEqual(score_one(complete, truth)["score"], 1.0)

    def test_compact_reference_merges_repeated_entity_observations(self) -> None:
        task = {
            "reference_execution": {
                "reference_answer": {
                    "result": [
                        [{"entity_id": "target:1", "name": "Albumin"}],
                        {"entity_id": "target:1", "organism": "Homo sapiens", "source_url": "https://x"},
                        {"entity_id": "activity:1", "standard_type": "Log K", "standard_value": "1.39"},
                    ]
                }
            }
        }
        self.assertEqual(
            compact_reference_answer(task),
            {
                "result": [
                    {"entity_id": "target:1", "name": "Albumin", "organism": "Homo sapiens"},
                    {"entity_id": "activity:1", "standard_type": "Log K", "standard_value": "1.39"},
                ]
            },
        )

    def test_compact_reward_requires_fields_from_each_entity(self) -> None:
        truth = {
            "request": "Report the target name, organism, activity measurement, journal, and year.",
            "expected_answer": {
                "result": [
                    {"entity_id": "target:1", "name": "Albumin", "organism": "Homo sapiens"},
                    {"entity_id": "activity:1", "standard_type": "Log K", "standard_value": "1.39"},
                    {"entity_id": "document:1", "journal": "J Med Chem", "year": 2001},
                ]
            },
            "reference_answer": {
                "result": [
                    {"entity_id": "target:1"},
                    {"entity_id": "activity:1"},
                    {"entity_id": "document:1"},
                ]
            },
        }
        observations = (
            '<tool_response>{"entity_id":"target:1","name":"Albumin","organism":"Homo sapiens"}</tool_response>'
            '<tool_response>{"entity_id":"activity:1","standard_type":"Log K","standard_value":"1.39"}</tool_response>'
            '<tool_response>{"entity_id":"document:1","journal":"J Med Chem","year":2001}</tool_response>'
        )
        complete = (
            observations
            + '{"result":{"target":"Albumin, Homo sapiens","activity":"Log K 1.39",'
            '"document":"J Med Chem, 2001"}}'
        )
        partial = observations + '{"result":{"target":"Albumin, Homo sapiens"}}'
        self.assertEqual(score_one(complete, truth)["score"], 1.0)
        self.assertAlmostEqual(score_one(partial, truth)["slot_coverage"], 1 / 3)


if __name__ == "__main__":
    unittest.main()
