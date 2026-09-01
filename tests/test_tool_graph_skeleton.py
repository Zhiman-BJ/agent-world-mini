"""Tool Graph 包骨架的最小回归检查。"""

from pathlib import Path
import ast
import tempfile
import unittest
from unittest.mock import patch


class ToolGraphSkeletonTest(unittest.TestCase):
    def test_pipeline_reports_stage_when_bundle_persistence_fails(self) -> None:
        from task_gen.tool_graph.contracts import Config
        from task_gen.tool_graph.pipeline import run

        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            updates = []
            with (
                patch("task_gen.tool_graph.pipeline.run_io.load_config", return_value=Config()),
                patch("task_gen.tool_graph.pipeline.run_io.create_run_dir", return_value=run_dir),
                patch("task_gen.tool_graph.pipeline.run_io.save_run_meta"),
                patch("task_gen.tool_graph.pipeline.load_environment", return_value={"environment": {}}),
                patch("task_gen.tool_graph.pipeline.run_io.merge_output", side_effect=OSError("disk full")),
                patch("task_gen.tool_graph.pipeline.run_io.update_run_meta", side_effect=lambda _p, value: updates.append(value)),
            ):
                with self.assertRaises(OSError):
                    run(Path("config.yaml"))

        self.assertEqual(updates[-1]["failed_step"], "step_0_environment_load")

    def test_public_entrypoint_imports(self) -> None:
        from task_gen.tool_graph import run
        from task_gen.tool_graph.pipeline import run as pipeline_run

        self.assertIs(run, pipeline_run)

    def test_run_contracts_are_instantiable(self) -> None:
        from task_gen.tool_graph.contracts import Config, RunResult

        config = Config(
            environment_dir=Path("environment"),
            schema_dir=Path("schemas"),
            output_root=Path("runs"),
            llm={},
            graph={},
            planning={},
            execution={},
            cost={},
        )
        result = RunResult(Path("run"), 1, 0, {})

        self.assertEqual(config.environment_dir, Path("environment"))
        self.assertEqual(result.task_count, 1)

    def test_config_precedence_is_cli_then_file_then_defaults(self) -> None:
        from task_gen.tool_graph import run_io
        from task_gen.tool_graph.contracts import Config

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "tool_graph.yaml"
            config_path.write_text(
                "paths:\n"
                "  environment_dir: file_environment\n"
                "  output_root: file_runs\n",
                encoding="utf-8",
            )

            config = run_io.load_config(
                config_path,
                {"environment_dir": str(root / "cli_environment")},
            )

        self.assertEqual(config.environment_dir, (root / "cli_environment").resolve())
        self.assertEqual(config.output_root, (root / "file_runs").resolve())
        self.assertEqual(config.schema_dir, Config().schema_dir)

    def test_step_zero_contract_starts_the_bundle(self) -> None:
        from task_gen.tool_graph import run_io
        from task_gen.tool_graph.contracts import EnvironmentLoadInput, EnvironmentLoadOutput

        self.assertEqual(
            EnvironmentLoadInput.__required_keys__,
            {"config"},
        )
        self.assertEqual(
            EnvironmentLoadOutput.__required_keys__,
            {"environment"},
        )
        config = run_io.load_config(Path("config/tool_graph.yaml"))
        stage_input = run_io.to_environment_load_input(config)
        self.assertEqual(set(stage_input), {"config"})
        self.assertIs(stage_input["config"], config)

    def test_step_one_contract_uses_environment_to_build_tool_graph(self) -> None:
        from task_gen.tool_graph import run_io
        from task_gen.tool_graph.contracts import BuildGraphInput, BuildGraphOutput, Config

        self.assertEqual(BuildGraphInput.__required_keys__, {"config", "environment"})
        self.assertEqual(BuildGraphOutput.__required_keys__, {"tool_graph"})
        config = Config()
        environment = {"environment_id": "example", "tools": []}
        stage_input = run_io.to_build_graph_input({"environment": environment}, config)

        self.assertEqual(set(stage_input), {"config", "environment"})
        self.assertIs(stage_input["config"], config)
        self.assertIs(stage_input["environment"], environment)

    def test_step_two_contract_uses_environment_and_tool_graph(self) -> None:
        from task_gen.tool_graph import run_io
        from task_gen.tool_graph.contracts import Config, SampleChainsInput, SampleChainsOutput

        self.assertEqual(
            SampleChainsInput.__required_keys__,
            {"config", "environment", "tool_graph"},
        )
        self.assertEqual(
            SampleChainsOutput.__required_keys__,
            {"tasks", "sampling_report"},
        )
        config = Config()
        environment = {"environment_id": "example", "tools": []}
        tool_graph = [{"from_tool": "a", "to_tool": "b"}]
        stage_input = run_io.to_sample_chains_input(
            {"environment": environment, "tool_graph": tool_graph},
            config,
        )

        self.assertEqual(set(stage_input), {"config", "environment", "tool_graph"})
        self.assertIs(stage_input["config"], config)
        self.assertIs(stage_input["environment"], environment)
        self.assertIs(stage_input["tool_graph"], tool_graph)

    def test_steps_three_to_five_pass_only_their_declared_inputs(self) -> None:
        from task_gen.tool_graph import run_io
        from task_gen.tool_graph.contracts import (
            ComposeTasksInput,
            ComposeTasksOutput,
            Config,
            ExecuteChainsInput,
            ExecuteChainsOutput,
            ValidateTasksInput,
            ValidateTasksOutput,
        )

        expected_input_keys = {"config", "environment", "tasks"}
        run_scoped_input_keys = expected_input_keys | {"run_dir"}
        self.assertEqual(ExecuteChainsInput.__required_keys__, run_scoped_input_keys)
        self.assertEqual(ComposeTasksInput.__required_keys__, expected_input_keys)
        self.assertEqual(ValidateTasksInput.__required_keys__, run_scoped_input_keys)
        self.assertEqual(ExecuteChainsOutput.__required_keys__, {"tasks"})
        self.assertEqual(ComposeTasksOutput.__required_keys__, {"tasks"})
        self.assertEqual(ValidateTasksOutput.__required_keys__, {"tasks"})

        config = Config()
        environment = {"environment_id": "example", "tools": []}
        tasks = [{"chain": ["a", "b"]}]
        base_bundle = {
            "environment": environment,
            "tasks": tasks,
        }
        run_dir = Path("/tmp/example_run")
        stage_input = run_io.to_execute_chains_input(base_bundle, config, run_dir)
        self.assertEqual(set(stage_input), run_scoped_input_keys)
        self.assertIs(stage_input["run_dir"], run_dir)

        stage_input = run_io.to_compose_tasks_input(base_bundle, config)
        self.assertEqual(set(stage_input), expected_input_keys)
        self.assertIs(stage_input["config"], config)
        self.assertIs(stage_input["environment"], environment)
        self.assertIs(stage_input["tasks"], tasks)

        stage_input = run_io.to_validate_tasks_input(base_bundle, config, run_dir)
        self.assertEqual(set(stage_input), run_scoped_input_keys)
        self.assertIs(stage_input["run_dir"], run_dir)

    def test_run_io_uses_full_bundle_snapshots(self) -> None:
        import inspect

        from task_gen.tool_graph import run_io

        self.assertTrue(callable(run_io.save_bundle))
        self.assertTrue(callable(run_io.load_bundle))
        self.assertTrue(callable(run_io.load_latest_bundle))
        self.assertFalse(hasattr(run_io, "save_graph"))
        self.assertEqual(
            list(inspect.signature(run_io.save_bundle).parameters),
            ["run_dir", "bundle"],
        )

    def test_merge_output_allows_tasks_to_be_enriched_but_rejects_other_overwrites(self) -> None:
        from task_gen.tool_graph import run_io
        from task_gen.tool_graph.contracts import PipelineStep

        bundle = {
            "environment": {"environment_id": "example"},
            "tasks": [{"chain": ["a"]}],
            "_step": "step_2_chain_sample",
        }
        enriched = [{"chain": ["a"], "execution": {"success": True}}]
        run_io.merge_output(bundle, {"tasks": enriched}, PipelineStep.CHAIN_EXECUTE)

        self.assertEqual(bundle["_step"], "step_3_chain_execute")
        self.assertIn("environment", bundle)
        self.assertIs(bundle["tasks"], enriched)
        with self.assertRaises(KeyError):
            run_io.merge_output(
                bundle,
                {"environment": {"environment_id": "changed"}},
                PipelineStep.TASK_COMPOSE,
            )

    def test_pipeline_lists_all_six_steps(self) -> None:
        pipeline_path = Path(__file__).parents[1] / "task_gen" / "tool_graph" / "pipeline.py"
        tree = ast.parse(pipeline_path.read_text(encoding="utf-8"))
        calls = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]

        self.assertEqual(
            [name for name in calls if name in {
                "load_environment", "build_graph", "sample_chains",
                "execute_chains", "compose_tasks", "validate_tasks",
            }],
            [
                "load_environment", "build_graph", "sample_chains",
                "execute_chains", "compose_tasks", "validate_tasks",
            ],
        )

    def test_cli_exposes_path_overrides(self) -> None:
        from task_gen.tool_graph.pipeline import build_parser

        arguments = build_parser().parse_args([
            "--config", "custom.yaml",
            "--environment-dir", "environment",
            "--schema-dir", "schemas",
            "--output-root", "runs",
            "--model", "gpt-5.6-sol",
            "--backend", "codex",
        ])

        self.assertEqual(arguments.config, Path("custom.yaml"))
        self.assertEqual(arguments.environment_dir, Path("environment"))
        self.assertEqual(arguments.schema_dir, Path("schemas"))
        self.assertEqual(arguments.output_root, Path("runs"))
        self.assertEqual(arguments.model, "gpt-5.6-sol")
        self.assertEqual(arguments.backend, "codex")


if __name__ == "__main__":
    unittest.main()
