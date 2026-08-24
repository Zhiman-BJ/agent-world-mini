from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agent_world_mini.seed_gen.catalog import output_slug, prepare_smithery_catalog, select_prepared_themes
from agent_world_mini.env_gen.assembler import assemble_environment_manifest
from agent_world_mini.env_gen.data_gen import DataGenerator
from agent_world_mini.env_gen.tool_gen.compiler import EnvironmentCompiler
from agent_world_mini.task_gen.dag_form.graph import ToolGraph
from agent_world_mini.utils.io import write_json
from agent_world_mini.utils.llm import LLMClient
from agent_world_mini.schemas.models import ResearchBundle, ToolSpec
from agent_world_mini.runtime.engine import LocalToolRuntime
from agent_world_mini.task_gen.dag_form.synthesizer import TaskSynthesizer
from agent_world_mini.seed_gen.themes import CURATED_THEME_SEEDS, ThemeSeed, resolve_theme
from agent_world_mini.env_gen.tool_gen.designer import ToolDesigner, ToolValidator
from agent_world_mini.task_gen.validation.five_run import FiveRunVerifier


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
    research_bundle: Path | None = None,
    deepseek_harness: bool = False,
    luna_review_export: bool = False,
) -> dict[str, object]:
    llm = LLMClient.from_environment()
    if luna_review_export and verify_five_runs:
        raise ValueError("Luna review export must be imported before five-run verification")
    if research_bundle is not None and deepseek_harness:
        raise ValueError("Use either research_bundle or deepseek_harness, not both")
    if deepseek_harness:
        seed = theme_seed or resolve_theme(theme, theme_id, source_url)
        print(f"[{seed.theme_id}] DeepSeek Harness researching {seed.source_url or seed.seed_label}", flush=True)
        bundle = DataGenerator(llm).generate(
            seed,
            deepseek_harness=True,
            output_file=output_dir / "research_bundle.json",
        )
        print(f"[{seed.theme_id}] DeepSeek Harness research complete: {len(bundle.records)} records", flush=True)
    elif research_bundle is not None:
        bundle = DataGenerator.load(research_bundle)
        metadata = bundle.theme_metadata
        seed = ThemeSeed(
            theme_id=str(metadata.get("theme_id") or f"external-{output_dir.name}"),
            seed_label=bundle.theme,
            source_type=str(metadata.get("source_type") or "external_research_bundle"),
            source_url=str(metadata.get("source_url") or ""),
            license_or_access_note=str(metadata.get("license_or_access_note") or "See research bundle sources."),
            coarse_route_label=str(metadata.get("coarse_route_label") or "externally-researched"),
            adapter=bundle.adapter,
        )
        print(f"[{seed.theme_id}] loaded external research bundle: {len(bundle.records)} records", flush=True)
    else:
        seed = theme_seed or resolve_theme(theme, theme_id, source_url)
        print(f"[{seed.theme_id}] researching {seed.source_url or seed.seed_label}", flush=True)
        bundle = DataGenerator(llm).generate(seed, complexify_rounds=complexify_rounds)
        print(f"[{seed.theme_id}] research complete: {len(bundle.records)} records", flush=True)
    EnvironmentCompiler(llm).prepare(bundle, use_agent=not luna_review_export)
    write_json(output_dir / "research_bundle.json", bundle.to_dict())
    write_json(output_dir / "theme_registry.json", {
        "selected_theme": seed.to_dict(),
        "available_curated_theme_ids": sorted(CURATED_THEME_SEEDS),
    })
    designer = ToolDesigner(llm)
    candidate_tools, tool_mode = designer.design(bundle, use_agent_selection=not luna_review_export)
    candidate_runtime = LocalToolRuntime(bundle, candidate_tools)
    tools, validation_reports = ToolValidator().validate(candidate_tools, candidate_runtime)
    if luna_review_export:
        designer.last_selection_report.update({"status": "locally_validated", "retained_tools": len(tools)})
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
    graph = ToolGraph(tools, llm, LocalToolRuntime(bundle, tools))
    chains = graph.chains()
    synthesizer = TaskSynthesizer(llm)
    tasks, task_mode, walk_report = synthesizer.synthesize_adaptive(
        bundle.theme,
        tools,
        bundle,
        graph.walks,
        max_candidates=max_candidates,
        max_semantic_reviews=max_semantic_reviews or None,
        max_tasks=max_tasks or None,
        candidate_only=luna_review_export,
    )
    print(f"[{seed.theme_id}] tasks complete: {len(tasks)} from {walk_report['executed_walks']} unique executed walks", flush=True)
    rejected_tasks = []
    inconclusive_tasks = []
    five_run_attempted = 0
    if verify_five_runs:
        # Rollouts need the public call schema, not designer evidence and test
        # metadata. Keeping this payload small materially reduces API latency.
        tool_contracts = tasks[0].available_tools if tasks else []
        def verify_one(index: int) -> tuple[int, dict[str, object]]:
            verifier = FiveRunVerifier(llm, LocalToolRuntime(bundle, tools), tool_contracts)
            return index, verifier.verify(tasks[index].to_dict(), tasks[index].reference_execution, max_steps=react_max_steps)
        # Verification is independent per task. Keep the verifier's own
        # two-rollout batches, and add a small task-level pool so a slow
        # gateway response for one task does not serialize the whole batch.
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(verify_one, index): index for index in range(len(tasks))}
            for future in as_completed(pending):
                index, result = future.result()
                tasks[index].validation["five_run_verification"] = result
                five_run_attempted += 1
                print(
                    f"[{seed.theme_id}] five-run task {index + 1}/{len(tasks)}: {result['status']} "
                    f"({result.get('successes', 0)} successes in {result.get('attempted_runs', 0)} runs)",
                    flush=True,
                )
        rejected_tasks = [task.to_dict() for task in tasks if task.validation["five_run_verification"]["status"] == "rejected"]
        inconclusive_tasks = [task.to_dict() for task in tasks if task.validation["five_run_verification"]["status"] == "inconclusive_infrastructure"]
        tasks = [task for task in tasks if task.validation["five_run_verification"]["status"] == "passed"]

    write_json(output_dir / "environment_manifest.json", assemble_environment_manifest(bundle, seed, tools))
    write_json(output_dir / "tool_graph.json", graph.to_dict(chains))
    write_json(output_dir / "walk_synthesis.json", walk_report)
    if luna_review_export:
        write_json(output_dir / "luna_review_packet.json", TaskSynthesizer.luna_review_packet(bundle, tools, walk_report))
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
        "resources": len(bundle.resources),
        "entity_types": sorted({record.entity_type for record in bundle.records}),
        "complexification_rounds_requested": complexify_rounds,
        "complexification_events": len(bundle.complexification),
        "candidate_tools": len(candidate_tools),
        "tools": len(tools),
        "state_mutating_tools": sum(tool.mutates_state for tool in tools),
        "python_tools": sum(tool.backend == "python" for tool in tools),
        "edges": len(graph.edges),
        "strict_chains": len(chains),
        "graph_construction_mode": graph.construction_mode,
        "raw_weighted_walks": walk_report["raw_walks"],
        "executed_walks": walk_report["executed_walks"],
        "task_generation_mode": task_mode,
        "semantic_review_status": "awaiting_luna_review" if luna_review_export else ("enabled" if llm.enabled else "not_run_no_llm"),
        "backend_model_calls": "disabled_luna_handoff" if luna_review_export else "configured_llm",
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


