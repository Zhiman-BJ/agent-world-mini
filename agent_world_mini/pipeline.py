from __future__ import annotations

import argparse
from pathlib import Path

from .catalog import output_slug, prepare_smithery_catalog, select_prepared_themes
from .graph import ToolGraph
from .io_utils import write_json
from .llm import LLMClient
from .research import WebResearchAgent
from .runtime import LocalToolRuntime
from .tasks import TaskSynthesizer
from .themes import CURATED_THEME_SEEDS, ThemeSeed, resolve_theme
from .tools import ToolDesigner, ToolValidator
from .verification import FiveRunVerifier


class EnvironmentRejected(RuntimeError):
    pass


def run(
    theme: str | None,
    output_dir: Path,
    verify_five_runs: bool = False,
    theme_id: str | None = None,
    complexify_rounds: int = 2,
    max_semantic_reviews: int = 0,
    max_tasks: int = 0,
    react_max_steps: int = 12,
    max_candidates: int = 128,
    source_url: str | None = None,
    theme_seed: ThemeSeed | None = None,
) -> dict[str, object]:
    llm = LLMClient.from_environment()
    seed = theme_seed or resolve_theme(theme, theme_id, source_url)
    print(f"[{seed.theme_id}] researching {seed.source_url or seed.seed_label}", flush=True)
    bundle = WebResearchAgent(llm).gather(seed, complexify_rounds=complexify_rounds)
    print(f"[{seed.theme_id}] research complete: {len(bundle.records)} records", flush=True)
    write_json(output_dir / "research_bundle.json", bundle.to_dict())
    write_json(output_dir / "theme_registry.json", {
        "selected_theme": seed.to_dict(),
        "available_curated_theme_ids": sorted(CURATED_THEME_SEEDS),
    })
    designer = ToolDesigner(llm)
    candidate_tools, tool_mode = designer.design(bundle)
    candidate_runtime = LocalToolRuntime(bundle.records, candidate_tools)
    tools, validation_reports = ToolValidator().validate(candidate_tools, candidate_runtime)
    print(f"[{seed.theme_id}] tools complete: {len(tools)}/{len(candidate_tools)} retained", flush=True)
    write_json(output_dir / "tool_specs.json", {"generation_mode": tool_mode, "tools": [tool.to_dict() for tool in tools]})
    write_json(output_dir / "tool_validation.json", {
        "candidate_tools": len(candidate_tools),
        "retained_tools": len(tools),
        "selection": designer.last_selection_report,
        "reports": validation_reports,
    })
    if not tools:
        missing = designer.last_selection_report.get("missing_capabilities", [])
        detail = ", ".join(str(value) for value in missing) or "no useful data-supported tools passed"
        raise EnvironmentRejected(detail)
    graph = ToolGraph(tools, llm)
    chains = graph.chains()
    synthesizer = TaskSynthesizer(llm)
    tasks, task_mode, walk_report = synthesizer.synthesize_adaptive(
        bundle.theme,
        tools,
        bundle.records,
        graph.walks,
        max_candidates=max_candidates,
        max_semantic_reviews=max_semantic_reviews or None,
        max_tasks=max_tasks or None,
    )
    print(f"[{seed.theme_id}] tasks complete: {len(tasks)} from {walk_report['executed_walks']} unique executed walks", flush=True)
    rejected_tasks = []
    inconclusive_tasks = []
    five_run_attempted = 0
    if verify_five_runs:
        tool_contracts = [tool.to_dict() for tool in tools]
        def verify_one(index: int) -> tuple[int, dict[str, object]]:
            verifier = FiveRunVerifier(llm, LocalToolRuntime(bundle.records, tools), tool_contracts)
            return index, verifier.verify(tasks[index].to_dict(), tasks[index].reference_execution, max_steps=react_max_steps)
        for index in range(len(tasks)):
            print(f"[{seed.theme_id}] five-run task {index + 1}/{len(tasks)}", flush=True)
            _, result = verify_one(index)
            tasks[index].validation["five_run_verification"] = result
            five_run_attempted += 1
            print(
                f"[{seed.theme_id}] five-run task {index + 1}: {result['status']} "
                f"({result.get('successes', 0)} successes in {result.get('attempted_runs', 0)} runs)",
                flush=True,
            )
        rejected_tasks = [task.to_dict() for task in tasks if task.validation["five_run_verification"]["status"] == "rejected"]
        inconclusive_tasks = [task.to_dict() for task in tasks if task.validation["five_run_verification"]["status"] == "inconclusive_infrastructure"]
        tasks = [task for task in tasks if task.validation["five_run_verification"]["status"] == "passed"]

    write_json(output_dir / "environment_manifest.json", {
        "theme": bundle.theme,
        "theme_source": seed.to_dict(),
        "research_sources": bundle.sources,
        "state_contract": bundle.state_contract,
        "agent_visible_contract": {
            "task": "Provided per task",
            "tools": "All retained schemas are visible before the rollout",
            "state": "Only observations returned by calls are visible; database snapshot and evaluators remain sandbox-internal",
        },
        "reset_policy": "Each reference execution and ReAct rollout starts from the immutable source snapshot and an empty local overlay.",
    })
    write_json(output_dir / "tool_graph.json", graph.to_dict(chains))
    write_json(output_dir / "walk_synthesis.json", walk_report)
    write_json(output_dir / "tasks.json", {
        "generation_mode": task_mode,
        "tasks": [task.to_dict() for task in tasks],
        "rejected_tasks": rejected_tasks,
        "inconclusive_tasks": inconclusive_tasks,
    })
    summary = {
        "theme": bundle.theme,
        "theme_id": seed.theme_id,
        "llm_enabled": llm.enabled,
        "records": len(bundle.records),
        "entity_types": sorted({record.entity_type for record in bundle.records}),
        "complexification_rounds_requested": complexify_rounds,
        "complexification_events": len(bundle.complexification),
        "candidate_tools": len(candidate_tools),
        "tools": len(tools),
        "edges": len(graph.edges),
        "strict_chains": len(chains),
        "graph_construction_mode": graph.construction_mode,
        "raw_weighted_walks": walk_report["raw_walks"],
        "executed_walks": walk_report["executed_walks"],
        "task_generation_mode": task_mode,
        "semantic_review_status": "enabled" if llm.enabled else "not_run_no_llm",
        "successful_tasks": len(tasks),
        "five_run_attempted_tasks": five_run_attempted,
        "five_run_rejected_tasks": len(rejected_tasks),
        "five_run_inconclusive_tasks": len(inconclusive_tasks),
        "average_successful_task_steps": round(sum(task.validation["chain_steps"] for task in tasks) / len(tasks), 2) if tasks else 0,
        "five_run_statuses": sorted({task.validation.get("five_run_verification", {}).get("status", "not_requested") for task in tasks}),
        "all_tasks_non_leaking": all(task.validation["no_tool_name_leak"] for task in tasks) if tasks else None,
        "all_reference_plans_executed": all(task.validation["reference_plan_executed"] for task in tasks) if tasks else None,
    }
    write_json(output_dir / "summary.json", summary)
    print(f"[{seed.theme_id}] environment complete: {len(tasks)} tasks retained", flush=True)
    return summary


