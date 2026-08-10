import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent_world_mini.catalog import discover_smithery_themes, load_prepared_catalog, select_prepared_themes
from agent_world_mini.graph import ToolGraph
from agent_world_mini.llm import LLMClient
from agent_world_mini.models import Record, ResearchBundle
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

    def test_tasks_are_created_only_from_executed_chains(self):
        _candidates, tools, _reports, _mode = self._validated_tools()
        walks = ToolGraph(tools).walks(count=12)
        tasks, mode, report = TaskSynthesizer(LLMClient()).synthesize("country indicators", tools, walks, self.records)
        self.assertEqual(mode, "awaiting_api_semantic_review")
        self.assertEqual(tasks, [])
        self.assertGreater(report["executed_walks"], 0)
        self.assertTrue(all("execution" in candidate for candidate in report["candidates"]))

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

    def test_tool_agent_rejects_tools_that_support_no_mcp_business_capability(self):
        class DocumentationOnlyLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                return json.dumps({
                    "usable_environment": True,
                    "supported_mcp_tool_names": [],
                    "keep_tool_names": ["search_countries"],
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

    def test_graph_keeps_data_flow_edges_and_implicit_independent_fallback(self):
        _candidates, tools, _reports, _mode = self._validated_tools()
        graph = ToolGraph(tools)
        self.assertLess(len(graph.edges), len(tools) * (len(tools) - 1))
        self.assertTrue(any(edge["kind"] == "strong" and edge["weight"] == 3 for edge in graph.edges))
        self.assertFalse(any(edge["kind"] == "independent" for edge in graph.edges))
        self.assertTrue(any("independent" in walk.edge_kinds for walk in graph.walks(count=48)))

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
