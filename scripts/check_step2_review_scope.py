"""Replay selected Step2 reviews without advancing the pipeline."""
import argparse
import json
import threading
from pathlib import Path

from task_gen.tool_graph.llm import capture_calls
from task_gen.tool_graph.step_2_chain_sample import _generate_objectives, _review_chains, _tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--tasks", type=int, nargs="+", default=[3, 8, 9])
    parser.add_argument("--objectives-only", action="store_true")
    parser.add_argument("--regenerate-objectives", action="store_true")
    parser.add_argument("--objectives-file", type=Path)
    args = parser.parse_args()
    bundle = json.loads((args.run / "intermediate/step_2_bundle.json").read_text())
    config = json.loads((args.run / "run.json").read_text())["config"]
    candidates = []
    for number in args.tasks:
        task = bundle["tasks"][number - 1]
        record = next(r for r in bundle["sampling_report"]["review_records"] if r["chain"] == task["chain"])
        candidates.append({"chain": record["original_chain"], "objective": record["objective"], "score": 1})
    if args.objectives_file:
        if args.objectives_only or args.regenerate_objectives:
            parser.error("--objectives-file 只用于复用目标进行 review")
        saved = json.loads(args.objectives_file.read_text())
        if len(saved) != len(candidates):
            raise ValueError("保存目标数量与所选链数量不一致")
        for candidate, record in zip(candidates, saved):
            if record["chain"] != candidate["chain"] or record.get("error"):
                raise ValueError("保存目标与原链不匹配或生成失败")
            if not isinstance(record.get("objective"), str) or not record["objective"].strip():
                raise ValueError("保存目标必须是非空字符串")
            candidate["objective"] = record["objective"]
            candidate["design_basis"] = record.get("design_basis")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "inputs.json").write_text(json.dumps({"task_numbers": args.tasks, "candidates": candidates}, ensure_ascii=False, indent=2))
    lock = threading.Lock()
    def record_call(call):
        with lock, (args.output / "llm_calls.jsonl").open("a") as stream:
            stream.write(json.dumps(call, ensure_ascii=False) + "\n")
    names, tools = _tools(bundle["environment"])
    records = []
    with capture_calls("step_2_chain_sample", record_call):
        if args.regenerate_objectives:
            candidates, objectives = _generate_objectives([(c["chain"], c["score"]) for c in candidates],
                                                          bundle["environment"], tools, config["llm"])
            (args.output / "objectives.json").write_text(json.dumps(objectives, ensure_ascii=False, indent=2))
            if len(candidates) != len(args.tasks):
                raise RuntimeError("目标生成失败；查看 objectives.json")
        if args.objectives_only:
            _, records = _generate_objectives([(c["chain"], c["score"]) for c in candidates],
                                               bundle["environment"], tools, config["llm"])
        else:
            _review_chains(candidates, bundle["environment"], tools, bundle["tool_graph"], names,
                           config["llm"], 18, 30, records,
                           initial_workspace=Path(config["environment_dir"]) / "state")
    (args.output / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
    for number, record in zip(args.tasks, records):
        print(number, record.get("accepted"), len(record["chain"]), record.get("score"), record["error"], flush=True)


if __name__ == "__main__":
    main()
