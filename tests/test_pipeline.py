import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_world_mini.seed_gen.catalog import discover_smithery_themes, load_prepared_catalog, prepare_smithery_catalog, select_prepared_themes
from agent_world_mini.task_gen.common.composition import pair_tasks
from agent_world_mini.env_gen.tool_gen.compiler import EnvironmentCompiler
from agent_world_mini.utils.search_agent.codex import CodexAgentClient
from agent_world_mini.utils.search_agent.deepseek_harness import DeepSeekHarnessResearchAgent
from agent_world_mini.task_gen.dag_form.graph import ToolGraph
from agent_world_mini.utils.io import write_json
from agent_world_mini.utils.llm import LLMClient
from agent_world_mini.utils import config as config_module
from agent_world_mini.task_gen.validation.luna_rollout import aggregate as aggregate_luna_rollouts
from agent_world_mini.task_gen.validation.luna_rollout import call as luna_call
from agent_world_mini.task_gen.validation.luna_rollout import finish as finish_luna_rollout
from agent_world_mini.task_gen.validation.luna_rollout import start as start_luna_rollout
from agent_world_mini.schemas.models import Record, ResearchBundle, ToolChain, ToolSpec
from agent_world_mini.run_pipeline import apply_luna_reviews, run
from agent_world_mini.runtime import LocalToolRuntime
from agent_world_mini.runtime.sessions import runtime_for_rollout
from agent_world_mini.utils.search_agent.web import WebResearchAgent
from agent_world_mini.task_gen.dag_form.synthesizer import TaskSynthesizer
from agent_world_mini.seed_gen.themes import CURATED_THEME_SEEDS, resolve_theme
from agent_world_mini.env_gen.tool_gen.designer import ToolDesigner, ToolValidator
from agent_world_mini.task_gen.validation.five_run import FiveRunVerifier


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

    def test_llm_initializes_openai_client_from_configuration(self):
        client = LLMClient(
            model="test-model",
            base_url="https://example.test/v1",
            api_key="test-key",
            timeout_seconds=1,
        )
        with patch("agent_world_mini.utils.llm.OpenAI") as openai:
            self.assertIs(client.client, openai.return_value)
        openai.assert_called_once_with(
            api_key="test-key",
            base_url="https://example.test/v1",
            timeout=1,
        )

    def test_llm_can_omit_json_response_format(self):
        client = LLMClient()
        with patch.object(
            client, "_create", return_value=("{}", {})
        ) as create:
            client.complete_json("system", "prompt", use_response_format=False)
        self.assertNotIn("response_format", create.call_args.kwargs)

    def test_llm_reads_openai_compatible_stream_incrementally(self):
        class FakeCompletions:
            def __init__(self):
                self.parameters = {}

            def create(self, **parameters):
                self.parameters = parameters
                return iter([
                    SimpleNamespace(
                        usage=None,
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="hel"))],
                    ),
                    SimpleNamespace(
                        usage=None,
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))],
                    ),
                    SimpleNamespace(
                        usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 2}),
                        choices=[],
                    ),
                ])

        deltas: list[str] = []
        client = LLMClient(
            model="test-model",
            base_url="https://example.test/v1",
            api_key="test-key",
        )
        completions = FakeCompletions()
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        content, usage = client._create([], on_delta=deltas.append)

        self.assertTrue(completions.parameters["stream"])
        self.assertEqual(
            completions.parameters["stream_options"],
            {"include_usage": True},
        )
        self.assertEqual(content, "hello")
        self.assertEqual(deltas, ["hel", "lo"])
        self.assertEqual(usage["total_tokens"], 2)

    def test_llm_environment_prefers_configured_openai_key_over_default_model(self):
        environment = {
            "OPENAI_API_KEY": "openai-test-key",
            "OPENAI_MODEL": "openai-test-model",
            # 配置示例可能预填模型名，但未填写 OpenRouter key。
            "OPENROUTER_MODEL": "openrouter-default-model",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "OPENAI_STREAM": "true",
        }
        with patch(
            "agent_world_mini.utils.llm.load_local_environment",
            return_value=environment,
        ):
            client = LLMClient.from_environment()

        self.assertEqual(client.model, "openai-test-model")
        self.assertEqual(client.base_url, "https://example.test/v1")
        self.assertTrue(client.stream)

    def test_local_api_key_file_does_not_override_injected_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            local_file = Path(temporary) / "api_keys.env"
            local_file.write_text(
                "OPENROUTER_API_KEY=local-key\nDEEPSEEK_API_KEY=deepseek-key\n",
                encoding="utf-8",
            )
            environment = {"OPENROUTER_API_KEY": "injected-key"}
            with patch.object(config_module, "LOCAL_API_KEYS_FILE", local_file), patch.object(
                config_module, "LEGACY_ENV_FILES", ()
            ):
                config_module.load_local_environment(environment)
            self.assertEqual(environment["OPENROUTER_API_KEY"], "injected-key")
            self.assertEqual(environment["DEEPSEEK_API_KEY"], "deepseek-key")

    def test_pipeline_can_continue_from_a_codex_research_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = root / "codex-research.json"
            bundle_path.write_text(json.dumps(self.bundle.to_dict()), encoding="utf-8")
            with patch("agent_world_mini.run_pipeline.LLMClient.from_environment", return_value=LLMClient()), patch(
                "agent_world_mini.env_gen.data_gen.generator.WebResearchAgent.gather"
            ) as gather:
                summary = run(None, root / "output", research_bundle=bundle_path, max_candidates=4)
            gather.assert_not_called()
            self.assertEqual(summary["records"], len(self.records))
            self.assertTrue((root / "output" / "tool_specs.json").is_file())

    def test_pipeline_can_use_deepseek_harness_for_research_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("agent_world_mini.run_pipeline.LLMClient.from_environment", return_value=LLMClient()), patch.object(
                DeepSeekHarnessResearchAgent, "gather", return_value=self.bundle
            ) as gather:
                summary = run("country indicators", root / "output", deepseek_harness=True, max_candidates=4)
            gather.assert_called_once()
            self.assertEqual(summary["records"], len(self.records))
            self.assertTrue((root / "output" / "tool_specs.json").is_file())

    def test_codex_agent_client_returns_final_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)

            def fake_run(command, **kwargs):
                final_path = Path(command[command.index("--output-last-message") + 1])
                final_path.write_text("research complete", encoding="utf-8")
                return SimpleNamespace(
                    returncode=0,
                    stdout="",
                    stderr="",
                )

            client = CodexAgentClient(
                executable="codex-test",
                enable_web_search=True,
                timeout_seconds=5,
            )
            with patch("agent_world_mini.utils.search_agent.codex.subprocess.run", side_effect=fake_run) as run_process:
                result = client.run(
                    "Research one environment",
                    working_directory=workspace,
                )

            command = run_process.call_args.args[0]
            self.assertLess(command.index("--search"), command.index("exec"))
            self.assertIn("--ephemeral", command)
            self.assertEqual(command[-1], "-")
            self.assertEqual(run_process.call_args.kwargs["input"], "Research one environment")
            self.assertEqual(result, "research complete")

    def test_codex_agent_client_accepts_its_own_model_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            client = CodexAgentClient(
                model="test-codex-model",
                base_url="https://example.test/v1",
                api_key="secret-test-key",
                executable="codex-test",
            )

            def fake_run(command, **kwargs):
                final_path = Path(command[command.index("--output-last-message") + 1])
                final_path.write_text("done", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch(
                "agent_world_mini.utils.search_agent.codex.subprocess.run",
                side_effect=fake_run,
            ) as run_process:
                self.assertEqual(client.run("Do the task", working_directory=workspace), "done")

            command = run_process.call_args.args[0]
            child_environment = run_process.call_args.kwargs["env"]
            self.assertEqual(command[command.index("--model") + 1], "test-codex-model")
            self.assertIn('model_provider="agent_world_llm"', command)
            self.assertIn(
                'model_providers.agent_world_llm.wire_api="responses"',
                command,
            )
            self.assertNotIn("secret-test-key", command)
            self.assertEqual(
                child_environment["AGENT_WORLD_LLM_API_KEY"],
                "secret-test-key",
            )

    def test_luna_handoff_replaces_backend_calls_and_replays_reviewed_tasks(self):
        class NoBackendCalls:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                raise AssertionError("Luna handoff must not call the configured API model")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = root / "research_bundle.json"
            bundle_path.write_text(json.dumps(self.bundle.to_dict()), encoding="utf-8")
            output = root / "output"
            with patch("agent_world_mini.run_pipeline.LLMClient.from_environment", return_value=NoBackendCalls()):
                summary = run(None, output, research_bundle=bundle_path, max_candidates=8, luna_review_export=True)
            self.assertEqual(summary["semantic_review_status"], "awaiting_luna_review")

            packet = json.loads((output / "luna_review_packet.json").read_text(encoding="utf-8"))
            self.assertTrue(packet["candidates"])
            candidate = packet["candidates"][0]
            indexes = list(range(len(candidate["causal_core"])))
            reviews_path = output / "luna_reviews.json"
            review_payload = {
                "usable_environment": True,
                "keep_tool_names": [tool["name"] for tool in packet["tool_contracts"]],
                "reason": "The locally validated tools support the reviewed task.",
                "reviews": [{
                    "candidate_id": candidate["candidate_id"],
                    "keep_step_indices": indexes,
                    "request": "Find and report the requested grounded result from the available records.",
                    "answer_slots": [{
                        "name": "result",
                        "description": "The requested grounded result.",
                        "step_indices": indexes,
                    }],
                    "rubric": ["Use the observed result."],
                }],
            }
            reviews_path.write_text(json.dumps(review_payload), encoding="utf-8")
            with patch("agent_world_mini.run_pipeline.LLMClient.from_environment", return_value=NoBackendCalls()):
                completed = apply_luna_reviews(output, reviews_path)
            self.assertEqual(completed["successful_tasks"], 1)
            task_payload = json.loads((output / "tasks.json").read_text(encoding="utf-8"))
            self.assertTrue(task_payload["tasks"][0]["validation"]["reference_plan_executed"])
            self.assertEqual(
                {tool["name"] for tool in task_payload["tasks"][0]["available_tools"]},
                set(review_payload["keep_tool_names"]),
            )

    def test_luna_review_cannot_restore_an_unselected_tool_call(self):
        _candidates, tools, _reports, _mode = self._validated_tools()
        synthesizer = TaskSynthesizer(LLMClient())
        _tasks, _mode, report = synthesizer.synthesize(
            self.bundle.theme, tools, ToolGraph(tools).walks(count=8), self.records, candidate_only=True,
        )
        packet = TaskSynthesizer.luna_review_packet(self.bundle, tools, {"batches": [report]})
        candidate = packet["candidates"][0]
        used = {call["tool"] for call in candidate["causal_core"]}
        selected = [tool for tool in tools if tool.name not in used]
        indexes = list(range(len(candidate["causal_core"])))
        tasks, imported = synthesizer.tasks_from_luna_reviews(selected, self.records, packet, {"reviews": [{
            "candidate_id": candidate["candidate_id"],
            "keep_step_indices": indexes,
            "request": "Return the grounded result.",
            "answer_slots": [{"name": "result", "description": "Result", "step_indices": indexes}],
        }]})
        self.assertEqual(tasks, [])
        self.assertEqual(imported["rejected_reviews"], 1)

    def test_luna_five_run_bridge_executes_calls_and_aggregates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "environment"
            output.mkdir()
            tools = self._validated_tools()[1]
            synthesizer = TaskSynthesizer(LLMClient())
            _empty, _mode, report = synthesizer.synthesize(
                self.bundle.theme, tools, ToolGraph(tools).walks(count=8), self.records, candidate_only=True,
            )
            packet = TaskSynthesizer.luna_review_packet(self.bundle, tools, {"batches": [report]})
            candidate = packet["candidates"][0]
            indexes = list(range(len(candidate["causal_core"])))
            reviewed, _imported = synthesizer.tasks_from_luna_reviews(tools, self.records, packet, {"reviews": [{
                "candidate_id": candidate["candidate_id"],
                "keep_step_indices": indexes,
                "request": "Return the grounded result for the requested country indicator.",
                "answer_slots": [{"name": "result", "description": "Result", "step_indices": indexes}],
            }]})
            write_json(output / "research_bundle.json", self.bundle.to_dict())
            write_json(output / "tool_specs.json", {"tools": [tool.to_dict() for tool in tools]})
            write_json(output / "tasks.json", {
                "generation_mode": "test",
                "tasks": [reviewed[0].to_dict()],
                "rejected_tasks": [],
                "inconclusive_tasks": [],
            })
            write_json(output / "summary.json", {})
            task = reviewed[0].to_dict()
            for run_id in range(1, 6):
                public = start_luna_rollout(output, task["task_id"], run_id)
                self.assertNotIn("reference_execution", public)
                for reference_call in task["validation"]["reference_calls"]:
                    luna_call(output, task["task_id"], run_id, reference_call["tool"], reference_call["arguments"])
                outcome = finish_luna_rollout(
                    output, task["task_id"], run_id, task["reference_execution"]["reference_answer"],
                )
                self.assertTrue(outcome["success"])
            aggregate = aggregate_luna_rollouts(output)
            self.assertEqual(aggregate["tasks_passed"], 1)
            verified = json.loads((output / "tasks.json").read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(verified["validation"]["five_run_verification"]["successes"], 2)
            self.assertEqual(verified["validation"]["five_run_verification"]["attempted_runs"], 2)
            self.assertTrue(verified["validation"]["five_run_verification"]["decided_early"])

            repeated = aggregate_luna_rollouts(output)
            self.assertEqual(repeated, aggregate)

    def test_related_task_composition_requires_shared_real_entities(self):
        def task(task_id, request, calls, entity_ids):
            return {
                "task_id": task_id,
                "request": request,
                "validation": {"reference_calls": calls},
                "reference_execution": {"trace": [{"result": [{"entity_id": value} for value in entity_ids]}]},
            }

        left = task("task_001", "Describe company A.", [
            {"tool": "search_companies", "arguments": {"query": "Company A"}},
            {"tool": "get_company", "arguments": {"entity_id": "company-a"}},
        ], ["company-a"])
        right = task("task_002", "Report company A filings.", [
            {"tool": "search_companies", "arguments": {"query": "Company A"}},
            {"tool": "list_filings", "arguments": {"entity_id": "company-a"}},
        ], ["company-a", "filing-a"])
        unrelated = task("task_003", "Describe company B.", [
            {"tool": "search_companies", "arguments": {"query": "Company B"}},
            {"tool": "get_company", "arguments": {"entity_id": "company-b"}},
        ], ["company-b"])
        pairs = pair_tasks([left, right, unrelated])
        self.assertEqual(len(pairs), 1)
        self.assertEqual([item["task_id"] for item in pairs[0][:2]], ["task_001", "task_002"])
        self.assertEqual(len(pairs[0][2]), 3)

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

    def test_identifier_numbers_do_not_create_rank_or_compare_tools(self):
        records = [
            Record("user", "user-a", {"name": "A", "user_number": 10}, "https://example.test/users"),
            Record("user", "user-b", {"name": "B", "user_number": 20}, "https://example.test/users"),
        ]
        candidates, _ = ToolDesigner(LLMClient()).design(ResearchBundle("users", "test", "x", [], records, {}))
        self.assertFalse(any(tool.operation in {"rank", "compare"} for tool in candidates))

    def test_runtime_crud_and_reset_share_one_local_state(self):
        tools = [
            ToolSpec("create_job", "Create a job", {"name": "string"}, {"job": "job"}, [], ["job"], mutates_state=True, operation="create", entity_type="job", writes=["job"]),
            ToolSpec("update_job", "Update a job", {"entity_id": "string", "status": "string"}, {"job": "job"}, ["job"], ["job"], mutates_state=True, operation="update", entity_type="job", writes=["job"]),
            ToolSpec("delete_job", "Delete a job", {"entity_id": "string"}, {"deleted": "boolean"}, ["job"], ["deletion"], mutates_state=True, operation="delete", entity_type="job", writes=["job"]),
        ]
        runtime = LocalToolRuntime(self.bundle, tools)
        created = runtime.call("create_job", {"name": "Analysis"})
        updated = runtime.call("update_job", {"entity_id": created["entity_id"], "status": "complete"})
        self.assertEqual(updated["status"], "complete")
        runtime.call("delete_job", {"entity_id": created["entity_id"]})
        self.assertEqual(runtime.rows_for("job"), [])
        runtime.reset()
        self.assertEqual(runtime.rows_for("job"), [])

    def test_outcome_keeps_a_create_then_delete_event(self):
        tools = [
            ToolSpec("create_job", "Create", {"name": "string"}, {"job": "job"}, [], ["job"], operation="create", entity_type="job"),
            ToolSpec("delete_job", "Delete", {"entity_id": "string"}, {"deleted": "boolean"}, ["job"], ["deletion"], operation="delete", entity_type="job"),
        ]
        runtime = LocalToolRuntime(self.bundle, tools)
        initial = runtime.snapshot()
        created = runtime.call("create_job", {"name": "Temporary"})
        runtime.call("delete_job", {"entity_id": created["entity_id"]})
        outcome = runtime.outcome(initial, runtime.snapshot())
        self.assertEqual(outcome, {"events": [{
            "operation": "delete", "entity_type": "job", "entity_id": created["entity_id"],
        }]})
        self.assertTrue(runtime.check_outcome(outcome)["passed"])
        self.assertFalse(LocalToolRuntime(self.bundle, tools).check_outcome(outcome)["passed"])

    def test_runtime_uses_overlay_seed_and_restores_it_after_reset(self):
        bundle = ResearchBundle(
            "jobs", "test", "x", [], self.records, {},
            overlay_seed=[{"entity_type": "job", "entity_id": "job-1", "attributes": {"status": "queued"}}],
        )
        tool = ToolSpec(
            "update_job", "Update a job", {"entity_id": "string", "status": "string"}, {"job": "job"},
            ["job"], ["job"], mutates_state=True, operation="update", entity_type="job", writes=["job"],
        )
        runtime = LocalToolRuntime(bundle, [tool])
        runtime.call("update_job", {"entity_id": "job-1", "status": "complete"})
        self.assertEqual(runtime.get_row("job", "job-1")["status"], "complete")
        runtime.reset()
        self.assertEqual(runtime.get_row("job", "job-1")["status"], "queued")

    def test_runtime_copies_reads_and_writes_real_resource_files(self):
        bundle = ResearchBundle(
            "files", "test", "x", [], self.records, {},
            resources=[{
                "resource_id": "manifest", "name": "manifest.json", "source_url": "https://example.test/manifest.json",
                "content": {"datasets": [{"name": "A"}]},
            }],
        )
        tools = [
            ToolSpec("download_manifest", "Download", {}, {"path": "string"}, [], ["file_path"], operation="copy_resource", config={"resource_id": "manifest"}),
            ToolSpec("read_manifest", "Read", {"path": "string"}, {"data": "object"}, ["file_path"], ["data"], operation="read_file"),
            ToolSpec("write_report", "Write", {"path": "string", "content": "object"}, {"path": "string"}, ["data"], ["report_path"], mutates_state=True, operation="write_file", writes=["file:report"]),
        ]
        runtime = LocalToolRuntime(bundle, tools)
        copied = runtime.call("download_manifest", {})
        data = runtime.call("read_manifest", {"path": copied["path"]})
        self.assertEqual(data["datasets"][0]["name"], "A")
        runtime.call("write_report", {"path": "report.json", "content": {"count": 1}})
        self.assertEqual(runtime.read_file("files/report.json")["count"], 1)
        runtime.reset()
        self.assertEqual(runtime.snapshot()["files"], [])

    def test_runtime_executes_environment_python_handler(self):
        tool = ToolSpec(
            "summarize_jobs", "Summarize jobs", {}, {"counts": "object"}, ["job"], ["counts"],
            backend="python",
            implementation="def run(context, arguments):\n    return {'count': len(context.rows_for('job'))}\n",
        )
        bundle = ResearchBundle(
            "jobs", "test", "x", [], self.records, {},
            overlay_seed=[{"entity_type": "job", "entity_id": "job-1", "attributes": {"status": "queued"}}],
        )
        self.assertEqual(LocalToolRuntime(bundle, [tool]).call("summarize_jobs", {}), {"count": 1})

    def test_runtime_session_follows_rollout_lifetime(self):
        tools = [
            ToolSpec("create_job", "Create", {"name": "string"}, {"job": "job"}, [], ["job"], mutates_state=True, operation="create", entity_type="job"),
            ToolSpec("get_job", "Get", {"entity_id": "string"}, {"job": "job"}, ["job"], ["job"], operation="lookup", entity_type="job"),
        ]
        first_rollout = type("Rollout", (), {})()
        first = runtime_for_rollout(first_rollout, "environment", lambda: LocalToolRuntime(self.bundle, tools))
        second = runtime_for_rollout(first_rollout, "environment", lambda: LocalToolRuntime(self.bundle, tools))
        created = first.call("create_job", {"name": "Shared"})
        self.assertEqual(second.call("get_job", {"entity_id": created["entity_id"]})["name"], "Shared")
        next_rollout = type("Rollout", (), {})()
        fresh = runtime_for_rollout(next_rollout, "environment", lambda: LocalToolRuntime(self.bundle, tools))
        self.assertEqual(fresh.rows_for("job"), [])

    def test_environment_compiler_builds_valid_crud_and_resource_tools(self):
        bundle = ResearchBundle(
            "analysis workspace", "test", "x", [], self.records, {},
            theme_metadata={"environment_blueprint": {
                "mutable_entities": [{
                    "entity_type": "analysis_job",
                    "description": "analysis job",
                    "fields": {
                        "name": {"type": "string", "example": "Population report"},
                        "status": {"type": "string", "example": "queued"},
                    },
                    "operations": ["create", "read", "update", "delete"],
                    "update_fields": ["status"],
                }],
                "python_tools": [],
            }},
            resources=[{
                "resource_id": "population_manifest", "name": "population.json",
                "media_type": "application/json", "source_url": "https://example.test/population.json",
                "content": {"countries": 2},
            }],
        )
        compiler = EnvironmentCompiler(LLMClient())
        compiler.prepare(bundle, use_agent=False)
        candidates = compiler.compile_tools(bundle)
        runtime = LocalToolRuntime(bundle, candidates)
        retained, reports = ToolValidator().validate(candidates, runtime)
        self.assertTrue(all(report["status"] == "passed" for report in reports), reports)
        self.assertEqual(
            {tool.name for tool in retained},
            {"create_analysis_job", "get_analysis_job", "update_analysis_job", "delete_analysis_job", "download_population_json", "read_population_json"},
        )

    def test_environment_compiler_agent_plan_is_theme_agnostic_and_executable(self):
        class PlanningLLM:
            enabled = True

            @staticmethod
            def complete_json(system, prompt):
                packet = json.loads(prompt)
                self.assertNotIn("github", system.lower())
                self.assertEqual(packet["environment"], "scientific files")
                return json.dumps({
                    "mutable_entities": [{
                        "entity_type": "report",
                        "fields": {"title": {"type": "string", "example": "Summary"}},
                        "operations": ["create", "read"],
                    }],
                    "python_tools": [{
                        "name": "count_datasets",
                        "description": "Count datasets in the downloaded manifest.",
                        "inputs": {"path": "string"},
                        "outputs": {"count": "integer"},
                        "reads": ["file_path"],
                        "produces": ["dataset_count"],
                        "requires_tools": ["download_manifest_json"],
                        "input_bindings": {"path": "download_manifest_json.path"},
                        "input_sources": {"path": "internal"},
                        "implementation": "def run(context, arguments):\n    return {'count': len(context.read_file(arguments['path'])['datasets'])}\n",
                        "test_cases": [{
                            "setup_calls": [{"tool": "download_manifest_json", "arguments": {}}],
                            "arguments": {"path": "files/manifest.json"},
                            "expect_nonempty": True,
                        }],
                    }],
                })

        bundle = ResearchBundle(
            "scientific files", "test", "x", [], self.records, {},
            theme_metadata={"documented_tools": [{"name": "download_manifest", "description": "Download a manifest"}]},
            resources=[{
                "resource_id": "manifest", "name": "manifest.json", "media_type": "application/json",
                "source_url": "https://example.test/manifest.json", "content": {"datasets": ["a", "b"]},
            }],
        )
        compiler = EnvironmentCompiler(PlanningLLM())
        compiler.prepare(bundle)
        candidates = compiler.compile_tools(bundle)
        retained, reports = ToolValidator().validate(candidates, LocalToolRuntime(bundle, candidates))
        self.assertTrue(all(report["status"] == "passed" for report in reports), reports)
        custom = next(tool for tool in retained if tool.name == "count_datasets")
        runtime = LocalToolRuntime(bundle, retained)
        downloaded = runtime.call("download_manifest_json", {})
        self.assertEqual(runtime.call(custom.name, {"path": downloaded["path"]}), {"count": 2})
        graph = ToolGraph(retained, runtime=runtime)
        self.assertTrue(any(
            edge["from"] == "download_manifest_json"
            and edge["to"] == "count_datasets"
            and edge["kind"] == "strong"
            for edge in graph.edges
        ))

    def test_stateful_compiled_tools_form_and_execute_one_clean_chain(self):
        bundle = ResearchBundle(
            "analysis workspace", "test", "x", [], self.records, {},
            theme_metadata={"environment_blueprint": {
                "mutable_entities": [{
                    "entity_type": "analysis_job",
                    "fields": {
                        "name": {"type": "string", "example": "Population report"},
                        "status": {"type": "string", "example": "queued", "update_example": "complete"},
                    },
                    "operations": ["create", "read", "update", "delete"],
                    "update_fields": ["status"],
                }],
                "python_tools": [],
            }},
        )
        compiler = EnvironmentCompiler(LLMClient())
        compiler.prepare(bundle, use_agent=False)
        candidates = compiler.compile_tools(bundle)
        tools, reports = ToolValidator().validate(candidates, LocalToolRuntime(bundle, candidates))
        self.assertTrue(all(report["status"] == "passed" for report in reports), reports)
        graph = ToolGraph(tools, runtime=LocalToolRuntime(bundle, tools))
        edges = {(edge["from"], edge["to"]) for edge in graph.edges}
        self.assertIn(("create_analysis_job", "update_analysis_job"), edges)
        self.assertIn(("update_analysis_job", "get_analysis_job"), edges)
        self.assertNotIn(("delete_analysis_job", "get_analysis_job"), edges)

        walk = ToolChain(
            ["create_analysis_job", "update_analysis_job", "get_analysis_job"],
            ["analysis_job"],
        )
        tasks, mode, report = TaskSynthesizer(LLMClient()).synthesize(
            bundle.theme, tools, [walk], bundle, candidate_only=True,
        )
        self.assertEqual(tasks, [])
        self.assertEqual(mode, "awaiting_luna_semantic_review")
        self.assertEqual(report["executed_walks"], 1)
        execution = report["candidates"][0]["execution"]
        jobs = [row for row in execution["final_state"]["records"] if row["entity_type"] == "analysis_job"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "complete")
        review = {
            "request": "Create a population analysis, mark it complete, and return the resulting job.",
            "keep_step_indices": [0, 1, 2],
            "answer_slots": [{"name": "job", "description": "The resulting job", "step_indices": [0, 1, 2]}],
            "rubric": ["The job is complete."],
        }
        task = TaskSynthesizer(LLMClient())._task_from_review(
            tools, LocalToolRuntime(bundle, tools), report["candidates"][0], 1, review,
        )
        self.assertIsNotNone(task)
        self.assertEqual(task.validation["outcome"]["created"][0]["status"], "complete")
        replay = LocalToolRuntime(bundle, tools)
        replay.execute(task.validation["reference_calls"])
        self.assertTrue(replay.check_outcome(task.validation["outcome"])["passed"])

    def test_stateful_environment_runs_through_luna_handoff_end_to_end(self):
        bundle = ResearchBundle(
            "local analysis jobs", "codex_research_agent", "x", [], self.records, {},
            theme_metadata={
                "theme_id": "analysis-jobs",
                "environment_blueprint": {
                    "mutable_entities": [{
                        "entity_type": "analysis_job",
                        "fields": {
                            "name": {"type": "string", "example": "Population report"},
                            "status": {"type": "string", "example": "queued", "update_example": "complete"},
                        },
                        "operations": ["create", "read", "update", "delete"],
                        "update_fields": ["status"],
                    }],
                    "python_tools": [],
                },
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = root / "bundle.json"
            bundle_path.write_text(json.dumps(bundle.to_dict()), encoding="utf-8")
            output = root / "output"
            with patch("agent_world_mini.run_pipeline.LLMClient.from_environment", return_value=LLMClient()):
                summary = run(None, output, research_bundle=bundle_path, max_candidates=64, luna_review_export=True)
            self.assertGreaterEqual(summary["state_mutating_tools"], 3)
            packet = json.loads((output / "luna_review_packet.json").read_text(encoding="utf-8"))
            candidate = next(
                item for item in packet["candidates"]
                if {"create_analysis_job", "update_analysis_job"} <= {step["tool"] for step in item["causal_core"]}
            )
            steps = [
                index for index, step in enumerate(candidate["causal_core"])
                if step["tool"] in {"create_analysis_job", "update_analysis_job"}
            ]
            tool_names = [tool["name"] for tool in packet["tool_contracts"]]
            reviews = {
                "usable_environment": True,
                "keep_tool_names": tool_names,
                "reviews": [{
                    "candidate_id": candidate["candidate_id"],
                    "keep_step_indices": steps,
                    "request": "Create a population analysis job, complete it, and return the resulting state.",
                    "answer_slots": [{"name": "result", "description": "The completed local job", "step_indices": steps}],
                    "rubric": ["The local job was created and completed."],
                }],
            }
            reviews_path = output / "reviews.json"
            reviews_path.write_text(json.dumps(reviews), encoding="utf-8")
            with patch("agent_world_mini.run_pipeline.LLMClient.from_environment", return_value=LLMClient()):
                imported = apply_luna_reviews(output, reviews_path)
            self.assertEqual(imported["successful_tasks"], 1)
            task = json.loads((output / "tasks.json").read_text(encoding="utf-8"))["tasks"][0]
            self.assertTrue(task["validation"]["outcome"])
            runtime_payload = json.loads((output / "research_bundle.json").read_text(encoding="utf-8"))
            tools_payload = json.loads((output / "tool_specs.json").read_text(encoding="utf-8"))["tools"]
            runtime = LocalToolRuntime.from_dict({**runtime_payload, "tools": tools_payload})
            runtime.execute(task["validation"]["reference_calls"])
            self.assertTrue(runtime.check_outcome(task["validation"]["outcome"])["passed"])

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

    def test_tool_agent_retries_an_unexplained_empty_selection_once(self):
        class FlakyLLM:
            enabled = True
            calls = 0

            @classmethod
            def complete_json(cls, system, prompt):
                cls.calls += 1
                if cls.calls == 1:
                    return json.dumps({"usable_environment": True, "keep_tool_names": []})
                return json.dumps({"usable_environment": True, "keep_tool_names": ["search_countries"]})

        designer = ToolDesigner(FlakyLLM())
        tools, _mode = designer.design(self.bundle)
        self.assertEqual([tool.name for tool in tools], ["search_countries"])
        self.assertEqual(FlakyLLM.calls, 2)

    def test_research_request_requires_real_web_tool_use(self):
        class FakeCompletions:
            def __init__(self):
                self.parameters = {}

            def create(self, **parameters):
                self.parameters = parameters
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="{}", annotations=[])
                    )],
                    usage=SimpleNamespace(model_dump=lambda: {}),
                )

        client = LLMClient(
            model="test-model",
            base_url="https://example.test/v1",
            api_key="test-key",
        )
        completions = FakeCompletions()
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        agent = WebResearchAgent(client)
        agent._research_json("system", "prompt", max_tool_calls=6)
        self.assertEqual(completions.parameters["tool_choice"], "required")
        self.assertEqual(completions.parameters["extra_body"]["max_tool_calls"], 6)

    def test_research_fetch_uses_curl_when_python_tls_fails(self):
        completed = type("CurlResult", (), {"returncode": 0, "stdout": b'[{"id": 1}]'})()
        with patch("agent_world_mini.utils.search_agent.web.urlopen", side_effect=OSError("TLS failed")), \
             patch("agent_world_mini.utils.search_agent.web.shutil.which", return_value="curl"), \
             patch("agent_world_mini.utils.search_agent.web.subprocess.run", return_value=completed):
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

    def test_walk_includes_all_declared_prerequisites(self):
        tools = [
            ToolSpec("create_note", "Create note", {}, {}, [], ["note_id"]),
            ToolSpec("create_collection", "Create collection", {}, {}, [], ["collection_id"]),
            ToolSpec(
                "add_note", "Add note", {"note_id": "string", "collection_id": "string"}, {}, [], ["membership"],
                requires_tools=["create_note", "create_collection"],
                input_bindings={"note_id": "create_note.entity_id", "collection_id": "create_collection.entity_id"},
                input_sources={"note_id": "internal", "collection_id": "internal"},
            ),
        ]
        walks = ToolGraph(tools).walks(count=32)
        linked = [walk.tool_names for walk in walks if "add_note" in walk.tool_names]
        self.assertTrue(linked)
        for names in linked:
            link_index = names.index("add_note")
            self.assertIn("create_note", names[:link_index])
            self.assertIn("create_collection", names[:link_index])

    def test_walk_continues_through_same_entity_state_steps(self):
        tools = [
            ToolSpec("create_note", "Create", {}, {}, [], ["note_id"], operation="create", entity_type="note"),
            ToolSpec(
                "trash_note", "Trash", {"note_id": "string"}, {}, ["note_status"], ["trashed_note"],
                requires_tools=["create_note"], input_bindings={"note_id": "create_note.entity_id"},
                input_sources={"note_id": "internal"}, writes=["note_status"], operation="python",
            ),
            ToolSpec(
                "restore_note", "Restore", {"note_id": "string"}, {}, ["note_status"], ["restored_note"],
                requires_tools=["trash_note"], input_bindings={"note_id": "trash_note.note.entity_id"},
                input_sources={"note_id": "internal"}, writes=["note_status"], operation="python",
            ),
        ]
        walks = ToolGraph(tools).walks(count=32)
        restored = [walk.tool_names for walk in walks if "restore_note" in walk.tool_names]
        self.assertTrue(restored)
        for names in restored:
            self.assertLess(names.index("create_note"), names.index("trash_note"))
            self.assertLess(names.index("trash_note"), names.index("restore_note"))

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

    def test_causal_core_keeps_state_dependency_without_argument_binding(self):
        tools = {
            "write_report": ToolSpec("write_report", "Write", {}, {}, [], [], writes=["report"]),
            "summarize_report": ToolSpec("summarize_report", "Summarize", {}, {}, ["report"], []),
        }
        calls = [
            {"tool": "write_report", "arguments": {}, "argument_provenance": {}},
            {"tool": "summarize_report", "arguments": {}, "argument_provenance": {}},
        ]
        core = TaskSynthesizer._causal_core(calls, tools)
        self.assertEqual([call["tool"] for call in core], ["write_report", "summarize_report"])

    def test_causal_core_does_not_join_different_state_objects(self):
        tools = {
            "update_note": ToolSpec("update_note", "Update", {"note_id": "string"}, {}, ["note"], [], writes=["note"]),
            "trash_note": ToolSpec("trash_note", "Trash", {"note_id": "string"}, {}, ["note"], []),
        }
        calls = [
            {"tool": "update_note", "arguments": {"note_id": "note-a"}, "argument_provenance": {"note_id": "task_constraint"}},
            {"tool": "trash_note", "arguments": {"note_id": "note-b"}, "argument_provenance": {"note_id": "task_constraint"}},
        ]
        core = TaskSynthesizer._causal_core(calls, tools)
        self.assertEqual([call["tool"] for call in core], ["trash_note"])

    def test_causal_core_keeps_declared_prerequisite_without_argument_binding(self):
        tools = {
            "download": ToolSpec("download", "Download", {}, {}, [], ["file"]),
            "analyze": ToolSpec("analyze", "Analyze", {}, {}, [], ["result"], requires_tools=["download"]),
        }
        calls = [
            {"tool": "download", "arguments": {}, "argument_provenance": {}},
            {"tool": "analyze", "arguments": {}, "argument_provenance": {}},
        ]
        core = TaskSynthesizer._causal_core(calls, tools)
        self.assertEqual([call["tool"] for call in core], ["download", "analyze"])

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

    def test_research_preserves_small_real_json_and_text_resources(self):
        resources = WebResearchAgent._resources_from_sources([
            {
                "url": "https://example.test/manifest.json", "content_type": "application/json",
                "retrieved_excerpt": '{"datasets":[{"name":"A"}]}',
            },
            {
                "url": "https://example.test/notes.txt", "content_type": "text/plain",
                "retrieved_excerpt": "Real source notes",
            },
            {
                "url": "https://example.test/page", "content_type": "text/html",
                "retrieved_excerpt": "An overview page",
            },
        ])
        self.assertEqual([item["name"] for item in resources], ["manifest.json", "notes.txt"])
        self.assertEqual(resources[0]["content"]["datasets"][0]["name"], "A")

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

    def test_verifier_ignores_unrequested_discovery_rows_and_internal_ids(self):
        expected = {"reference_answer": {"summaries": [[
            {"entity_id": "faers:warfarin", "dataset": "FDA FAERS", "drug_id": "rxnorm:11289"},
            {"entity_id": "faers:aspirin", "dataset": "FDA FAERS", "drug_id": "rxnorm:1191"},
            {"entity_id": "faers:metformin", "dataset": "FDA FAERS", "drug_id": "rxnorm:6809"},
        ]]}}
        trace = [{"tool": "search", "arguments": {"query": "warfarin"}, "result": expected["reference_answer"]["summaries"][0]}]
        judgment = FiveRunVerifier._judge(
            {"request": "Compare warfarin and aspirin adverse event summaries."}, expected, trace,
            {"summaries": [{"drug": "warfarin"}, {"drug": "aspirin"}]},
        )
        self.assertTrue(judgment["passed"])

    def test_verifier_preview_handles_non_mapping_list_items(self):
        self.assertEqual(FiveRunVerifier._preview(["a"] * 9)[-1], {"truncated": 1})

    def test_verifier_normalizes_equivalent_tool_call_shapes(self):
        tools = {"search_chemicals"}
        expected = {
            "kind": "call",
            "call": {"tool": "search_chemicals", "arguments": {"query": "benzene"}},
        }
        self.assertEqual(
            FiveRunVerifier._normalize_action(
                {"tool": "search_chemicals", "arguments": {"query": "benzene"}}, tools, set(), False
            ),
            expected,
        )
        self.assertEqual(
            FiveRunVerifier._normalize_action(
                {"call": {"name": "search_chemicals", "parameters": {"query": "benzene"}}}, tools, set(), False
            ),
            expected,
        )

    def test_verifier_normalizes_equivalent_final_answer_shapes(self):
        answer = {"chemical": {"name": "Benzene"}}
        self.assertEqual(
            FiveRunVerifier._normalize_action({"final_answer": answer}, set(), {"chemical"}, True),
            {"kind": "final", "answer": answer},
        )
        self.assertEqual(
            FiveRunVerifier._normalize_action(answer, set(), {"chemical"}, True),
            {"kind": "final", "answer": answer},
        )

    def test_verifier_does_not_treat_an_unknown_action_as_an_answer(self):
        self.assertIsNone(
            FiveRunVerifier._normalize_action(
                {"tool": "made_up", "arguments": {}}, {"search_chemicals"}, {"chemical"}, True
            )
        )

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
            with patch("agent_world_mini.seed_gen.catalog._get_json", side_effect=[listing, detail]):
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
            with patch("agent_world_mini.seed_gen.catalog._get_json", side_effect=AssertionError("batch selection must not use the network")):
                selected, report = select_prepared_themes(catalog, 1, root / "runs", selection_seed=1)
            self.assertEqual(selected[0].seed_label, "Library")
            self.assertEqual(report["selected"], 1)

    def test_prepared_catalog_deduplicates_themes_before_fetching_details(self):
        servers = [
            {"qualifiedName": "vendor/product", "displayName": "Product MCP", "description": "A public business service with searchable operational records."},
            {"qualifiedName": "vendor/product-v2", "displayName": "Product", "description": "A duplicate entry for the same public business service."},
            {"qualifiedName": "vendor/library", "displayName": "Library", "description": "A public library service with searchable operational records."},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.json"
            with (
                patch("agent_world_mini.seed_gen.catalog._smithery_servers", return_value=servers),
                patch("agent_world_mini.seed_gen.catalog._read_server_detail", side_effect=lambda item: item | {"tools": [{"name": "search"}]}),
                patch("agent_world_mini.seed_gen.catalog._organize_environment", side_effect=lambda item, _llm: item | {"organizationStatus": "agent_organized"}),
            ):
                summary = prepare_smithery_catalog(output)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(summary["prepared"], 2)
            self.assertEqual([item["displayName"] for item in payload["environments"]], ["Product MCP", "Library"])
