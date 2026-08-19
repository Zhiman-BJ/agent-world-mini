from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_BATCHES = (
    "runs/luna-batch-30-fresh",
    "runs/luna-batch-40-fresh",
    "runs/luna-batch-50-fresh",
)
SPLIT_COUNTS = {"train": 90, "dev": 15, "test": 15}
SYSTEM_PROMPT = (
    "Use the available tools to answer the request. Base every factual claim on tool results. "
    "Return one JSON object whose keys are the requested answer sections."
)


def request_with_sections(request: str, slots: list[dict[str, Any]]) -> str:
    sections = [{"name": slot["name"], "description": slot.get("description", "")} for slot in slots]
    if not sections:
        return request
    return f"{request}\n\nRequired JSON answer sections: {json.dumps(sections, ensure_ascii=False)}"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in materialized),
        encoding="utf-8",
    )
    return len(materialized)


def stable_key(value: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def infer_domain(theme: str, environment_id: str) -> str:
    text = f"{theme} {environment_id}".casefold()
    groups = (
        ("life_science", "medical pharma health food chemical cannabis gene bio protein clinical fda chembl pubchem rxnorm"),
        ("aerospace_earth", "nasa aviation flight opensky weather climate geo earthquake water recreation exoplanet"),
        ("economy_public", "economic bank finance treasury fiscal ecb fred senate tender government company business labor commodity sec"),
        ("software_data", "github gitlab npm pypi maven crates ruby package microsoft netlify supabase database grafbase code openzeppelin blockscout"),
        ("research_knowledge", "publication arxiv crossref datacite dblp openalex europe pmc zenodo library research learn knowledge"),
        ("industrial_service", "industrial crane energy vehicle data center commerce paypal airtable klaviyo bitly media transcription"),
    )
    for domain, words in groups:
        if any(word in text for word in words.split()):
            return domain
    return "general_tools"


def complexity_bucket(steps: float) -> str:
    if steps == 0:
        return "no_tasks"
    if steps < 5.5:
        return "short"
    if steps < 7.5:
        return "medium"
    return "long"


def stratified_split(environments: list[dict[str, Any]], seed: int) -> dict[str, str]:
    if len(environments) != sum(SPLIT_COUNTS.values()):
        raise ValueError(f"Expected 120 environments, found {len(environments)}")

    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for environment in environments:
        strata[(environment["domain"], environment["complexity"])].append(environment)
    assignment: dict[str, str] = {}
    train_pool: list[dict[str, Any]] = []
    current = {"dev": 0, "test": 0}
    remainders: dict[tuple[str, str], float] = {}

    for stratum, items in sorted(strata.items()):
        items.sort(key=lambda item: stable_key(item["global_environment_id"], seed))
        target = len(items) * SPLIT_COUNTS["dev"] / len(environments)
        base = math.floor(target)
        for item in items[:base]:
            assignment[item["global_environment_id"]] = "test"
        for item in items[base : 2 * base]:
            assignment[item["global_environment_id"]] = "dev"
        train_pool.extend(items[2 * base :])
        current["test"] += base
        current["dev"] += base
        remainders[stratum] = target - base

    for split in ("test", "dev"):
        needed = SPLIT_COUNTS[split] - current[split]
        candidates = sorted(
            train_pool,
            key=lambda item: (
                -remainders[(item["domain"], item["complexity"])],
                stable_key(f"{split}:{item['global_environment_id']}", seed),
            ),
        )
        chosen = candidates[:needed]
        chosen_ids = {item["global_environment_id"] for item in chosen}
        for item in chosen:
            assignment[item["global_environment_id"]] = split
        train_pool = [item for item in train_pool if item["global_environment_id"] not in chosen_ids]

    for item in train_pool:
        assignment[item["global_environment_id"]] = "train"
    counts = Counter(assignment.values())
    if dict(counts) != SPLIT_COUNTS:
        raise AssertionError(f"Unexpected split counts: {dict(counts)}")
    return assignment


def rebalance_split(environments: list[dict[str, Any]], assignment: dict[str, str]) -> dict[str, str]:
    """Improve task balance with pair swaps while keeping split sizes fixed."""
    total_tasks = sum(item["tasks"] for item in environments)
    total_trajectories = sum(item.get("trajectory_count", 0) for item in environments)
    domains = Counter(item["domain"] for item in environments)
    complexities = Counter(item["complexity"] for item in environments)
    by_id = {item["global_environment_id"]: item for item in environments}

    def cost(candidate: dict[str, str]) -> float:
        value = 0.0
        for split, count in SPLIT_COUNTS.items():
            ratio = count / len(environments)
            members = [by_id[key] for key, assigned in candidate.items() if assigned == split]
            task_target = max(1.0, total_tasks * ratio)
            trajectory_target = max(1.0, total_trajectories * ratio)
            value += 12 * ((sum(item["tasks"] for item in members) - task_target) / task_target) ** 2
            value += 4 * (
                (sum(item.get("trajectory_count", 0) for item in members) - trajectory_target)
                / trajectory_target
            ) ** 2
            member_domains = Counter(item["domain"] for item in members)
            member_complexities = Counter(item["complexity"] for item in members)
            value += 0.2 * sum(
                ((member_domains[name] - amount * ratio) / max(1.0, amount * ratio)) ** 2
                for name, amount in domains.items()
            )
            value += 0.2 * sum(
                ((member_complexities[name] - amount * ratio) / max(1.0, amount * ratio)) ** 2
                for name, amount in complexities.items()
            )
        return value

    result = dict(assignment)
    current = cost(result)
    ids = sorted(result)
    for _ in range(60):
        best_pair: tuple[str, str] | None = None
        best = current
        for left_index, left in enumerate(ids):
            for right in ids[left_index + 1 :]:
                if result[left] == result[right]:
                    continue
                result[left], result[right] = result[right], result[left]
                candidate_cost = cost(result)
                result[left], result[right] = result[right], result[left]
                if candidate_cost + 1e-9 < best:
                    best = candidate_cost
                    best_pair = (left, right)
        if best_pair is None:
            break
        left, right = best_pair
        result[left], result[right] = result[right], result[left]
        current = best
    return result


def json_type(type_name: str) -> dict[str, Any]:
    normalized = str(type_name).strip().casefold()
    if normalized.endswith("[]"):
        return {"type": "array", "items": json_type(normalized[:-2])}
    aliases = {"int": "integer", "float": "number", "dict": "object", "list": "array"}
    value = aliases.get(normalized, normalized)
    if value not in {"string", "integer", "number", "boolean", "object", "array"}:
        value = "string"
    return {"type": value}


def canonical_schema(tool: dict[str, Any]) -> dict[str, Any]:
    properties = {name: json_type(kind) for name, kind in tool.get("inputs", {}).items()}
    return {
        "description": str(tool.get("description", "")),
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        },
    }


