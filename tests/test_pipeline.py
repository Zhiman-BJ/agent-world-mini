import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent_world_mini.catalog import discover_smithery_themes, load_prepared_catalog, select_prepared_themes
from agent_world_mini.graph import ToolGraph
from agent_world_mini.llm import LLMClient
from agent_world_mini.models import Record, ResearchBundle, ToolSpec
from agent_world_mini.pipeline import run
from agent_world_mini.runtime import LocalToolRuntime
from agent_world_mini.research import WebResearchAgent
from agent_world_mini.tasks import TaskSynthesizer
from agent_world_mini.themes import CURATED_THEME_SEEDS, resolve_theme
from agent_world_mini.tools import ToolDesigner, ToolValidator
from agent_world_mini.verification import FiveRunVerifier


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            Record("country", "USA", {"name": "United States", "region": "North America", "income_level": "High income"}, "https://example.test/countries"),
            Record("country", "IND", {"name": "India", "region": "South Asia", "income_level": "Lower middle income"}, "https://example.test/countries"),
            Record("indicator_observation", "usa_population", {"country_id": "USA", "indicator": "population", "year": 2023, "value": 334_914_895}, "https://example.test/indicators"),
            Record("indicator_observation", "ind_population", {"country_id": "IND", "indicator": "population", "year": 2023, "value": 1_438_069_596}, "https://example.test/indicators"),
        ]
        self.bundle = ResearchBundle("country indicators", "test", "x", [], self.records, {})

    def _validated_tools(self):
        candidates, mode = ToolDesigner(LLMClient()).design(self.bundle)
        retained, reports = ToolValidator().validate(candidates, LocalToolRuntime(self.records, candidates))
        return candidates, retained, reports, mode

    def test_research_bundle_round_trip_supports_codex_handoff(self):
        restored = ResearchBundle.from_dict(self.bundle.to_dict())
        self.assertEqual(restored.theme, self.bundle.theme)
        self.assertEqual(restored.adapter, self.bundle.adapter)
        self.assertEqual([record.to_dict() for record in restored.records], [record.to_dict() for record in self.records])

    def test_research_bundle_requires_real_records(self):
        with self.assertRaisesRegex(ValueError, "theme and at least one record"):
            ResearchBundle.from_dict({"theme": "empty", "records": []})

    def test_pipeline_can_continue_from_a_codex_research_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = root / "codex-research.json"
            bundle_path.write_text(json.dumps(self.bundle.to_dict()), encoding="utf-8")
            with patch("agent_world_mini.pipeline.LLMClient.from_environment", return_value=LLMClient()), patch(
                "agent_world_mini.pipeline.WebResearchAgent.gather"
            ) as gather:
                summary = run(None, root / "output", research_bundle=bundle_path, max_candidates=4)
            gather.assert_not_called()
            self.assertEqual(summary["records"], len(self.records))
            self.assertTrue((root / "output" / "tool_specs.json").is_file())

    def test_tools_are_derived_from_entities_and_relations(self):
        _candidates, tools, reports, mode = self._validated_tools()
        names = {tool.name for tool in tools}
        self.assertEqual(mode, "data_configuration_compiler")
        self.assertIn("search_countries", names)
        self.assertIn("get_country", names)
        self.assertIn("list_indicator_observations_for_country", names)
        self.assertTrue(all(report["status"] == "passed" for report in reports))

    def test_relation_tool_uses_a_real_upstream_id(self):
        _candidates, tools, _reports, _mode = self._validated_tools()
        runtime = LocalToolRuntime(self.records, tools)
        searched = runtime.call("search_countries", {"query": "United"})
        related = runtime.call("list_indicator_observations_for_country", {"entity_id": searched[0]["entity_id"]})
        self.assertEqual(related[0]["country_id"], "USA")

    def test_discovery_tools_leave_full_details_for_lookup(self):
        records = [
            Record("model", "model-a", {"name": "Model A", "score": 10, "description": "Full details"}, "https://example.test/models"),
            Record("model", "model-b", {"name": "Model B", "score": 5, "description": "Other details"}, "https://example.test/models"),
        ]
        candidates, _ = ToolDesigner(LLMClient()).design(ResearchBundle("models", "test", "x", [], records, {}))
        runtime = LocalToolRuntime(records, candidates)
        rank_tool = next(tool for tool in candidates if tool.operation == "rank")
        compare_tool = next(tool for tool in candidates if tool.operation == "compare")
        ranked = runtime.call(rank_tool.name, {"limit": 2})
        comparison = runtime.call(compare_tool.name, {"left_id": "model-a", "right_id": "model-b"})
        self.assertNotIn("description", ranked[0])
        self.assertNotIn("left", comparison)
        self.assertEqual(comparison["winner_id"], "model-a")

    def test_join_records_compile_to_direct_bridge_tools(self):
        records = [
            Record("model", "model-a", {"name": "Model A"}, "https://example.test/models"),
            Record("file", "file-a", {"name": "weights.bin"}, "https://example.test/files"),
            Record("model_file_link", "model-a|file-a", {"model_id": "model-a", "file_id": "file-a"}, "https://example.test/model-a"),
        ]
        candidates, _mode = ToolDesigner(LLMClient()).design(ResearchBundle("models", "test", "x", [], records, {}))
        names = {tool.name for tool in candidates}
        self.assertNotIn("search_model_file_links", names)
        self.assertIn("list_files_for_model", names)
        self.assertIn("list_models_for_file", names)
        runtime = LocalToolRuntime(records, candidates)
        self.assertEqual(runtime.call("list_files_for_model", {"entity_id": "model-a", "limit": 3})[0]["entity_id"], "file-a")

    def test_tasks_are_created_only_from_executed_chains(self):
        _candidates, tools, _reports, _mode = self._validated_tools()
        walks = ToolGraph(tools).walks(count=12)
        tasks, mode, report = TaskSynthesizer(LLMClient()).synthesize("country indicators", tools, walks, self.records)
        self.assertEqual(mode, "awaiting_api_semantic_review")
        self.assertEqual(tasks, [])
        self.assertGreater(report["executed_walks"], 0)
        self.assertTrue(all("execution" in candidate for candidate in report["candidates"]))

    def test_semantic_review_batches_four_executed_chains_per_request(self):
        class BatchReviewLLM:
            enabled = True

            def __init__(self):
                self.calls = 0

            def complete_json(self, system, prompt):
                self.calls += 1
                data = json.loads(prompt)
                return json.dumps({"reviews": [{
                    "candidate_index": candidate["candidate_index"],
                    "keep_step_indices": list(range(len(candidate["executed_trace"]))),
                    "request": f"Answer grounded request {candidate['candidate_index']}",
                    "answer_slots": [{
                        "name": "result",
                        "description": "Requested result",
                        "step_indices": list(range(len(candidate["executed_trace"]))),
                    }],
                    "rubric": ["Use the executed evidence."],
                } for candidate in data["candidates"]]})

        _candidates, tools, _reports, _mode = self._validated_tools()
        reviewer = BatchReviewLLM()
        walks = ToolGraph(tools).walks(count=24)
        tasks, _mode, report = TaskSynthesizer(reviewer).synthesize("country indicators", tools, walks, self.records)
        self.assertEqual(reviewer.calls, report["semantic_review_requests"])
        self.assertEqual(reviewer.calls, (report["semantic_reviews"] + 3) // 4)
        self.assertEqual(len(tasks), report["semantic_reviews"])
        self.assertTrue(all(task.validation["reference_plan_executed"] for task in tasks))

    def test_execution_dedup_can_be_shared_across_batches(self):
        _candidates, tools, _reports, _mode = self._validated_tools()
        walks = ToolGraph(tools).walks(count=12, seed=9)
        synthesizer = TaskSynthesizer(LLMClient())
        seen: set[str] = set()
        _tasks, _mode, first = synthesizer.synthesize(
            "country indicators", tools, walks, self.records, seen_execution_signatures=seen,
        )
        _tasks, _mode, second = synthesizer.synthesize(
            "country indicators", tools, walks, self.records, seen_execution_signatures=seen,
        )
        self.assertGreater(first["executed_walks"], 0)
        self.assertEqual(second["executed_walks"], 0)
        self.assertEqual(second["rejected_walks"]["duplicate_candidate_execution"], len(walks))

    def test_invalid_configuration_is_rejected(self):
        candidates, _tools, _reports, _mode = self._validated_tools()
        candidates[0].search_fields = []
        _retained, reports = ToolValidator().validate(candidates, LocalToolRuntime(self.records, candidates))
        self.assertEqual(reports[0]["status"], "rejected")
        self.assertIn("search_has_no_text_fields", reports[0]["failures"])

    def test_tool_agent_can_reject_data_that_misses_the_core_capability(self):
        class RejectingLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                return json.dumps({
                    "usable_environment": False,
                    "keep_tool_names": [],
                    "missing_capabilities": ["placing a real order"],
                    "reason": "The data only describes products.",
                })

        designer = ToolDesigner(RejectingLLM())
        tools, mode = designer.design(self.bundle)
        self.assertEqual(mode, "data_grounded_agent_selection")
        self.assertEqual(tools, [])
        self.assertEqual(designer.last_selection_report["status"], "unusable_data")

    def test_tool_agent_accepts_string_false_from_json_model(self):
        class RejectingLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                return json.dumps({
                    "usable_environment": "false",
                    "keep_tool_names": ["search_countries"],
                    "missing_capabilities": ["placing a real order"],
                    "reason": "The data only describes products.",
                })

        designer = ToolDesigner(RejectingLLM())
        tools, _mode = designer.design(self.bundle)
        self.assertEqual(tools, [])
        self.assertEqual(designer.last_selection_report["status"], "unusable_data")

    def test_tool_agent_rejects_documentation_only_data_when_marked_unusable(self):
        class DocumentationOnlyLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                return json.dumps({
                    "usable_environment": False,
                    "keep_tool_names": [],
                    "missing_capabilities": ["retrieve live indicators"],
                    "reason": "Only documentation lookup is supported.",
                })

        bundle = ResearchBundle(
            "country indicators", "test", "x", [], self.records, {},
            theme_metadata={"documented_tools": [{"name": "retrieve_live_indicator"}]},
        )
        designer = ToolDesigner(DocumentationOnlyLLM())
        tools, _mode = designer.design(bundle)
        self.assertEqual(tools, [])
        self.assertEqual(designer.last_selection_report["status"], "unusable_data")

    def test_tool_agent_accepts_an_explicit_capability_to_retained_tool_pair(self):
        class DataBackedLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                return json.dumps({
                    "usable_environment": True,
                    "keep_tool_names": ["search_countries"],
                    "capability_support": [{
                        "mcp_tool_name": "find_country",
                        "retained_tool_names": ["search_countries"],
                    }],
                    "reason": "Country discovery is supported by real records.",
                })

        bundle = ResearchBundle(
            "country indicators", "test", "x", [], self.records, {},
            theme_metadata={"documented_tools": [{"name": "find_country"}]},
        )
        designer = ToolDesigner(DataBackedLLM())
        tools, _mode = designer.design(bundle)
        self.assertEqual([tool.name for tool in tools], ["search_countries"])
        self.assertEqual(designer.last_selection_report["supported_mcp_tools"], ["find_country"])

    def test_research_request_requires_real_web_tool_use(self):
        client = LLMClient()
        with patch.object(client, "_complete", return_value=("{}", {})) as complete:
            client.research_json("system", "prompt", max_tool_calls=6)
        payload = complete.call_args.args[0]
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["max_tool_calls"], 6)

    def test_research_fetch_uses_curl_when_python_tls_fails(self):
        completed = type("CurlResult", (), {"returncode": 0, "stdout": b'[{"id": 1}]'})()
        with patch("agent_world_mini.research.urlopen", side_effect=OSError("TLS failed")), \
             patch("agent_world_mini.research.shutil.which", return_value="curl"), \
             patch("agent_world_mini.research.subprocess.run", return_value=completed):
            content_type, text = WebResearchAgent(LLMClient())._fetch("https://example.test/data")
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(text), [{"id": 1}])

    def test_structured_json_rows_are_kept_by_code(self):
        class MappingLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                return json.dumps({"mappings": [{
                    "url": "https://example.test/models",
                    "path": "$.items",
                    "entity_type": "model",
                    "id_field": "modelId",
                }]})

        source = {
            "url": "https://example.test/models",
            "content_type": "application/json",
            "retrieved_excerpt": json.dumps({"items": [
                {"modelId": "model-a", "likes": 10},
                {"modelId": "model-b", "likes": 8},
                {"modelId": "model-c", "likes": 4},
            ]}),
        }
        records, report = WebResearchAgent(MappingLLM())._extract_sources("models", [source])
        self.assertEqual([record.entity_id for record in records], ["model-a", "model-b", "model-c"])
        self.assertEqual(report["structured_sources"], 1)

    def test_scoped_child_paths_are_unique_and_sampled(self):
        class MappingLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                return json.dumps({"mappings": [{
                    "url": "https://example.test/repos/a/tree",
                    "path": "$.items",
                    "entity_type": "file",
                    "id_field": "path",
                }]})

        source = {
            "url": "https://example.test/repos/a/tree",
            "content_type": "application/json",
            "retrieved_excerpt": json.dumps({"items": [{"path": f"part-{index}.bin", "size": index} for index in range(30)]}),
        }
        hint = {source["url"]: {"link_from": {"entity_type": "model", "entity_id": "repo/a"}}}
        records, _report = WebResearchAgent(MappingLLM())._extract_sources("models", [source], hints=hint)
        self.assertEqual(len(records), 20)
        self.assertEqual(records[0].entity_id, "model:repo/a:part-0.bin")
        self.assertEqual(records[0].attributes["local_id"], "part-0.bin")

    def test_root_json_array_is_kept_when_mapper_omits_it(self):
        class EmptyMappingLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                return json.dumps({"mappings": []})

        source = {
            "url": "https://example.test/api/models",
            "content_type": "application/json",
            "retrieved_excerpt": json.dumps([{"id": "model-a", "likes": 10}, {"id": "model-b", "likes": 8}]),
        }
        records, _report = WebResearchAgent(EmptyMappingLLM())._extract_sources("models", [source])
        self.assertEqual([(record.entity_type, record.entity_id) for record in records], [("model", "model-a"), ("model", "model-b")])

    def test_html_documentation_is_not_used_as_environment_state(self):
        source = {
            "url": "https://example.test/api-docs",
            "content_type": "text/html",
            "retrieved_excerpt": "This API can create orders and list customers.",
        }
        records, report = WebResearchAgent(LLMClient())._extract_sources("orders", [source])
        self.assertEqual(records, [])
        self.assertEqual(report["text_sources"], 0)

    def test_state_expansion_can_add_a_real_link(self):
        parent = Record("drug", "drug-a", {"name": "Drug A"}, "https://example.test/drugs")
        child = Record("ingredient", "ingredient-a", {"name": "Ingredient A"}, "https://example.test/ingredients")
        agent = WebResearchAgent(LLMClient())
        links = agent._link_records(
            [parent, child],
            [child],
            {child.source_url: {"link_from": {"entity_type": "drug", "entity_id": "drug-a"}}},
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].attributes, {"drug_id": "drug-a", "ingredient_id": "ingredient-a"})
        self.assertEqual(len(agent._relation_pairs([parent, child, *links])), 2)

    def test_graph_walks_execute_without_forced_length_or_template(self):
        records = [
            Record("subject", "science", {"name": "Science"}, "https://example.test/subject"),
            Record("work", "work-a", {"title": "A", "subject_id": "science", "author_id": "author-a", "edition_count": 12}, "https://example.test/works"),
            Record("work", "work-b", {"title": "B", "subject_id": "science", "author_id": "author-b", "edition_count": 9}, "https://example.test/works"),
            Record("author", "author-a", {"name": "Author A"}, "https://example.test/authors"),
            Record("author", "author-b", {"name": "Author B"}, "https://example.test/authors"),
        ]
        bundle = ResearchBundle("catalogue", "test", "x", [], records, {})
        candidates, _ = ToolDesigner(LLMClient()).design(bundle)
        tools, reports = ToolValidator().validate(candidates, LocalToolRuntime(records, candidates))
        self.assertTrue(all(report["status"] == "passed" for report in reports))
        walks = ToolGraph(tools).walks(count=48, seed=4)
        _tasks, mode, report = TaskSynthesizer(LLMClient()).synthesize("catalogue", tools, walks, records)
        self.assertEqual(mode, "awaiting_api_semantic_review")
        self.assertGreater(report["executed_walks"], 0)
        lengths = {len(candidate["calls"]) for candidate in report["candidates"]}
        self.assertGreater(len(lengths), 1)
        self.assertTrue(all(len({(call["tool"], tuple(sorted(call["arguments"].items()))) for call in candidate["causal_core"]}) == len(candidate["causal_core"]) for candidate in report["candidates"]))

    def test_connected_rich_data_can_naturally_produce_long_subgraphs(self):
        records: list[Record] = []
        for organization_index in range(2):
            organization_id = f"org-{organization_index}"
            records.append(Record("organization", organization_id, {"name": organization_id, "score": organization_index + 1}, "x"))
            for model_index in range(3):
                model_id = f"{organization_id}/model-{model_index}"
                records.append(Record("model", model_id, {"name": model_id, "organization_id": organization_id, "score": model_index + 1}, "x"))
                for file_index in range(2):
                    records.append(Record("file", f"{model_id}/file-{file_index}", {"name": f"file-{file_index}", "model_id": model_id, "size": file_index + 1}, "x"))
        for paper_index in range(3):
            records.append(Record("paper", f"paper-{paper_index}", {
                "title": f"Paper {paper_index}",
                "model_id": f"org-{paper_index % 2}/model-{paper_index % 3}",
                "citations": paper_index + 1,
            }, "x"))
        bundle = ResearchBundle("rich graph", "test", "x", [], records, {})
        candidates, _mode = ToolDesigner(LLMClient()).design(bundle)
        tools, _reports = ToolValidator().validate(candidates, LocalToolRuntime(records, candidates))
        runtime = LocalToolRuntime(records, tools)
        walks = ToolGraph(tools, runtime=runtime).walks(count=64, seed=7)
        _tasks, _mode, report = TaskSynthesizer(LLMClient()).synthesize("rich graph", tools, walks, records)
        lengths = [length for length, amount in report["causal_core_step_distribution"].items() for _ in range(amount)]
        self.assertGreaterEqual(max(lengths), 10)
        self.assertGreaterEqual(sum(lengths) / len(lengths), 5.5)
        self.assertLessEqual(max(lengths), 14)

    def test_graph_sampling_stays_on_connected_data_flow(self):
        _candidates, tools, _reports, _mode = self._validated_tools()
        graph = ToolGraph(tools)
        self.assertLess(len(graph.edges), len(tools) * (len(tools) - 1))
        self.assertTrue(any(edge["kind"] == "strong" and edge["weight"] == 3 for edge in graph.edges))
        self.assertFalse(any(edge["kind"] == "independent" for edge in graph.edges))
        self.assertFalse(any("independent" in walk.edge_kinds for walk in graph.walks(count=48)))
        self.assertEqual(graph.sampling_mode, "topology_expansion_without_target_length")

    def test_graph_relationships_come_from_verified_schema_flow(self):
        class MisclassifyingLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                raise AssertionError("graph construction should not ask an LLM to discard schema-backed edges")

        _candidates, tools, _reports, _mode = self._validated_tools()
        graph = ToolGraph(tools, MisclassifyingLLM())
        self.assertTrue(any(edge["kind"] == "strong" for edge in graph.edges))
        self.assertTrue(any(edge["kind"] == "weak" for edge in graph.edges))
        self.assertEqual(graph.construction_mode, "schema_data_flow")

    def test_causal_core_removes_independent_prefix_and_rebases_provenance(self):
        calls = [
            {"tool": "unrelated", "arguments": {}, "argument_provenance": {}},
            {"tool": "search", "arguments": {"query": "x"}, "argument_provenance": {"query": "user_seed"}},
            {"tool": "lookup", "arguments": {"entity_id": "x"}, "argument_provenance": {"entity_id": "observation:1"}},
        ]
        core = TaskSynthesizer._causal_core(calls)
        self.assertEqual([call["tool"] for call in core], ["search", "lookup"])
        self.assertEqual(core[1]["argument_provenance"]["entity_id"], "observation:0")

    def test_causal_core_keeps_two_branches_from_one_discovery(self):
        calls = [
            {"tool": "rank_models", "arguments": {"limit": 3}, "argument_provenance": {"limit": "task_constraint"}},
            {"tool": "files_for_model", "arguments": {"entity_id": "a"}, "argument_provenance": {"entity_id": "observation:0"}},
            {"tool": "files_for_model", "arguments": {"entity_id": "b"}, "argument_provenance": {"entity_id": "observation:0"}},
        ]
        core = TaskSynthesizer._causal_core(calls)
        self.assertEqual([call["tool"] for call in core], ["rank_models", "files_for_model", "files_for_model"])

    def test_comparison_ids_are_distinct_even_when_one_id_has_two_origins(self):
        compare = next(tool for tool in self._validated_tools()[1] if tool.operation == "compare")
        runtime = LocalToolRuntime(self.records, [compare])
        observations = [
            {"tool": "first", "result": {"entity_id": "USA", "entity_type": compare.entity_type}},
            {"tool": "second", "result": {"entity_id": "USA", "entity_type": compare.entity_type}},
        ]
        options = TaskSynthesizer(LLMClient())._argument_options(compare, runtime, observations, 0)
        self.assertTrue(options)
        self.assertTrue(all(arguments["left_id"] != arguments["right_id"] for arguments, _ in options))

    def test_relation_binding_backtracks_from_an_empty_parent(self):
        records = [
            Record("parent", "empty", {"name": "Empty"}, "x"),
            Record("parent", "full", {"name": "Full"}, "x"),
            Record("child", "child-a", {"name": "A", "parent_id": "full"}, "x"),
        ]
        tool = ToolSpec(
            name="list_children", description="x", inputs={"entity_id": "string", "limit": "integer"},
            outputs={"children": "child[]"}, reads=["parent", "child"], produces=["children"],
            operation="relation", entity_type="parent", related_entity_type="child", relation_field="parent_id",
            input_sources={"entity_id": "internal", "limit": "external"},
        )
        runtime = LocalToolRuntime(records, [tool])
        calls = TaskSynthesizer(LLMClient())._instantiate_walk([tool], runtime, 0)
        self.assertEqual(calls[0]["arguments"]["entity_id"], "full")

    def test_same_parent_leaf_growth_is_not_structural_progress(self):
        agent = WebResearchAgent(LLMClient())
        before = [
            Record("model", "model-a", {"name": "A"}, "x"),
            Record("file", "model-a/readme", {"name": "README"}, "x"),
            Record("model_file_link", "a|readme", {"model_id": "model-a", "file_id": "model-a/readme"}, "x"),
        ]
        after = [
            *before,
            Record("file", "model-a/config", {"name": "config"}, "x"),
            Record("model_file_link", "a|config", {"model_id": "model-a", "file_id": "model-a/config"}, "x"),
        ]
        gains = agent._coverage_gains(agent._state_summary(before), agent._state_summary(after))
        self.assertEqual(gains, ["file"])

    def test_verifier_accepts_concise_answer_with_tool_evidence(self):
        expected = {"reference_answer": {"repository": [{"entity_id": "repo/a", "entity_type": "repository"}]}}
        trace = [{"tool": "resolve", "arguments": {}, "result": {"entity_id": "repo/a", "entity_type": "repository"}}]
        judgment = FiveRunVerifier._judge({"request": "x"}, expected, trace, {"repository": "repo/a"})
        self.assertTrue(judgment["passed"])

    def test_verifier_does_not_require_every_reference_leaf(self):
        expected = {"reference_answer": {"system": [{
            "entity_id": "unified-medical-language-system",
            "entity_type": "knowledge_base",
            "description": "A long description",
            "created_year": 1986,
            "maintainer": "US National Library of Medicine",
        }]}}
        trace = [{"tool": "lookup", "arguments": {}, "result": expected["reference_answer"]["system"]}]
        judgment = FiveRunVerifier._judge(
            {"request": "x"}, expected, trace,
            {"system": "The Unified Medical Language System is maintained by the US National Library of Medicine."},
        )
        self.assertTrue(judgment["passed"])

    def test_verifier_rejects_the_wrong_reference_object(self):
        expected = {"reference_answer": {"repository": [{"entity_id": "repo/a", "entity_type": "repository"}]}}
        trace = [{"tool": "resolve", "arguments": {}, "result": {"entity_id": "repo/b", "entity_type": "repository"}}]
        judgment = FiveRunVerifier._judge({"request": "x"}, expected, trace, {"repository": "repo/b"})
        self.assertFalse(judgment["passed"])

    def test_five_run_stops_after_two_successes(self):
        llm = type("EnabledLLM", (), {"enabled": True})()
        verifier = FiveRunVerifier(llm, None, [])
        outcomes = iter([{"success": True}, {"success": True}])
        verifier._run_once = lambda task, expected, max_steps: next(outcomes)
        result = verifier.verify({}, {}, runs=5)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["attempted_runs"], 2)
        self.assertTrue(result["decided_early"])

    def test_five_run_stops_after_four_failures(self):
        llm = type("EnabledLLM", (), {"enabled": True})()
        verifier = FiveRunVerifier(llm, None, [])
        outcomes = iter([{"success": False}] * 4)
        verifier._run_once = lambda task, expected, max_steps: next(outcomes)
        result = verifier.verify({}, {}, runs=5)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["attempted_runs"], 4)
        self.assertTrue(result["decided_early"])

    def test_five_run_retries_infrastructure_errors_without_counting_them(self):
        llm = type("EnabledLLM", (), {"enabled": True})()
        verifier = FiveRunVerifier(llm, None, [])
        outcomes = iter([
            {"success": False, "infrastructure_error": True},
            {"success": True},
            {"success": True},
            {"success": False},
        ])
        verifier._run_once = lambda task, expected, max_steps: next(outcomes)
        result = verifier.verify({}, {}, runs=5)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["attempted_runs"], 3)
        self.assertEqual(result["infrastructure_retries"], 1)

    def test_themes_are_loaded_from_the_source_catalog(self):
        self.assertIn("github-community-triage", CURATED_THEME_SEEDS)
        seed = resolve_theme(None, source_url="https://example.test/mcp/hotel-booking")
        self.assertEqual(seed.source_url, "https://example.test/mcp/hotel-booking")
        self.assertEqual(seed.adapter, "generic_web")

    def test_catalog_skips_an_environment_already_in_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            old_run = output_root / "old"
            old_run.mkdir()
            (old_run / "theme_registry.json").write_text(json.dumps({
                "selected_theme": {
                    "seed_label": "Already Built",
                    "source_url": "https://smithery.ai/servers/example/already-built",
                }
            }), encoding="utf-8")
            listing = {
                "servers": [
                    {"qualifiedName": "example/already-built", "displayName": "Already Built", "description": "A complete existing business service with several useful operations."},
                    {"qualifiedName": "example/new", "displayName": "New Business", "description": "A new business service with public records and useful operations."},
                ],
                "pagination": {"totalPages": 1},
            }
            detail = {"tools": [{"name": "search_records"}, {"name": "get_record"}]}
            with patch("agent_world_mini.catalog._get_json", side_effect=[listing, detail]):
                selected, report = discover_smithery_themes(1, output_root, selection_seed=1)
            self.assertEqual([seed.seed_label for seed in selected], ["New Business"])
            self.assertEqual(report["skipped_existing_or_duplicate"], 1)

    def test_prepared_catalog_preserves_tool_schemas_and_is_selected_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "prepared.json"
            catalog.write_text(json.dumps({
                "environments": [{
                    "qualifiedName": "example/library",
                    "displayName": "Library",
                    "description": "Search and inspect a public library collection with documented records.",
                    "tools": [{
                        "name": "search_books",
                        "description": "Search books",
                        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                    }],
                }]
            }), encoding="utf-8")
            loaded = load_prepared_catalog(catalog)
            self.assertEqual(loaded[0].documented_tools[0]["inputSchema"]["properties"]["query"]["type"], "string")
            with patch("agent_world_mini.catalog._get_json", side_effect=AssertionError("batch selection must not use the network")):
                selected, report = select_prepared_themes(catalog, 1, root / "runs", selection_seed=1)
            self.assertEqual(selected[0].seed_label, "Library")
            self.assertEqual(report["selected"], 1)
