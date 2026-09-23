import json
import base64
import gzip
import re


def test_visualization_is_self_contained_and_escapes_embedded_html(tmp_path):
    from distill.visualize import render_trajectory_html

    trajectory = {
        "trajectory_id": "trace-1",
        "created_at": "2026-09-21T00:00:00+00:00",
        "model": {"upstream_id": "kimi-k3", "display_name": "Kimi K3", "max_context_size": 1000000},
        "task": {"task_id": "task4", "task_text": "inspect </script> safely"},
        "steps": [{
            "index": 0, "turn_id": 0, "step": 1, "step_id": "s1",
            "reasoning": {"text": "reason", "event_sequences": [1]},
            "assistant_text": "done", "tool_calls": [], "finish": {},
        }],
        "compactions": [],
        "outcome": {
            "status": "completed", "final_answer": "done", "usage": {"total": {"output": 2}},
            "evaluation": {"outcome": "pass", "summary": "ok", "requirements": []},
        },
        "artifacts": [],
    }
    source = tmp_path / "trajectory.json"
    source.write_text(json.dumps(trajectory), encoding="utf-8")
    raw = tmp_path / "raw"
    raw.mkdir()
    request = {
        "request_id": 1,
        "recorded_at": "2026-09-21T00:00:01+00:00",
        "sha256": "a" * 64,
        "request": {
            "model": "kimi-k3",
            "stream": True,
            "max_tokens": 131072,
            "messages": [
                {"role": "system", "content": "Match the user's language. </script>"},
                {"role": "user", "content": "检查任务"},
            ],
            "tools": [{
                "type": "function",
                "function": {"name": "inspect_environment", "parameters": {"type": "object"}},
            }],
        },
    }
    (raw / "model_requests.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
    output = render_trajectory_html(source, tmp_path / "trajectory.html")
    html = output.read_text(encoding="utf-8")
    assert "Kimi K3 蒸馏轨迹" in html
    assert "首次模型上下文" in html
    assert "inspect_environment" in html
    assert "Match the user's language. </script>" not in html
    assert "Match the user's language. \\u003c/script>" in html
    assert "https://" not in html
    assert "inspect </script> safely" not in html
    assert "inspect \\u003c/script> safely" in html


def test_batch_visualization_preserves_summary_order_and_links_cases(tmp_path):
    from distill.visualize import render_run_visualizations

    results = []
    for index, task_id in enumerate(("task7", "task2"), 1):
        case = f"environment_{index}__{task_id}"
        case_dir = tmp_path / case
        case_dir.mkdir()
        trajectory = {
            "trajectory_id": f"trace-{index}",
            "created_at": "2026-09-21T00:00:00+00:00",
            "model": {"upstream_id": "kimi-k3", "display_name": "Kimi K3", "max_context_size": 1000000},
            "task": {"task_id": task_id, "task_text": f"任务 {index} </script>"},
            "environment": {"environment_id": f"environment_{index}"},
            "steps": [{
                "index": 0, "turn_id": 0, "step": 1,
                "reasoning": {"text": "reason", "event_sequences": [1]},
                "assistant_text": "done", "tool_calls": [{}] * index,
            }],
            "compactions": [],
            "outcome": {
                "status": "completed", "final_answer": "done",
                "usage": {"total": {"output": index}}, "evaluation": None,
            },
            "artifacts": [],
        }
        (case_dir / "trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
        results.append({"case": case, "status": "completed"})
    (tmp_path / "summary.json").write_text(json.dumps({"results": results}), encoding="utf-8")

    index_path = render_run_visualizations(tmp_path)
    html = index_path.read_text(encoding="utf-8")
    assert html.index("environment_1__task7/trajectory.html") < html.index("environment_2__task2/trajectory.html")
    assert "任务 1 </script>" not in html
    assert "任务 1 \\u003c/script>" in html
    assert (tmp_path / "environment_1__task7/trajectory.html").is_file()
    assert (tmp_path / "environment_2__task2/trajectory.html").is_file()

    from distill.visualize import render_combined_visualization

    combined = render_combined_visualization(tmp_path)
    combined_html = combined.read_text(encoding="utf-8")
    match = re.search(r'<script id="combined-data" type="application/json">(.*?)</script>', combined_html)
    assert match is not None
    payload = json.loads(match.group(1))
    assert [item["task_id"] for item in payload["documents"]] == ["task7", "task2"]
    embedded = gzip.decompress(base64.b64decode(payload["documents"][0]["gzip_base64"])).decode()
    assert "首次模型上下文" in embedded
    assert "任务 1 \\u003c/script>" in embedded


def test_batch_visualization_can_freeze_a_prefix(tmp_path):
    from distill.visualize import render_run_visualizations

    results = []
    for index in range(3):
        case = f"case-{index}"
        case_dir = tmp_path / case
        case_dir.mkdir()
        trajectory = {
            "trajectory_id": f"trace-{index}",
            "created_at": "2026-09-21T00:00:00+00:00",
            "model": {"upstream_id": "kimi-k3"},
            "task": {"task_id": f"task{index}", "task_text": f"task {index}"},
            "environment": {"environment_id": "env"},
            "steps": [],
            "compactions": [],
            "outcome": {"status": "completed", "final_answer": "done", "usage": {}, "evaluation": None},
            "artifacts": [],
        }
        (case_dir / "trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
        results.append({"case": case, "status": "completed"})
    (tmp_path / "summary.json").write_text(json.dumps({"results": results}), encoding="utf-8")

    output = render_run_visualizations(tmp_path, tmp_path / "first-two.html", limit=2)
    html = output.read_text(encoding="utf-8")

    assert "case-0" in html
    assert "case-1" in html
    assert "case-2" not in html
