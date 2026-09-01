"""DataGen entry point: prepare one run, then execute five business steps."""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from env_gen.data_gen.analysis.environment_quality import EnvironmentQualityPolicy
from env_gen.data_gen.config import CollectionPolicy, DEFAULT_OSS_OUTPUT_ROOT, DataGenConfig
from env_gen.data_gen.steps.step0_prepare_run import prepare_generation_run
from env_gen.data_gen.steps.step1_research_scenario import ScenarioResearchError, run_scenario_research
from env_gen.data_gen.steps.step2_explore_sources import SourceExplorationError, run_source_exploration
from env_gen.data_gen.steps.step3_integrate_data import IntegrationFinalizationError, run_integration_phase
from env_gen.data_gen.steps.step4_freeze_environment import EnvironmentFreezeError, freeze_environment
from env_gen.data_gen.steps.step5_validate_and_publish import FinalValidationError, validate_and_publish
from utils.search_agent.codex import CodexAgentClient


class CodexAgent(Protocol):
    def run(self, prompt: str, *, working_directory: Path) -> str: ...


AgentRunner = Callable[[str, int, tuple[Path, ...]], str]


def _make_agent_runner(
    codex: CodexAgent,
    *,
    staging: Path,
    policy: CollectionPolicy,
    started: float,
) -> AgentRunner:
    """把 Agent 客户端适配为各阶段共用的总预算受控调用。"""

    def invoke_agent(prompt: str, phase_seconds: int, required_paths: tuple[Path, ...]) -> str:
        remaining = policy.max_total_seconds - int(time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError(f"环境生成超过总预算 {policy.max_total_seconds} 秒")
        previous_timeout = getattr(codex, "timeout_seconds", None)
        if isinstance(previous_timeout, int):
            codex.timeout_seconds = min(previous_timeout, phase_seconds, remaining)  # type: ignore[attr-defined]
        try:
            run_until_files = getattr(codex, "run_until_files", None)
            if callable(run_until_files):
                return run_until_files(
                    prompt,
                    working_directory=staging,
                    required_paths=required_paths,
                )
            return codex.run(prompt, working_directory=staging)
        finally:
            if isinstance(previous_timeout, int):
                codex.timeout_seconds = previous_timeout  # type: ignore[attr-defined]

    return invoke_agent


@dataclass(frozen=True)
class DataGenerationResult:
    output_dir: Path
    environment_path: Path
    environment_context_path: Path
    state_path: Path
    seed_global_id: str
    seed_sha256: str
    scenario_research_path: Path
    source_plan_path: Path
    source_inventory_path: Path
    integration_plan_path: Path
    integration_profile_path: Path
    quality_profile_path: Path
    source_manifest_path: Path
    validation_path: Path
    quality_tier: str
    integration_tier: str
    scenario_research_agent_calls: int
    exploration_agent_calls: int
    integration_agent_calls: int
    integration_assessment_runs: int
    elapsed_seconds: float

    @property
    def workspace_path(self) -> Path:
        """兼容旧调用方；v2 最终业务状态位于 state。"""
        return self.state_path

    @property
    def provenance_path(self) -> Path:
        return self.source_manifest_path

    @property
    def research_report_path(self) -> Path:
        return self.scenario_research_path

    @property
    def data_profile_path(self) -> Path:
        return self.source_inventory_path

    @property
    def collection_agent_calls(self) -> int:
        return self.exploration_agent_calls + self.integration_agent_calls

    @property
    def profile_runs(self) -> int:
        return self.integration_assessment_runs + 2

    @property
    def repair_rounds(self) -> int:
        return max(0, self.integration_agent_calls - 1)


class DataGenerationError(RuntimeError):
    """环境未通过某个确定性阶段门。"""


class InsufficientPublicDataError(DataGenerationError):
    """Step 2 证明所有核心公开来源不可用。"""


def _safe_directory_name(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else "_" for character in value.strip()
    )
    return "_".join(part for part in normalized.split("_") if part) or "environment"


def _create_staging(
    *,
    explicit_output_dir: Path | None,
    output_root: Path,
    safe_global_id: str,
    overwrite: bool,
) -> Path:
    if explicit_output_dir is not None:
        existing = [explicit_output_dir] if explicit_output_dir.exists() else []
        parent = explicit_output_dir.parent
        prefix = f".{explicit_output_dir.name}.building-"
    else:
        existing = [
            path for path in (
                output_root / "rich" / safe_global_id,
                output_root / "not_rich" / safe_global_id,
            ) if path.exists()
        ]
        parent = output_root / ".building"
        prefix = f".{safe_global_id}.building-"
    if existing and not overwrite:
        raise FileExistsError(f"输出目录已经存在：{existing[0]}")
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=parent))


def _preserve_failed(
    staging: Path,
    *,
    error: Exception,
    preserve: bool,
    explicit_output_dir: Path | None,
    output_root: Path,
    safe_global_id: str,
) -> None:
    if not preserve or not staging.exists() or not any(staging.iterdir()):
        shutil.rmtree(staging, ignore_errors=True)
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if explicit_output_dir is not None:
        failed = explicit_output_dir.with_name(
            f"{explicit_output_dir.name}.failed-{timestamp}-{staging.name[-6:]}"
        )
    else:
        failed_root = output_root / "failed"
        failed_root.mkdir(parents=True, exist_ok=True)
        failed = failed_root / f"{safe_global_id}-{timestamp}-{staging.name[-6:]}"
    staging.replace(failed)
    error.add_note(f"未发布的生成现场保留在：{failed}")