def registered_tool_names(environment_tools: dict[str, list[dict[str, Any]]]) -> dict[tuple[str, str], str]:
    signatures: dict[str, set[str]] = defaultdict(set)
    for tools in environment_tools.values():
        for tool in tools:
            signatures[tool["name"]].add(json.dumps(canonical_schema(tool), sort_keys=True, ensure_ascii=False))

    result: dict[tuple[str, str], str] = {}
    for environment_id, tools in environment_tools.items():
        suffix = hashlib.sha1(environment_id.encode()).hexdigest()[:8]
        for tool in tools:
            original = str(tool["name"])
            if len(signatures[original]) == 1:
                registered = original
            else:
                stem = re.sub(r"[^a-zA-Z0-9_]", "_", original)[:48]
                registered = f"{stem}__{suffix}"
            result[(environment_id, original)] = registered
    return result


def llama_tool(tool: dict[str, Any], registered_name: str) -> dict[str, Any]:
    schema = canonical_schema(tool)
    return {"name": registered_name, **schema}


def openai_tool(tool: dict[str, Any], registered_name: str) -> dict[str, Any]:
    return {"type": "function", "function": llama_tool(tool, registered_name)}


def compact_reference_section(value: Any) -> Any:
    """Merge repeated observations into one complete record per real entity."""
    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        entity_id = item.get("entity_id")
        if entity_id not in (None, ""):
            key = str(entity_id)
            if key not in records:
                records[key] = {}
                order.append(key)
            for field, field_value in item.items():
                if field != "source_url" and field_value not in (None, "", [], {}):
                    records[key][field] = field_value
            return
        for child in item.values():
            visit(child)

    visit(value)
    if records:
        return [records[entity_id] for entity_id in order]
    if isinstance(value, dict):
        return {key: child for key, child in value.items() if key != "source_url"}
    return value