def run_batch(
    batch_size: int,
    output_root: Path,
    selection_seed: int | None = None,
    dry_run: bool = False,
    prepared_catalog: Path = Path("agent_world_mini/prepared_environments.json"),
    **run_options: object,
) -> dict[str, object]:
    pool_size = batch_size if dry_run else batch_size * 2
    candidates, discovery = select_prepared_themes(prepared_catalog, pool_size, output_root, selection_seed)
    results: list[dict[str, object]] = []
    succeeded = 0
    for seed in candidates:
        if succeeded >= batch_size:
            break
        item: dict[str, object] = {
            "theme_id": seed.theme_id,
            "theme": seed.seed_label,
            "source_url": seed.source_url,
            "documented_tools": len(seed.candidate_operations),
        }
        if dry_run:
            item["status"] = "selected"
            results.append(item)
            succeeded += 1
            continue
        try:
            print(f"[batch] starting {seed.theme_id}", flush=True)
            summary = run(None, output_root / output_slug(seed), theme_seed=seed, **run_options)
            has_tasks = int(summary.get("successful_tasks", 0)) > 0
            item["status"] = "completed_with_tasks" if has_tasks else "built_without_tasks"
            item["summary"] = summary
            if has_tasks:
                succeeded += 1
            print(f"[batch] {item['status']} {seed.theme_id} ({succeeded}/{batch_size} task-ready)", flush=True)
        except EnvironmentRejected as error:
            item["status"] = "rejected_insufficient_data"
            item["reason"] = str(error)
            print(f"[batch] rejected {seed.theme_id}: {error}", flush=True)
        except Exception as error:
            item["status"] = "failed"
            item["error"] = f"{type(error).__name__}: {error}"
            print(f"[batch] failed {seed.theme_id}: {item['error']}", flush=True)
        results.append(item)

    report = discovery | {
        "requested": batch_size,
        "selected": len(results),
        "task_ready" if not dry_run else "ready": succeeded,
        "dry_run": dry_run,
        "results": results,
    }
    write_json(output_root / "catalog_batch.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Small Agent-World style environment synthesis run")
    parser.add_argument("--theme")
    parser.add_argument("--theme-id", choices=sorted(CURATED_THEME_SEEDS))
    parser.add_argument("--source-url", help="Use one concrete MCP or tool documentation page as the theme source.")
    parser.add_argument("--batch-size", type=int, help="Select and run this many unseen environments from the prepared local catalogue.")
    parser.add_argument("--prepare-catalog", action="store_true", help="Fetch and organize the Smithery catalogue before generation.")
    parser.add_argument("--prepared-catalog", default="agent_world_mini/prepared_environments.json", help="Local prepared environment catalogue used by batch runs.")
    parser.add_argument("--catalog-query", default="", help="Optional Smithery query used only while preparing the catalogue.")
    parser.add_argument("--catalog-limit", type=int, default=0, help="Optional preparation limit; 0 prepares every matching entry.")
    parser.add_argument("--selection-seed", type=int, help="Optional repeatable random selection seed.")
    parser.add_argument("--dry-run", action="store_true", help="Select and deduplicate catalogue themes without building them.")
    parser.add_argument("--slug", default="country-market")
    parser.add_argument("--output-root", default="runs")
    parser.add_argument("--verify-five-runs", action="store_true", help="Run five independent solver rollouts and retain tasks with 2+ grounded correct answers.")
    parser.add_argument("--complexify-rounds", type=int, default=2, help="Number of source/data enrichment rounds for generic web research.")
    parser.add_argument("--max-semantic-reviews", type=int, default=0, help="Optional review cap for debugging only; 0 reviews every executed walk.")
    parser.add_argument("--max-tasks", type=int, default=0, help="Optional task cap for debugging only; 0 retains every reviewed task.")
    parser.add_argument("--react-max-steps", type=int, default=12, help="Per-rollout tool-call budget for the five-run ReAct check.")
    parser.add_argument("--max-candidates", type=int, default=128, help="Absolute per-environment candidate budget; adaptive generation usually stops earlier.")
    args = parser.parse_args()
    if args.prepare_catalog:
        summary = prepare_smithery_catalog(Path(args.prepared_catalog), args.catalog_query, args.catalog_limit, LLMClient.from_environment())
    elif args.batch_size is not None:
        if args.batch_size < 1:
            parser.error("--batch-size must be at least 1")
        summary = run_batch(
            args.batch_size,
            Path(args.output_root),
            args.selection_seed,
            args.dry_run,
            Path(args.prepared_catalog),
            verify_five_runs=args.verify_five_runs,
            complexify_rounds=args.complexify_rounds,
            max_semantic_reviews=args.max_semantic_reviews,
            max_tasks=args.max_tasks,
            react_max_steps=args.react_max_steps,
            max_candidates=args.max_candidates,
        )
    else:
        if not args.theme and not args.theme_id and not args.source_url:
            parser.error("one of --theme, --theme-id, --source-url, or --batch-size is required")
        summary = run(args.theme, Path(args.output_root) / args.slug, args.verify_five_runs, args.theme_id, args.complexify_rounds, args.max_semantic_reviews, args.max_tasks, args.react_max_steps, args.max_candidates, args.source_url)
    print(summary)