def run_pipeline(
    config: DataGenConfig,
    *,
    agent: CodexAgent | None = None,
    collection_policy: CollectionPolicy | None = None,
    environment_quality_policy: EnvironmentQualityPolicy | None = None,
) -> DataGenerationResult:
    """执行“准备 -> 场景研究 -> 来源探索 -> 集成 -> 冻结 -> 发布”。"""

    policy = collection_policy or CollectionPolicy(
        max_total_seconds=config.timeout_seconds,
        max_collection_rounds=config.max_collection_rounds,
    )
    if collection_policy is not None and collection_policy.max_total_seconds != config.timeout_seconds:
        policy = replace(collection_policy, max_total_seconds=config.timeout_seconds)
    quality_policy = environment_quality_policy or EnvironmentQualityPolicy()
    explicit_output = config.output_dir.resolve() if config.output_dir else None
    output_root = (config.output_root or DEFAULT_OSS_OUTPUT_ROOT).resolve()
    safe_global_id = _safe_directory_name(config.global_id)
    staging = _create_staging(
        explicit_output_dir=explicit_output,
        output_root=output_root,
        safe_global_id=safe_global_id,
        overwrite=config.overwrite,
    )
    started = time.monotonic()
    codex = agent or CodexAgentClient(
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        sandbox="danger-full-access",
        enable_web_search=config.enable_web_search,
        network_access=True,
        reasoning_effort=config.reasoning_effort,
        disabled_mcp_servers=("openaiDeveloperDocs",),
    )
    if isinstance(codex, CodexAgentClient) and codex.log_directory is None:
        codex.log_directory = staging / ".datagen/agent_runs"
    agent_runner = _make_agent_runner(codex, staging=staging, policy=policy, started=started)

    try:
        seed_sha256 = prepare_generation_run(
            staging,
            config,
            limits=asdict(policy),
            quality=asdict(quality_policy),
        )
        try:
            _, research_agent_calls = run_scenario_research(
                run_dir=staging,
                agent_runner=agent_runner,
            )
        except ScenarioResearchError as error:
            raise DataGenerationError(str(error)) from error
        try:
            exploration = run_source_exploration(
                run_dir=staging,
                collection_policy=policy,
                agent_runner=agent_runner,
            )
        except SourceExplorationError as error:
            raise DataGenerationError(str(error)) from error
        if exploration.result == "insufficient_public_data":
            raise InsufficientPublicDataError("Step 2 未找到任何可用核心公开来源")
        try:
            integration = run_integration_phase(
                run_dir=staging,
                collection_policy=policy,
                agent_runner=agent_runner,
            )
        except IntegrationFinalizationError as error:
            raise DataGenerationError(str(error)) from error
        try:
            frozen = freeze_environment(staging)
        except EnvironmentFreezeError as error:
            raise DataGenerationError(str(error)) from error
        quality_tier = str(frozen["quality_profile"]["quality_tier"])
        final_output = explicit_output or output_root / quality_tier / safe_global_id
        try:
            published = validate_and_publish(
                staging,
                final_output_dir=final_output,
                overwrite=config.overwrite,
            )
        except FinalValidationError as error:
            raise DataGenerationError(str(error)) from error
        output_dir = Path(published["output_dir"])
        if config.overwrite and explicit_output is None:
            opposite = "not_rich" if quality_tier == "rich" else "rich"
            shutil.rmtree(output_root / opposite / safe_global_id, ignore_errors=True)
        return DataGenerationResult(
            output_dir=output_dir,
            environment_path=output_dir / "environment.json",
            environment_context_path=output_dir / "environment.md",
            state_path=output_dir / "state",
            seed_global_id=config.global_id,
            seed_sha256=seed_sha256,
            scenario_research_path=output_dir / "provenance/scenario_research.json",
            source_plan_path=output_dir / "provenance/source_plan.json",
            source_inventory_path=output_dir / "provenance/source_inventory.json",
            integration_plan_path=output_dir / "provenance/integration_plan.json",
            integration_profile_path=output_dir / "provenance/integration_profile.json",
            quality_profile_path=output_dir / "provenance/quality_profile.json",
            source_manifest_path=output_dir / "provenance/source_manifest.json",
            validation_path=output_dir / "validation.json",
            quality_tier=quality_tier,
            integration_tier=str(published["integration_tier"]),
            scenario_research_agent_calls=research_agent_calls,
            exploration_agent_calls=exploration.agent_calls,
            integration_agent_calls=integration.agent_calls,
            integration_assessment_runs=integration.assessment_runs,
            elapsed_seconds=time.monotonic() - started,
        )
    except Exception as error:
        _preserve_failed(
            staging,
            error=error,
            preserve=config.preserve_failed,
            explicit_output_dir=explicit_output,
            output_root=output_root,
            safe_global_id=safe_global_id,
        )
        raise


__all__ = ["DataGenerationError", "DataGenerationResult", "InsufficientPublicDataError", "run_pipeline"]