def compact_reference_answer(task: dict[str, Any]) -> dict[str, Any]:
    reference = task.get("reference_execution", {}).get("reference_answer", {})
    return {name: compact_reference_section(value) for name, value in reference.items()}


def sft_row(
    trajectory: dict[str, Any],
    global_environment_id: str,
    tool_names: dict[tuple[str, str], str],
) -> dict[str, Any]:
    tools = trajectory["available_tools"]
    converted_tools = [
        llama_tool(tool, tool_names[(global_environment_id, str(tool["name"]))]) for tool in tools
    ]
    request = request_with_sections(trajectory["request"], trajectory.get("required_answer_sections", []))
    conversations: list[dict[str, str]] = [{"from": "human", "value": request}]
    for call in trajectory["calls"]:
        registered = tool_names[(global_environment_id, str(call["tool"]))]
        conversations.append(
            {
                "from": "function_call",
                "value": json.dumps(
                    {"name": registered, "arguments": call.get("arguments", {})},
                    ensure_ascii=False,
                ),
            }
        )
        conversations.append(
            {"from": "observation", "value": json.dumps(call.get("result"), ensure_ascii=False)}
        )
    conversations.append(
        {"from": "gpt", "value": json.dumps(trajectory["final_answer"], ensure_ascii=False)}
    )
    return {
        "conversations": conversations,
        "system": SYSTEM_PROMPT,
        "tools": json.dumps(converted_tools, ensure_ascii=False),
        "metadata": {
            "trajectory_id": trajectory["trajectory_id"],
            "environment_id": global_environment_id,
            "task_id": trajectory["task_id"],
            "chain_steps": trajectory["chain_steps"],
        },
    }


def reference_sft_row(
    task: dict[str, Any],
    global_environment_id: str,
    tool_names: dict[tuple[str, str], str],
) -> dict[str, Any]:
    converted_tools = [
        llama_tool(tool, tool_names[(global_environment_id, str(tool["name"]))])
        for tool in task["available_tools"]
    ]
    answer_slots = task.get("validation", {}).get("answer_slots", [])
    conversations: list[dict[str, str]] = [
        {"from": "human", "value": request_with_sections(task["request"], answer_slots)}
    ]
    for call in task.get("reference_execution", {}).get("trace", []):
        registered = tool_names[(global_environment_id, str(call["tool"]))]
        conversations.append(
            {
                "from": "function_call",
                "value": json.dumps(
                    {"name": registered, "arguments": call.get("arguments", {})},
                    ensure_ascii=False,
                ),
            }
        )
        conversations.append(
            {"from": "observation", "value": json.dumps(call.get("result"), ensure_ascii=False)}
        )
    conversations.append(
        {"from": "gpt", "value": json.dumps(compact_reference_answer(task), ensure_ascii=False)}
    )
    return {
        "conversations": conversations,
        "system": SYSTEM_PROMPT,
        "tools": json.dumps(converted_tools, ensure_ascii=False),
        "metadata": {
            "trajectory_id": f"{global_environment_id}/{task['task_id']}/reference",
            "environment_id": global_environment_id,
            "task_id": task["task_id"],
            "chain_steps": task.get("validation", {}).get("chain_steps", 0),
        },
    }


