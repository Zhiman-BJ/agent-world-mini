"""Audit six historical executions; expected labels are never sent to Codex."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_gen.task_eval import load_cases
from task_gen.task_eval_verifier import verify_execution


CASES = [
    ("recreation_sol_retry_2", "task16", "sol_verifier_qwen", "pass"),
    ("openzeppelin_sol_full", "task1", "sol_verifier_qwen", "fail"),
    ("openzeppelin_sol_full", "task6", "sol_verifier_qwen", "fail"),
    ("openzeppelin_sol_full", "task8", "sol_verifier_qwen", "pass"),
    ("pymatgen_sol_full", "task19", "qwen_trial_final", "fail"),
    ("medical_sol_full", "task15", "reference", "fail"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--case", action="append", help="Select group_taskid; repeat for multiple cases")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    def run(item):
        group, task_id, kind, expected = item
        case = next(c for c in load_cases(args.runs / group) if c.task["task_id"] == task_id)
        if kind == "reference":
            state, calls = case.reference_state, case.reference_calls
            answer = case.task["reference"]["answer"]
            execution = {"source": "reference execution being independently evaluated"}
        else:
            folder = case.source_run / kind / task_id
            payload = json.loads((folder / "result.json").read_text())
            state = folder / "state"
            trace = folder / "state.agent/tool_calls.jsonl"
            calls = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
            answer = payload.get("agent_answer", payload.get("answer", ""))
            # Old result.error may describe the old verifier, not the solver.
            execution = {"source": str(folder), "has_final_answer": bool(answer)}
        name = group + "_" + task_id
        started = time.monotonic()
        print("START", name, flush=True)
        try:
            verdict = verify_execution(run_dir=args.output / name,
                task=case.task, environment=case.environment,
                initial_state=case.initial_state, actual_state=state, calls=calls,
                answer=answer, execution=execution, reference_state=case.reference_state,
                reference_calls=case.reference_calls, reference_answer=case.task.get("reference", {}).get("answer", ""),
                config={"timeout_seconds": args.timeout})
            result = {"case": name, "historical_expected": expected, "outcome": verdict["outcome"],
                      "summary": verdict["summary"], "matches_history": verdict["outcome"] == expected}
        except Exception as error:
            result = {"case": name, "historical_expected": expected, "error": f"{type(error).__name__}: {error}"}
        result["seconds"] = time.monotonic() - started
        (args.output / (name + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print("DONE", name, result.get("outcome", result.get("error")), flush=True)
        return result

    selected = [c for c in CASES if not args.case or c[0] + "_" + c[1] in args.case]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, selected[:args.limit]))
    (args.output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