def apply_luna_reviews(output_dir: Path, reviews_path: Path, max_tasks: int = 0) -> dict[str, object]:
    bundle = ResearchBundle.from_dict(json.loads((output_dir / "research_bundle.json").read_text(encoding="utf-8")))
    tool_payload = json.loads((output_dir / "tool_specs.json").read_text(encoding="utf-8"))
    candidate_tools = [ToolSpec(**item) for item in tool_payload.get("tools", [])]
    packet = json.loads((output_dir / "luna_review_packet.json").read_text(encoding="utf-8"))
    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    designer = ToolDesigner(LLMClient.from_environment())
    tools = designer.apply_selection_result(bundle, candidate_tools, reviews)
    if not tools:
        raise EnvironmentRejected(str(designer.last_selection_report.get("reason") or "Luna retained no usable tools"))
    tasks, report = TaskSynthesizer(LLMClient.from_environment()).tasks_from_luna_reviews(
        tools, bundle, packet, reviews, max_tasks=max_tasks or None,
    )
    write_json(output_dir / "tool_specs.json", {
        "generation_mode": "luna_data_grounded_selection",
        "tools": [tool.to_dict() for tool in tools],
    })
    validation_path = output_dir / "tool_validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["selection"] = designer.last_selection_report
    validation["retained_tools"] = len(tools)
    write_json(validation_path, validation)
    graph = ToolGraph(tools, LLMClient.from_environment(), LocalToolRuntime(bundle, tools))
    write_json(output_dir / "tool_graph.json", graph.to_dict(graph.chains()))
    write_json(output_dir / "luna_review_result.json", report)
    write_json(output_dir / "tasks.json", {
        "generation_mode": "luna_reviewed_graph_walks",
        "tasks": [task.to_dict() for task in tasks],
        "rejected_tasks": [],
        "inconclusive_tasks": [],
    })
    summary_path = output_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    summary.update({
        "task_generation_mode": "luna_reviewed_graph_walks",
        "semantic_review_status": "completed_by_luna",
        "backend_model_calls": "disabled_luna_handoff",
        "tools": len(tools),
        "state_mutating_tools": sum(tool.mutates_state for tool in tools),
        "python_tools": sum(tool.backend == "python" for tool in tools),
        "edges": len(graph.edges),
        "strict_chains": len(graph.chains()),
        "successful_tasks": len(tasks),
        "average_successful_task_steps": round(sum(task.validation["chain_steps"] for task in tasks) / len(tasks), 2) if tasks else 0,
        "five_run_attempted_tasks": 0,
        "five_run_rejected_tasks": 0,
        "five_run_inconclusive_tasks": 0,
        "five_run_statuses": ["not_requested"],
        "all_tasks_non_leaking": all(task.validation["no_tool_name_leak"] for task in tasks) if tasks else None,
        "all_reference_plans_executed": all(task.validation["reference_plan_executed"] for task in tasks) if tasks else None,
        "luna_review": report,
    })
    write_json(summary_path, summary)
    print(f"[{output_dir.name}] Luna reviews imported: {len(tasks)} tasks accepted", flush=True)
    return summary