def grpo_row(
    task: dict[str, Any],
    global_environment_id: str,
    environment_file: str,
    tool_names: dict[tuple[str, str], str],
) -> dict[str, Any]:
    selected = [tool_names[(global_environment_id, str(tool["name"]))] for tool in task["available_tools"]]
    tools_kwargs = {
        tool_names[(global_environment_id, str(tool["name"]))]: {
            "create_kwargs": {"environment_file": environment_file, "original_name": tool["name"]}
        }
        for tool in task["available_tools"]
    }
    ground_truth = {
        "request": task["request"],
        "answer_slots": task.get("validation", {}).get("answer_slots", []),
        "rubric": task.get("validation", {}).get("rubric", []),
        "expected_answer": compact_reference_answer(task),
        "reference_answer": task.get("reference_execution", {}).get("reference_answer", {}),
    }
    return {
        "data_source": "agentworld",
        "agent_name": "tool_agent",
        "prompt": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": request_with_sections(task["request"], ground_truth["answer_slots"]),
            },
        ],
        "ability": "multi_turn_tool_use",
        "reward_model": {"style": "rule", "ground_truth": json.dumps(ground_truth, ensure_ascii=False)},
        "extra_info": {
            "environment_id": global_environment_id,
            "task_id": task["task_id"],
            "chain_steps": task.get("validation", {}).get("chain_steps", 0),
            "tool_selection": selected,
            "need_tools_kwargs": True,
            "tools_kwargs": tools_kwargs,
        },
    }


def environment_runtime(bundle: dict[str, Any], tools: list[dict[str, Any]]) -> dict[str, Any]:
    return {"records": bundle["records"], "tools": tools}


