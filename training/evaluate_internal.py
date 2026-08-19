from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any

from openai import OpenAI

from agent_world_mini.io_utils import extract_json_object
from agent_world_mini.models import Record, ToolSpec
from agent_world_mini.runtime import LocalToolRuntime
from agent_world_mini.verification import FiveRunVerifier


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_runtime(path: Path) -> LocalToolRuntime:
    value = json.loads(path.read_text(encoding="utf-8"))
    return LocalToolRuntime([Record(**item) for item in value["records"]], [ToolSpec(**item) for item in value["tools"]])


def parse_answer(text: str) -> dict[str, Any] | None:
    try:
        value = extract_json_object(text)
        return value if isinstance(value, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def run_once(
    client: OpenAI,
    model: str,
    row: dict[str, Any],
    data_root: Path,
    registry: dict[str, Any],
    temperature: float,
    max_steps: int,
) -> dict[str, Any]:
    extra = row["extra_info"]
    runtime = make_runtime(data_root / next(iter(extra["tools_kwargs"].values()))["create_kwargs"]["environment_file"])
    selected = set(extra["tool_selection"])
    tools = [entry["tool_schema"] for name, entry in registry.items() if name in selected]
    original = {
        name: values["create_kwargs"]["original_name"] for name, values in extra["tools_kwargs"].items()
    }
    messages = deepcopy(row["prompt"])
    trace: list[dict[str, Any]] = []
    for _ in range(max_steps):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=0.95 if temperature else 1.0,
        ).choices[0].message
        assistant: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
        if response.tool_calls:
            assistant["tool_calls"] = [call.model_dump() for call in response.tool_calls]
        messages.append(assistant)
        if not response.tool_calls:
            truth = json.loads(row["reward_model"]["ground_truth"])
            answer = parse_answer(response.content or "")
            task = {"request": truth["request"], "validation": {"answer_slots": truth["answer_slots"]}}
            judgment = FiveRunVerifier._judge(task, {"reference_answer": truth["reference_answer"]}, trace, answer)
            return {
                "success": judgment["passed"],
                "calls": trace,
                "final_text": response.content or "",
                "final_answer": answer,
                "judgment": judgment,
            }
        for call in response.tool_calls:
            registered_name = call.function.name
            try:
                arguments = json.loads(call.function.arguments)
                result = runtime.call(original[registered_name], arguments)
                trace.append({"tool": original[registered_name], "arguments": arguments, "result": result})
                content = json.dumps(result, ensure_ascii=False)
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, StopIteration) as error:
                content = f"Tool error: {type(error).__name__}: {error}"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
    return {"success": False, "calls": trace, "judgment": {"passed": False, "reason": "step_limit"}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate unseen Agent-World environments through an OpenAI endpoint")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1"))
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "EMPTY"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.data_root.resolve()
    rows = load_jsonl(root / "grpo" / f"agentworld_{args.split}.jsonl")
    if args.limit is not None:
        rows = rows[: args.limit]
    tool_config = json.loads((root / "grpo" / "tool_config.json").read_text(encoding="utf-8"))
    registry = {entry["tool_schema"]["function"]["name"]: entry for entry in tool_config["tools"]}
    client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=600)
    def evaluate_row(index: int, row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        attempts = []
        for _ in range(args.runs):
            attempts.append(run_once(client, args.model, row, root, registry, args.temperature, args.max_steps))
        return index, {
            "environment_id": row["extra_info"]["environment_id"],
            "task_id": row["extra_info"]["task_id"],
            "chain_steps": row["extra_info"]["chain_steps"],
            "attempts": attempts,
        }

    results: list[dict[str, Any]] = [{} for _ in rows]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(evaluate_row, index, row) for index, row in enumerate(rows)]
        for completed, future in enumerate(as_completed(futures), start=1):
            index, result = future.result()
            results[index] = result
            if completed % 10 == 0 or completed == len(rows):
                print(f"evaluated {completed}/{len(rows)}", flush=True)

    buckets: dict[str, list[bool]] = defaultdict(list)
    for result in results:
        steps = result["chain_steps"]
        bucket = "1-3" if steps <= 3 else "4-6" if steps <= 6 else "7+"
        buckets[bucket].append(bool(result["attempts"][0]["success"]))
    pass_at_1 = sum(item["attempts"][0]["success"] for item in results) / len(results)
    pass_at_k = sum(any(attempt["success"] for attempt in item["attempts"]) for item in results) / len(results)
    report = {
        "model": args.model,
        "split": args.split,
        "tasks": len(results),
        "runs": args.runs,
        "temperature": args.temperature,
        "pass_at_1": round(pass_at_1, 6),
        "pass_at_k": round(pass_at_k, 6),
        "by_reference_length": {
            name: {"tasks": len(values), "pass_at_1": round(sum(values) / len(values), 6)}
            for name, values in sorted(buckets.items())
        },
        "failure_reasons": dict(
            Counter(
                item["attempts"][0]["judgment"].get("reason", "unknown")
                for item in results
                if not item["attempts"][0]["success"]
            )
        ),
        "created_at": int(time.time()),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))


if __name__ == "__main__":
    main()