def run_batch(
    batch_size: int,
    output_root: Path,
    selection_seed: int | None = None,
    dry_run: bool = False,
    prepared_catalog: Path = Path("agent_world_mini/seed_gen/prepared_environments.json"),
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
            awaiting_luna = summary.get("semantic_review_status") == "awaiting_luna_review"
            has_tasks = int(summary.get("successful_tasks", 0)) > 0
            item["status"] = "awaiting_luna_review" if awaiting_luna else ("completed_with_tasks" if has_tasks else "built_without_tasks")
            item["summary"] = summary
            if has_tasks or awaiting_luna:
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
    parser.add_argument("--research-bundle", type=Path, help="Use a research_bundle.json produced by an external research agent and skip built-in web research.")
    parser.add_argument("--deepseek-harness", action="store_true", help="Use the installed DeepSeek Harness as the Research Agent.")
    parser.add_argument("--luna-review-export", action="store_true", help="Build and execute candidates without API model calls, then export luna_review_packet.json.")
    parser.add_argument("--luna-reviews", type=Path, help="Import reviews written by a Luna subagent into an existing exported environment.")
    parser.add_argument("--batch-size", type=int, help="Select and run this many unseen environments from the prepared local catalogue.")
    parser.add_argument("--prepare-catalog", action="store_true", help="Fetch and organize the Smithery catalogue before generation.")
    parser.add_argument("--prepared-catalog", default="agent_world_mini/seed_gen/prepared_environments.json", help="Local prepared environment catalogue used by batch runs.")
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
    if args.luna_reviews is not None:
        summary = apply_luna_reviews(Path(args.output_root) / args.slug, args.luna_reviews, args.max_tasks)
    elif args.prepare_catalog:
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
            deepseek_harness=args.deepseek_harness,
            luna_review_export=args.luna_review_export,
        )
    else:
        if not args.theme and not args.theme_id and not args.source_url and not args.research_bundle:
            parser.error("one of --theme, --theme-id, --source-url, --research-bundle, or --batch-size is required")
        summary = run(
            args.theme,
            Path(args.output_root) / args.slug,
            args.verify_five_runs,
            args.theme_id,
            args.complexify_rounds,
            args.max_semantic_reviews,
            args.max_tasks,
            args.react_max_steps,
            args.max_candidates,
            args.source_url,
            research_bundle=args.research_bundle,
            deepseek_harness=args.deepseek_harness,
            luna_review_export=args.luna_review_export,
        )
    print(summary)
