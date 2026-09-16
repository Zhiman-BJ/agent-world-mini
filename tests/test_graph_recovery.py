import json
from unittest.mock import patch

import pytest

from task_gen.tool_graph import step_1_graph_build as graph
from task_gen.tool_graph.contracts import Config
from task_gen.tool_graph.llm import InferenceResult


def reply(source=None):
    return InferenceResult(json.dumps({
        "decisions": [] if source is None else [
            {"from_tool": source, "weight": 1, "reason": "related"}],
        "prerequisite_alternatives": [],
    }), {}, "test")


def inputs(retries=3):
    return {"config": Config(graph={"retry_count": retries}), "environment": {
        "tools": [{"name": name, "description": name, "inputSchema": {"type": "object"},
                   "outputSchema": {"type": "object"}} for name in ("a", "b")],
    }}


def test_feedback_and_configured_retries():
    with patch.object(graph, "infer", side_effect=[
        [reply("b"), reply()], [reply()], [reply()], [reply("a")],
    ]) as request:
        output = graph.build_graph(inputs())
    assert len(output["tool_graph"]["edges"]) == 2
    assert request.call_count == 4
    for call in request.call_args_list[1:]:
        assert len(call.args[0]) == 1
        assert "漏审：a" in call.args[0][0]
        assert reply().text in call.args[0][0]


def test_failed_run_preserves_success_and_resume_only_requests_missing(tmp_path):
    with patch.object(graph, "infer", return_value=[reply("b"), reply()]):
        with pytest.raises(ValueError, match="b"):
            graph.build_graph(inputs(0), checkpoint_dir=tmp_path)
    with patch.object(graph, "infer", return_value=[reply("a")]) as request:
        output = graph.build_graph(inputs(0), checkpoint_dir=tmp_path)
    assert len(request.call_args.args[0]) == 1
    assert len(output["tool_graph"]["edges"]) == 2
    with patch.object(graph, "infer", side_effect=AssertionError("must reuse")):
        assert graph.build_graph(inputs(0), checkpoint_dir=tmp_path) == output
    changed = inputs(0)
    changed["environment"]["tools"][0]["description"] = "changed contract"
    with patch.object(graph, "infer", return_value=[reply("b"), reply("a")]) as request:
        graph.build_graph(changed, checkpoint_dir=tmp_path)
    assert len(request.call_args.args[0]) == 2


def test_exhaustion_reports_every_failed_target():
    with patch.object(graph, "infer", return_value=[reply(), reply()]) as request:
        with pytest.raises(ValueError) as failure:
            graph.build_graph(inputs(0))
    assert request.call_count == 1
    assert "目标 a" in str(failure.value) and "目标 b" in str(failure.value)


def test_completed_target_survives_interrupted_batch(tmp_path):
    def interrupted(prompts, *, llm_config, on_result):
        on_result(0, reply("b"))
        assert len(list(tmp_path.glob("*.json"))) == 1
        raise KeyboardInterrupt

    with patch.object(graph, "infer", interrupted):
        with pytest.raises(KeyboardInterrupt):
            graph.build_graph(inputs(), checkpoint_dir=tmp_path)
    with patch.object(graph, "infer", return_value=[reply("a")]) as request:
        assert len(graph.build_graph(inputs(), checkpoint_dir=tmp_path)["tool_graph"]["edges"]) == 2
    assert len(request.call_args.args[0]) == 1


def test_cache_is_revalidated_and_model_change_invalidates_it(tmp_path):
    with patch.object(graph, "infer", return_value=[reply("b"), reply("a")]):
        graph.build_graph(inputs(), checkpoint_dir=tmp_path)
    for path in tmp_path.glob("*.json"):
        record = json.loads(path.read_text())
        if record["target"] == "a":
            record["answer"] = reply().text
            path.write_text(json.dumps(record))
    with patch.object(graph, "infer", return_value=[reply("b")]) as request:
        graph.build_graph(inputs(), checkpoint_dir=tmp_path)
    assert len(request.call_args.args[0]) == 1
    changed = inputs()
    changed["config"].llm["model"] = "another-model"
    with patch.object(graph, "infer", return_value=[reply("b"), reply("a")]) as request:
        graph.build_graph(changed, checkpoint_dir=tmp_path)
    assert len(request.call_args.args[0]) == 2


@pytest.mark.parametrize("retries", [-1, True, 1.5])
def test_invalid_retry_count_fails_before_request(retries):
    with patch.object(graph, "infer", side_effect=AssertionError("must validate first")):
        with pytest.raises(ValueError, match="retry_count"):
            graph.build_graph(inputs(retries))


def test_batch_callback_runs_before_slow_first_request_and_preserves_order():
    from threading import Event
    from task_gen.tool_graph.llm import _run_batch

    saved = Event()
    completed = []

    def run(index, prompt):
        if index == 0:
            assert saved.wait(2), "callback was blocked by slow first request"
        return reply(prompt)

    def on_result(index, result):
        completed.append(index)
        if index == 1:
            saved.set()

    results = _run_batch(run, ["a", "b"], 2, on_result)
    assert completed == [1, 0]
    assert results == [reply("a"), reply("b")]


def test_pipeline_resume_uses_target_checkpoints(tmp_path):
    from task_gen.tool_graph import pipeline

    config = tmp_path / "config.yaml"
    config.write_text(f"paths:\n  output_root: {tmp_path}/runs\ngraph:\n  retry_count: 0\n")
    with patch.object(pipeline, "load_environment", return_value={"environment": inputs()["environment"]}), \
            patch.object(graph, "infer", return_value=[reply("b"), reply()]):
        with pytest.raises(ValueError, match="目标 b"):
            pipeline.run(config, stop_after=1)
    run_dir = next((tmp_path / "runs").iterdir())
    assert not (run_dir / "intermediate/step_1_bundle.json").exists()
    assert json.loads((run_dir / "run.json").read_text())["status"] == "failed"
    with patch.object(graph, "infer", return_value=[reply("a")]) as request:
        pipeline.run(resume=run_dir, stop_after=1)
    assert len(request.call_args.args[0]) == 1
    assert (run_dir / "intermediate/step_1_bundle.json").exists()