def prepare(repo_root: Path, output: Path, batch_paths: list[Path], seed: int) -> dict[str, Any]:
    environments: list[dict[str, Any]] = []
    tasks_by_environment: dict[str, list[dict[str, Any]]] = {}
    trajectories_by_environment: dict[str, list[dict[str, Any]]] = {}
    environment_tools: dict[str, list[dict[str, Any]]] = {}
    runtime_payloads: dict[str, dict[str, Any]] = {}

    for batch_path in batch_paths:
        batch_name = batch_path.name
        dataset = read_json(batch_path / "luna_final_dataset.json")
        trajectories = read_jsonl(batch_path / "luna_successful_trajectories.jsonl")
        for environment in dataset["environments"]:
            local_id = environment["environment_id"]
            global_id = f"{batch_name}/{local_id}"
            steps = float(environment.get("average_steps_final", 0))
            environments.append(
                {
                    "global_environment_id": global_id,
                    "batch": batch_name,
                    "environment_id": local_id,
                    "theme": environment.get("theme", ""),
                    "source_name": environment.get("source_name", ""),
                    "tasks": int(environment.get("tasks_passed", 0)),
                    "average_steps": steps,
                    "domain": infer_domain(str(environment.get("theme", "")), local_id),
                    "complexity": complexity_bucket(steps),
                }
            )
            tasks_by_environment[global_id] = []
            trajectories_by_environment[global_id] = []
            tool_payload = environment["tool_specs"]
            tools = tool_payload.get("tools", []) if isinstance(tool_payload, dict) else tool_payload
            environment_tools[global_id] = tools
            runtime_payloads[global_id] = environment_runtime(environment["research_bundle"], tools)
        for task in dataset["tasks"]:
            global_id = f"{batch_name}/{task['environment_id']}"
            tasks_by_environment[global_id].append(task)
        for trajectory in trajectories:
            global_id = f"{batch_name}/{trajectory['environment_id']}"
            trajectories_by_environment[global_id].append(trajectory)

    for environment in environments:
        environment["trajectory_count"] = len(trajectories_by_environment[environment["global_environment_id"]])
    assignment = rebalance_split(environments, stratified_split(environments, seed))
    names = registered_tool_names(environment_tools)
    registry: dict[str, dict[str, Any]] = {}
    for environment_id, tools in environment_tools.items():
        for tool in tools:
            registered = names[(environment_id, str(tool["name"]))]
            schema = openai_tool(tool, registered)
            previous = registry.get(registered)
            if previous and previous["tool_schema"] != schema:
                raise AssertionError(f"Tool registry collision: {registered}")
            registry[registered] = {
                "class_name": "training.verl_agentworld_tool.AgentWorldTool",
                "config": {"type": "native", "original_name": tool["name"]},
                "tool_schema": schema,
            }

    output.mkdir(parents=True, exist_ok=True)
    split_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLIT_COUNTS}
    variant_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLIT_COUNTS}
    grpo_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLIT_COUNTS}
    split_tasks: Counter[str] = Counter()
    split_variants: Counter[str] = Counter()
    assistant_chars: Counter[str] = Counter()

    for environment in environments:
        global_id = environment["global_environment_id"]
        split = assignment[global_id]
        safe_name = global_id.replace("/", "__") + ".json"
        write_json(output / "environments" / safe_name, runtime_payloads[global_id])
        for trajectory in trajectories_by_environment[global_id]:
            row = sft_row(trajectory, global_id, names)
            variant_rows[split].append(row)
            split_variants[split] += 1
        for task in tasks_by_environment[global_id]:
            row = reference_sft_row(task, global_id, names)
            split_rows[split].append(row)
            assistant_chars[split] += sum(
                len(turn["value"]) for turn in row["conversations"] if turn["from"] in {"gpt", "function_call"}
            )
            grpo_rows[split].append(grpo_row(task, global_id, f"environments/{safe_name}", names))
            split_tasks[split] += 1

    rng = random.Random(seed)
    for split in SPLIT_COUNTS:
        rng.shuffle(split_rows[split])
        rng.shuffle(variant_rows[split])
        rng.shuffle(grpo_rows[split])
        write_jsonl(output / "sft" / f"agentworld_{split}.jsonl", split_rows[split])
        write_jsonl(output / "sft" / f"agentworld_{split}_five_run_variants.jsonl", variant_rows[split])
        write_jsonl(output / "grpo" / f"agentworld_{split}.jsonl", grpo_rows[split])

    write_json(output / "grpo" / "tool_config.json", {"tools": [registry[name] for name in sorted(registry)]})
    manifest_environments = []
    for environment in environments:
        global_id = environment["global_environment_id"]
        manifest_environments.append(
            {
                **environment,
                "split": assignment[global_id],
                "task_count": len(tasks_by_environment[global_id]),
                "trajectory_count": len(trajectories_by_environment[global_id]),
            }
        )
    write_json(
        output / "split_manifest.json",
        {"seed": seed, "target_counts": SPLIT_COUNTS, "environments": manifest_environments},
    )

    dataset_info = {}
    for split in SPLIT_COUNTS:
        dataset_info[f"agentworld_{split}"] = {
            "file_name": f"sft/agentworld_{split}.jsonl",
            "formatting": "sharegpt",
            "columns": {"messages": "conversations", "system": "system", "tools": "tools"},
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
                "observation_tag": "observation",
                "function_tag": "function_call",
            },
        }
        dataset_info[f"agentworld_{split}_five_run_variants"] = {
            **dataset_info[f"agentworld_{split}"],
            "file_name": f"sft/agentworld_{split}_five_run_variants.jsonl",
        }
    write_json(output / "dataset_info.json", dataset_info)

    stats = {
        "environments": len(environments),
        "tasks": sum(split_tasks.values()),
        "sft_reference_trajectories": sum(len(rows) for rows in split_rows.values()),
        "five_run_trajectories_preserved": sum(split_variants.values()),
        "registered_tools": len(registry),
        "split_environments": dict(Counter(assignment.values())),
        "split_tasks": dict(split_tasks),
        "split_sft_reference_trajectories": {name: len(split_rows[name]) for name in SPLIT_COUNTS},
        "split_five_run_trajectories": dict(split_variants),
        "assistant_characters": dict(assistant_chars),
        "zero_task_environments": sum(not tasks_by_environment[item["global_environment_id"]] for item in environments),
    }
    write_json(output / "stats.json", stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Agent-World SFT, GRPO, and held-out evaluation data")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("training_artifacts/agentworld_120"))
    parser.add_argument("--batch", action="append", dest="batches")
    parser.add_argument("--seed", type=int, default=20260817)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    batches = [root / value for value in (args.batches or DEFAULT_BATCHES)]
    output = args.output if args.output.is_absolute() else root / args.output
    print(json.dumps(prepare(root, output, batches, args.seed), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
