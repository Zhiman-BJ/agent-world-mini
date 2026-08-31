from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from env_gen.data_gen.acquisition import (
    DEFAULT_OSS_OUTPUT_ROOT,
    AcquisitionPolicy,
    SourceInventoryValidator,
    acquisition_frontier,
)
from env_gen.data_gen.policy import ResearchPolicy
from env_gen.data_gen.validator import (
    INTERNAL_SCHEMA_DIR,
    EnvironmentPackageValidator,
    ValidationIssue,
    ValidationReport,
)
from env_gen.data_gen.quality import (
    RichnessPolicy,
    quality_gain,
)
from env_gen.data_gen.steps import (
    build_collection_prompt,
    build_environment_description_prompt,
    build_expansion_prompt,
    build_repair_prompt,
    build_research_request,
    evaluate_data_richness,
    profile_collected_data,
    publish_environment,
    validate_environment,
)
from utils.search_agent.codex import CodexAgentClient


# 调研需要先理解种子范围、来源约束和文件契约，因此默认使用 Terra 的高推理档位。
# 调用方仍可通过 ``model`` 和 ``reasoning_effort`` 显式覆盖。
DEFAULT_RESEARCH_MODEL = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "high"


class ResearchAgent(Protocol):
    """DataGen 只依赖这一项 Agent 能力，便于以后替换 Codex。"""

    def run(self, prompt: str, *, working_directory: Path) -> str: ...


@dataclass(frozen=True)
class DataGenerationResult:
    """一次成功生成的环境数据包。"""

    output_dir: Path
    environment_path: Path
    workspace_path: Path
    research_request_path: Path
    provenance_path: Path
    research_report_path: Path
    source_inventory_path: Path
    data_profile_path: Path
    quality_profile_path: Path
    validation_path: Path
    quality_tier: str
    collection_rounds: int
    repair_rounds: int
    elapsed_seconds: float


class DataGenerationError(RuntimeError):
    """Agent 多次修复后仍未生成合法环境。"""

    def __init__(self, report: ValidationReport):
        self.report = report
        details = "; ".join(issue.message for issue in report.errors[:8])
        super().__init__(f"环境数据包校验失败：{details or '未知错误'}")


class InsufficientPublicDataError(DataGenerationError):
    """核心种子要求找不到合法公开数据，按协议停止而不是合成记录。"""


class DataGenerator:
    """让研究 Agent 直接生成不带工具的最终环境数据包。

    Agent 自己读取种子和协议、检索真实数据并写出环境。Python 只负责输入
    边界、确定性校验、修复循环和最终发布，不参与业务数据编造。
    """

    def __init__(
        self,
        agent: ResearchAgent | None = None,
        *,
        max_repair_rounds: int = 2,
        max_collection_rounds: int | None = None,
        research_policy: ResearchPolicy | None = None,
        acquisition_policy: AcquisitionPolicy | None = None,
        richness_policy: RichnessPolicy | None = None,
        preserve_failed: bool = True,
        enable_web_search: bool = False,
        use_metadata_agent: bool | None = None,
        model: str | None = DEFAULT_RESEARCH_MODEL,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        disabled_mcp_servers: tuple[str, ...] = ("openaiDeveloperDocs",),
    ) -> None:
        if max_repair_rounds < 0:
            raise ValueError("max_repair_rounds 不能小于 0")
        if max_collection_rounds is not None and max_collection_rounds <= 0:
            raise ValueError("max_collection_rounds 必须大于 0")
        self.agent = agent or CodexAgentClient(
            model=model,
            sandbox="workspace-write",
            # 搜索 MCP 在不同机器上的可用性不稳定；默认让 Agent 使用
            # 沙箱内的 curl/git 等网络命令，避免搜索服务 403 时整阶段挂起。
            enable_web_search=enable_web_search,
            network_access=True,
            reasoning_effort=reasoning_effort,
            disabled_mcp_servers=disabled_mcp_servers,
        )
        self.max_repair_rounds = max_repair_rounds
        self.research_policy = research_policy or ResearchPolicy()
        self.acquisition_policy = acquisition_policy or AcquisitionPolicy()
        # research_request 与采集 Prompt 必须使用同一组硬预算。调用方缩小
        # ResearchPolicy 时，采集控制器同步取更严格值，避免 Agent 下载后
        # 才被 checkpoint Validator 拒绝。
        self.acquisition_policy = replace(
            self.acquisition_policy,
            max_raw_bytes=min(
                self.acquisition_policy.max_raw_bytes,
                self.research_policy.max_download_bytes,
            ),
            max_workspace_bytes=min(
                self.acquisition_policy.max_workspace_bytes,
                self.research_policy.max_workspace_bytes,
            ),
            max_raw_files=min(
                self.acquisition_policy.max_raw_files,
                self.research_policy.max_raw_files,
            ),
            max_sources=min(
                self.acquisition_policy.max_sources,
                self.research_policy.max_sources,
            ),
        )
        if max_collection_rounds is not None:
            self.acquisition_policy = replace(
                self.acquisition_policy,
                max_collection_rounds=max_collection_rounds,
            )
        self.richness_policy = richness_policy or RichnessPolicy()
        self.preserve_failed = preserve_failed
        # 兼容旧调用签名。环境语义描述现在是必经阶段，不再提供跳过后由
        # Python 猜测最终语义的模式。
        if use_metadata_agent is False:
            raise ValueError("环境语义描述 Agent 现在是必经阶段，不能关闭")

    def generate(
        self,
        *,
        seed_path: Path,
        seed_id: str,
        schema_path: Path,
        output_dir: Path | None = None,
        output_root: Path | None = None,
        contract_path: Path | None = None,
        overwrite: bool = False,
    ) -> DataGenerationResult:
        """生成并发布一个环境数据包。

        ``seed_path`` 可以包含多个主题，但 ``seed_id`` 必须唯一指向其中一个。
        Agent 只能在临时工作目录内写入。显式 ``output_dir`` 用于测试或
        单次调试；未传时按质量自动发布到 OSS 根目录的 rich/not_rich。
        """

        seed_path = seed_path.resolve()
        schema_path = schema_path.resolve()
        validation_schema_path = self._resolve_validation_schema(schema_path)
        if output_dir is not None and output_root is not None:
            raise ValueError("output_dir 和 output_root 不能同时传入")
        explicit_output_dir = output_dir.resolve() if output_dir is not None else None
        classification_root = (
            output_root.resolve()
            if output_root is not None
            else DEFAULT_OSS_OUTPUT_ROOT.resolve()
        )
        if contract_path is None:
            inferred_contract = schema_path.with_name("环境契约-v1.0.md")
            if not inferred_contract.is_file() and schema_path.parent.name == "validation":
                inferred_contract = schema_path.parent.parent / "环境契约-v1.0.md"
            contract_path = inferred_contract if inferred_contract.is_file() else None
        contract_path = contract_path.resolve() if contract_path else None

        seed = self._check_inputs(
            seed_path,
            seed_id,
            schema_path,
            contract_path,
            validation_schema_path,
        )
        safe_seed_id = self._safe_directory_name(seed_id)
        if explicit_output_dir is not None:
            existing_outputs = [explicit_output_dir] if explicit_output_dir.exists() else []
            staging_parent = explicit_output_dir.parent
            staging_prefix = f".{explicit_output_dir.name}.building-"
        else:
            existing_outputs = [
                candidate
                for candidate in (
                    classification_root / "rich" / safe_seed_id,
                    classification_root / "not_rich" / safe_seed_id,
                )
                if candidate.exists()
            ]
            staging_parent = classification_root / ".building"
            staging_prefix = f".{safe_seed_id}.building-"
        if existing_outputs and not overwrite:
            raise FileExistsError(f"输出目录已经存在：{existing_outputs[0]}")

        try:
            staging_parent.mkdir(parents=True, exist_ok=True)
        except PermissionError as error:
            raise PermissionError(
                f"环境输出根目录不可写：{staging_parent}；请修正 OSS 挂载 UID/GID 或写权限"
            ) from error
        try:
            staging_path = Path(
                tempfile.mkdtemp(
                    prefix=staging_prefix,
                    dir=staging_parent,
                )
            )
        except PermissionError as error:
            raise PermissionError(
                f"环境输出根目录不可写：{staging_parent}；请修正 OSS 挂载 UID/GID 或写权限"
            ) from error
        provenance_dir = staging_path / "provenance"
        provenance_dir.mkdir(parents=True, exist_ok=True)
        request_path = provenance_dir / "research_request.json"
        research_request = build_research_request(
            seed,
            self.research_policy,
            request_path,
        )
        validator = EnvironmentPackageValidator(
            validation_schema_path,
            seed=seed,
            research_policy=self.research_policy,
        )
        started_at = time.monotonic()
        checkpoint_path = provenance_dir / "data_checkpoint.json"
        source_inventory_path = provenance_dir / "source_inventory.json"
        data_profile_path = provenance_dir / "data_profile.json"
        quality_profile_path = provenance_dir / "quality_profile.json"
        done_path = provenance_dir / "agent_done.json"
        source_inventory_schema_path = INTERNAL_SCHEMA_DIR / "source_inventory.schema.json"
        quality_profile_schema_path = INTERNAL_SCHEMA_DIR / "quality_profile.schema.json"
        for label, path in (
            ("数据面清单 Schema", source_inventory_schema_path),
            ("质量画像 Schema", quality_profile_schema_path),
        ):
            if not path.is_file():
                raise FileNotFoundError(f"{label}不存在：{path}")
        source_inventory_validator = SourceInventoryValidator(
            source_inventory_schema_path
        )

        try:
            checkpoint_error: Exception | None = None
            try:
                self._run_agent(
                    build_collection_prompt(
                        seed_path=seed_path,
                        seed_id=seed_id,
                        schema_path=schema_path,
                        contract_path=contract_path,
                        request_path=request_path,
                        source_inventory_path=source_inventory_path,
                        source_inventory_schema_path=source_inventory_schema_path,
                        checkpoint_path=checkpoint_path,
                        policy=self.acquisition_policy,
                    ),
                    staging_path,
                    started_at,
                    phase_seconds=self.research_policy.data_phase_seconds,
                    stop_when=(checkpoint_path,),
                )
            except Exception as error:
                checkpoint_error = error

            # 如果 Agent 调用没有完成提交，仍可对已经落盘的文件做一次
            # 纯确定性盘点。它不判断业务覆盖、不生成记录，只把非空文件列入
            # checkpoint；真正的覆盖和来源校验仍由后续 Validator/metadata 阶段负责。
            if not checkpoint_path.is_file():
                self._write_inventory_checkpoint(
                    staging_path,
                    research_request=research_request,
                    checkpoint_path=checkpoint_path,
                )

            checkpoint_report = validator.validate_data_checkpoint(staging_path)
            if any(issue.code == "insufficient_public_data" for issue in checkpoint_report.errors):
                raise InsufficientPublicDataError(checkpoint_report)
            if not checkpoint_report.valid:
                if checkpoint_error is not None:
                    checkpoint_report.errors.insert(
                        0,
                        ValidationIssue(
                            code="data_collection_failed",
                            path="$.agent.data_collection",
                            message=f"数据采集阶段异常：{checkpoint_error}",
                        ),
                    )
                raise DataGenerationError(checkpoint_report)

            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            source_inventory = self._validated_source_inventory(
                source_inventory_validator,
                source_inventory_path,
                research_request=research_request,
                checkpoint=checkpoint,
            )
            collection_rounds = 1
            quality_history_dir = provenance_dir / "quality_history"
            acquisition_rounds_dir = provenance_dir / "acquisition_rounds"
            quality_history_dir.mkdir(parents=True, exist_ok=True)
            acquisition_rounds_dir.mkdir(parents=True, exist_ok=True)
            data_profile = profile_collected_data(
                staging_path,
                research_request=research_request,
                checkpoint=checkpoint,
                output_path=data_profile_path,
            )
            quality_profile, profile_errors = evaluate_data_richness(
                staging_path,
                research_request=research_request,
                checkpoint=checkpoint,
                source_inventory=source_inventory,
                data_profile=data_profile,
                policy=self.richness_policy,
                schema_path=quality_profile_schema_path,
                output_path=quality_profile_path,
                history_path=quality_history_dir / "round_01.json",
            )
            if profile_errors:
                raise self._quality_profile_error(profile_errors)
            stagnant_rounds = 0
            post_rich_rounds = 0

            # 首轮采集不是最终答案。只要仍有质量缺口或数据面未完成，就把
            # 明确 frontier 交给 Agent 继续扩展；旧业务文件保持只读，新分页
            # 必须写入新 raw 文件，便于逐轮审计和回放。
            while collection_rounds < self.acquisition_policy.max_collection_rounds:
                frontier = acquisition_frontier(source_inventory, quality_profile)
                if quality_profile["quality_tier"] == "rich":
                    if not frontier or post_rich_rounds >= self.acquisition_policy.post_rich_rounds:
                        break
                elif stagnant_rounds >= self.acquisition_policy.diminishing_rounds:
                    break

                next_round = collection_rounds + 1
                archive_checkpoint = (
                    acquisition_rounds_dir
                    / f"data_checkpoint_round_{collection_rounds:02d}.json"
                )
                archive_inventory = (
                    acquisition_rounds_dir
                    / f"source_inventory_round_{collection_rounds:02d}.json"
                )
                frontier_path = (
                    acquisition_rounds_dir / f"frontier_round_{next_round:02d}.json"
                )
                shutil.copy2(checkpoint_path, archive_checkpoint)
                shutil.copy2(source_inventory_path, archive_inventory)
                frontier_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "round": next_round,
                            "actions": frontier,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                previous_snapshot = self._snapshot_business_files(staging_path)
                previous_profile = quality_profile
                checkpoint_path.unlink()
                done_path.unlink(missing_ok=True)

                expansion_error: Exception | None = None
                try:
                    self._run_agent(
                        build_expansion_prompt(
                            seed_path=seed_path,
                            seed_id=seed_id,
                            schema_path=schema_path,
                            contract_path=contract_path,
                            request_path=request_path,
                            source_inventory_path=source_inventory_path,
                            source_inventory_schema_path=source_inventory_schema_path,
                            previous_checkpoint_path=archive_checkpoint,
                            frontier_path=frontier_path,
                            checkpoint_path=checkpoint_path,
                            collection_round=next_round,
                        ),
                        staging_path,
                        started_at,
                        phase_seconds=self.research_policy.repair_phase_seconds,
                        stop_when=(checkpoint_path,),
                    )
                except Exception as error:
                    expansion_error = error
                if not checkpoint_path.is_file():
                    report = ValidationReport(
                        errors=[
                            ValidationIssue(
                                code="expansion_checkpoint_missing",
                                path="$.agent.data_expansion",
                                message=f"第 {next_round} 轮没有提交 data_checkpoint.json"
                                + (f"：{expansion_error}" if expansion_error else ""),
                            )
                        ]
                    )
                    raise DataGenerationError(report)

                checkpoint_report = validator.validate_data_checkpoint(staging_path)
                checkpoint_report.errors.extend(
                    self._validate_acquisition_append_only(
                        staging_path,
                        previous_snapshot,
                    )
                )
                if self._insufficient_public_data(checkpoint_report):
                    raise InsufficientPublicDataError(checkpoint_report)
                if not checkpoint_report.valid:
                    if expansion_error is not None:
                        checkpoint_report.errors.insert(
                            0,
                            ValidationIssue(
                                code="data_expansion_failed",
                                path="$.agent.data_expansion",
                                message=f"第 {next_round} 轮扩展异常：{expansion_error}",
                            ),
                        )
                    raise DataGenerationError(checkpoint_report)

                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                source_inventory = self._validated_source_inventory(
                    source_inventory_validator,
                    source_inventory_path,
                    research_request=research_request,
                    checkpoint=checkpoint,
                )
                data_profile = profile_collected_data(
                    staging_path,
                    research_request=research_request,
                    checkpoint=checkpoint,
                    output_path=data_profile_path,
                )
                quality_profile, profile_errors = evaluate_data_richness(
                    staging_path,
                    research_request=research_request,
                    checkpoint=checkpoint,
                    source_inventory=source_inventory,
                    data_profile=data_profile,
                    policy=self.richness_policy,
                    schema_path=quality_profile_schema_path,
                    output_path=quality_profile_path,
                    history_path=quality_history_dir / f"round_{next_round:02d}.json",
                )
                if profile_errors:
                    raise self._quality_profile_error(profile_errors)
                gain = quality_gain(previous_profile, quality_profile)
                no_material_gain = (
                    gain["new_capability_atoms"] <= 0
                    and gain["new_relations"] <= 0
                    and gain["new_chain_shapes"] <= 0
                    and gain["task_capacity_growth_percent"]
                    < self.acquisition_policy.minimum_task_growth_percent
                )
                stagnant_rounds = stagnant_rounds + 1 if no_material_gain else 0
                if previous_profile["quality_tier"] == "rich":
                    post_rich_rounds += 1
                collection_rounds = next_round

            # Step 06：数据已经冻结。Agent 负责业务语义和环境声明，Python
            # 不再根据字段名猜测并覆盖 environment.json。
            data_snapshot = self._snapshot_business_files(staging_path)
            done_path.unlink(missing_ok=True)
            description_error: Exception | None = None
            try:
                self._run_agent(
                    build_environment_description_prompt(
                        seed_path=seed_path,
                        seed_id=seed_id,
                        schema_path=schema_path,
                        contract_path=contract_path,
                        request_path=request_path,
                        checkpoint_path=checkpoint_path,
                        source_inventory_path=source_inventory_path,
                        data_profile_path=data_profile_path,
                        quality_profile_path=quality_profile_path,
                        done_path=done_path,
                    ),
                    staging_path,
                    started_at,
                    phase_seconds=self.research_policy.metadata_phase_seconds,
                    stop_when=(done_path,),
                )
            except Exception as error:
                description_error = error

            # Step 07：只验证 Agent 的语义声明和真实文件，不生成替代描述。
            report = validate_environment(validator, staging_path)
            report.errors.extend(
                self._validate_environment_description_boundary(
                    staging_path,
                    data_snapshot,
                    allowed_new_files=set(),
                    allowed_removed_files=set(),
                )
            )
            if description_error is not None:
                report.errors.insert(
                    0,
                    ValidationIssue(
                        "environment_description_failed",
                        "$.agent.environment_description",
                        f"环境语义描述阶段异常：{description_error}",
                    ),
                )
            repair_rounds = 0
            if self._insufficient_public_data(report):
                raise InsufficientPublicDataError(report)
            while not report.valid and repair_rounds < self.max_repair_rounds:
                self._remaining_seconds(started_at)
                repair_rounds += 1
                error_path = staging_path / "validation_errors.json"
                error_path.write_text(
                    json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                done_path.unlink(missing_ok=True)
                self._run_agent(
                    build_repair_prompt(
                        seed_path=seed_path,
                        seed_id=seed_id,
                        schema_path=schema_path,
                        contract_path=contract_path,
                        request_path=request_path,
                        checkpoint_path=checkpoint_path,
                        error_path=error_path,
                        repair_round=repair_rounds,
                        done_path=done_path,
                    ),
                    staging_path,
                    started_at,
                    phase_seconds=self.research_policy.repair_phase_seconds,
                    stop_when=(done_path,),
                )
                report = validate_environment(validator, staging_path)
                report.errors.extend(
                    self._validate_environment_description_boundary(
                        staging_path,
                        data_snapshot,
                        allowed_new_files=set(),
                        allowed_removed_files=set(),
                    )
                )
                if self._insufficient_public_data(report):
                    raise InsufficientPublicDataError(report)

            if not report.valid:
                raise DataGenerationError(report)

            validator.finalize_provenance(staging_path)
            report = validate_environment(validator, staging_path)
            if not report.valid:
                raise DataGenerationError(report)

            # 环境描述/修复 Agent 永远不能决定质量等级。发布前重新从最终
            # checkpoint 和 workspace 计算一次，覆盖任何中间文件内容。
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            source_inventory = self._validated_source_inventory(
                source_inventory_validator,
                source_inventory_path,
                research_request=research_request,
                checkpoint=checkpoint,
            )
            data_profile = profile_collected_data(
                staging_path,
                research_request=research_request,
                checkpoint=checkpoint,
                output_path=data_profile_path,
            )
            quality_profile, profile_errors = evaluate_data_richness(
                staging_path,
                research_request=research_request,
                checkpoint=checkpoint,
                source_inventory=source_inventory,
                data_profile=data_profile,
                policy=self.richness_policy,
                schema_path=quality_profile_schema_path,
                output_path=quality_profile_path,
                history_path=quality_history_dir / "final.json",
            )
            if profile_errors:
                raise self._quality_profile_error(profile_errors)

            (staging_path / "validation_errors.json").unlink(missing_ok=True)
            done_path.unlink(missing_ok=True)
            validation_path = staging_path / "validation.json"
            validation_path.write_text(
                json.dumps(
                    {
                        **report.to_dict(),
                        "validated_at": datetime.now(timezone.utc).isoformat(),
                        "quality_tier": quality_profile["quality_tier"],
                        "collection_rounds": collection_rounds,
                        "repair_rounds": repair_rounds,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            quality_tier = str(quality_profile["quality_tier"])
            final_output_dir = explicit_output_dir or (
                classification_root / quality_tier / safe_seed_id
            )
            if overwrite and explicit_output_dir is None:
                opposite_tier = "not_rich" if quality_tier == "rich" else "rich"
                shutil.rmtree(
                    classification_root / opposite_tier / safe_seed_id,
                    ignore_errors=True,
                )
            final_output_dir = publish_environment(
                staging_path,
                final_output_dir=final_output_dir,
                overwrite=overwrite,
            )
            return DataGenerationResult(
                output_dir=final_output_dir,
                environment_path=final_output_dir / "environment.json",
                workspace_path=final_output_dir / "workspace",
                research_request_path=final_output_dir / "provenance" / "research_request.json",
                provenance_path=final_output_dir / "provenance" / "sources.json",
                research_report_path=final_output_dir / "provenance" / "research_report.json",
                source_inventory_path=final_output_dir / "provenance" / "source_inventory.json",
                data_profile_path=final_output_dir / "provenance" / "data_profile.json",
                quality_profile_path=final_output_dir / "provenance" / "quality_profile.json",
                validation_path=final_output_dir / "validation.json",
                quality_tier=quality_tier,
                collection_rounds=collection_rounds,
                repair_rounds=repair_rounds,
                elapsed_seconds=time.monotonic() - started_at,
            )
        except Exception as error:
            if self.preserve_failed and staging_path.exists() and any(staging_path.iterdir()):
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                if explicit_output_dir is not None:
                    failed_path = explicit_output_dir.with_name(
                        f"{explicit_output_dir.name}.failed-{timestamp}-{staging_path.name[-6:]}"
                    )
                else:
                    failed_root = classification_root / "failed"
                    failed_root.mkdir(parents=True, exist_ok=True)
                    failed_path = failed_root / (
                        f"{safe_seed_id}-{timestamp}-{staging_path.name[-6:]}"
                    )
                staging_path.replace(failed_path)
                error.add_note(f"未发布的调研现场保留在：{failed_path}")
            else:
                shutil.rmtree(staging_path, ignore_errors=True)
            raise

    def _remaining_seconds(self, started_at: float) -> int:
        remaining = self.research_policy.max_total_seconds - int(time.monotonic() - started_at)
        if remaining <= 0:
            raise TimeoutError(
                f"环境调研超过总预算 {self.research_policy.max_total_seconds} 秒"
            )
        return remaining

    @staticmethod
    def _safe_directory_name(value: str) -> str:
        """把 seed_id 转成稳定目录名，避免路径分隔符进入 OSS 路径。"""

        normalized = "".join(
            char.lower() if char.isalnum() else "_" for char in value.strip()
        )
        normalized = "_".join(part for part in normalized.split("_") if part)
        return normalized or "environment"

    @staticmethod
    def _validated_source_inventory(
        validator: SourceInventoryValidator,
        inventory_path: Path,
        *,
        research_request: dict[str, object],
        checkpoint: dict[str, object],
    ) -> dict[str, object]:
        """加载数据面清单，并把协议错误统一转换为 DataGenerationError。"""

        inventory, issues = validator.validate(
            inventory_path,
            request_sha256=str(research_request.get("request_sha256") or ""),
            checkpoint=checkpoint,
        )
        if issues or inventory is None:
            raise DataGenerationError(
                ValidationReport(
                    errors=[
                        ValidationIssue(issue.code, issue.path, issue.message)
                        for issue in issues
                    ]
                )
            )
        return inventory

    @staticmethod
    def _quality_profile_error(messages: list[str]) -> DataGenerationError:
        return DataGenerationError(
            ValidationReport(
                errors=[
                    ValidationIssue(
                        "quality_profile_schema",
                        "$.quality_profile",
                        message,
                    )
                    for message in messages
                ]
            )
        )

    @staticmethod
    def _validate_acquisition_append_only(
        staging_path: Path,
        previous_snapshot: dict[str, str],
    ) -> list[ValidationIssue]:
        """扩展轮只能新增文件，不能覆盖上一轮已经取得的公开证据。"""

        current = DataGenerator._snapshot_business_files(staging_path)
        issues: list[ValidationIssue] = []
        for relative, digest in previous_snapshot.items():
            if relative not in current:
                issues.append(
                    ValidationIssue(
                        "acquisition_removed_business_file",
                        "$.workspace",
                        f"扩展轮删除了已有业务文件：{relative}",
                    )
                )
            elif current[relative] != digest:
                issues.append(
                    ValidationIssue(
                        "acquisition_modified_business_file",
                        "$.workspace",
                        f"扩展轮改写了已有业务文件：{relative}；新增分页必须保存为新文件",
                    )
                )
        return issues

    @staticmethod
    def _snapshot_business_files(staging_path: Path) -> dict[str, str]:
        """返回 raw/entity/derived 文件的内容指纹，用于锁定数据阶段交付物。"""

        workspace = staging_path / "workspace"
        snapshot: dict[str, str] = {}
        for bucket in ("raw", "entities", "derived"):
            root = workspace / bucket
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                snapshot[path.relative_to(workspace).as_posix()] = digest.hexdigest()
        return snapshot

    def _validate_environment_description_boundary(
        self,
        staging_path: Path,
        data_snapshot: dict[str, str],
        *,
        allowed_new_files: set[str],
        allowed_removed_files: set[str] | None = None,
    ) -> list[ValidationIssue]:
        """检查环境描述阶段是否修改或擅自创建业务文件。"""

        current = self._snapshot_business_files(staging_path)
        allowed_removed_files = allowed_removed_files or set()
        issues: list[ValidationIssue] = []
        for relative, digest in data_snapshot.items():
            if relative not in current:
                if relative in allowed_removed_files:
                    continue
                issues.append(
                    ValidationIssue(
                        code="environment_description_removed_business_file",
                        path="$.workspace",
                        message=f"环境描述阶段删除了冻结的业务文件：{relative}",
                    )
                )
            elif current[relative] != digest:
                issues.append(
                    ValidationIssue(
                        code="environment_description_modified_business_file",
                        path="$.workspace",
                        message=f"环境描述阶段修改了冻结的业务文件：{relative}",
                    )
                )
        for relative in sorted(set(current) - set(data_snapshot) - allowed_new_files):
            issues.append(
                ValidationIssue(
                    code="environment_description_created_business_file",
                    path="$.workspace",
                    message=f"环境描述阶段擅自创建业务文件：{relative}",
                )
            )
        return issues

    @staticmethod
    def _write_inventory_checkpoint(
        staging_path: Path,
        *,
        research_request: dict[str, object],
        checkpoint_path: Path,
    ) -> None:
        """在 Agent 超时后仅盘点已有非空文件，避免丢弃可继续校验的现场。"""

        workspace_root = staging_path / "workspace"
        files_by_bucket: dict[str, list[str]] = {}
        for bucket in ("raw", "entities", "derived"):
            bucket_root = workspace_root / bucket
            files: list[str] = []
            if bucket_root.is_dir():
                for path in sorted(bucket_root.rglob("*")):
                    try:
                        relative = path.relative_to(workspace_root).as_posix()
                        if (
                            path.is_file()
                            and path.stat().st_size > 0
                            and not EnvironmentPackageValidator._is_protocol_metadata_path(relative)
                            and DataGenerator._file_is_usable(path, bucket)
                        ):
                            files.append(relative)
                    except OSError:
                        continue
            files_by_bucket[bucket] = files

        seed = research_request.get("seed")
        source_url = seed.get("source_url") if isinstance(seed, dict) else None
        source_urls = [source_url] if isinstance(source_url, str) and source_url else []
        # Agent 可能来不及写 checkpoint，但已经保存 source_manifest。只读取
        # 明确的来源清单字段，不从业务记录中的 html_url/api_url 猜测来源。
        for relative in files_by_bucket["raw"]:
            if "manifest" not in Path(relative).stem.lower():
                continue
            path = workspace_root / relative
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            candidates = manifest.get("sources", []) if isinstance(manifest, dict) else []
            if not isinstance(candidates, list):
                continue
            for item in candidates:
                url = item.get("url") if isinstance(item, dict) else item
                if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in source_urls:
                    source_urls.append(url)
        max_sources = int(
            research_request.get("quality_policy", {}).get("max_sources", len(source_urls))
        )
        source_files_by_url: dict[str, list[str]] = {}
        inventory_path = staging_path / "provenance" / "source_inventory.json"
        if inventory_path.is_file():
            try:
                inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                inventory = {}
            mapped: set[str] = set()
            for surface in inventory.get("surfaces", []) if isinstance(inventory, dict) else []:
                if not isinstance(surface, dict):
                    continue
                url = surface.get("url")
                files = [
                    value
                    for value in surface.get("raw_files", [])
                    if isinstance(value, str)
                    and value in files_by_bucket["raw"]
                    and value not in mapped
                ]
                if isinstance(url, str) and url.startswith(("http://", "https://")) and files:
                    source_urls.append(url)
                    source_files_by_url.setdefault(url, []).extend(files)
                    mapped.update(files)
        source_file_map = [
            {"url": url, "file_paths": files}
            for url, files in source_files_by_url.items()
        ]
        source_urls = list(dict.fromkeys(source_urls))[:max_sources]
        payload = {
            "schema_version": "1.0",
            "request_sha256": research_request.get("request_sha256", ""),
            "status": "ready" if files_by_bucket["raw"] else "insufficient_public_data",
            "summary": (
                "Agent 未在阶段时限内写入提交点；已对非空已有文件做确定性盘点，"
                "后续仍需通过完整环境质量校验。"
                if files_by_bucket["raw"]
                else "Agent 未在阶段时限内写入提交点，workspace 中没有可用的非空 raw 文件。"
            ),
            "raw_files": files_by_bucket["raw"],
            "entity_files": files_by_bucket["entities"],
            "derived_files": files_by_bucket["derived"],
            "source_urls": source_urls,
            **({"source_file_map": source_file_map} if source_file_map else {}),
            "synthetic_business_record_count": 0,
        }
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _file_is_usable(path: Path, bucket: str) -> bool:
        """为兜底盘点过滤明显截断或空壳的常见结构化文件。"""

        suffix = path.suffix.lower()
        try:
            if suffix in {".json", ".sarif"}:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload in ({}, [], None, ""):
                    return False
                if bucket == "raw" and EnvironmentPackageValidator._is_error_payload(payload):
                    return False
                if bucket == "entities":
                    return EnvironmentPackageValidator._entity_json_shape_valid(payload)
            elif suffix == ".jsonl":
                lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if not lines:
                    return False
                for line in lines:
                    json.loads(line)
            elif suffix == ".csv":
                with path.open("r", encoding="utf-8", newline="") as stream:
                    reader = csv.reader(stream)
                    header = next(reader, None)
                    row = next(reader, None)
                return bool(header and all(name.strip() for name in header) and row is not None)
        except (OSError, UnicodeError, json.JSONDecodeError, csv.Error):
            return False
        return True

    def _run_agent(
        self,
        prompt: str,
        staging_path: Path,
        started_at: float,
        *,
        phase_seconds: int | None = None,
        stop_when: tuple[Path, ...] = (),
    ) -> str:
        remaining = self._remaining_seconds(started_at)
        previous_timeout = getattr(self.agent, "timeout_seconds", None)
        if isinstance(previous_timeout, int):
            timeout = min(previous_timeout, remaining)
            if phase_seconds is not None:
                timeout = min(timeout, phase_seconds)
            self.agent.timeout_seconds = timeout  # type: ignore[attr-defined]
        try:
            run_until_files = getattr(self.agent, "run_until_files", None)
            if stop_when and callable(run_until_files):
                return run_until_files(
                    prompt,
                    working_directory=staging_path,
                    required_paths=stop_when,
                )
            return self.agent.run(prompt, working_directory=staging_path)
        finally:
            if isinstance(previous_timeout, int):
                self.agent.timeout_seconds = previous_timeout  # type: ignore[attr-defined]

    @staticmethod
    def _insufficient_public_data(report: ValidationReport) -> bool:
        return any(issue.code == "insufficient_public_data" for issue in report.errors)

    @staticmethod
    def _resolve_validation_schema(schema_path: Path) -> Path:
        """解析协议示例对应的校验 Schema。

        ``schemas/*.schema.json`` 现在保存契约中的结构示例，不是 JSON Schema
        语法。真正用于 Draft 2020-12 校验的文件统一位于 ``schemas/validation``。
        调用方也可以直接传入一个带 ``$schema`` 的自定义校验文件。
        """

        try:
            document = json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"环境 Schema 不是合法 JSON：{error}") from error
        if isinstance(document, dict) and document.get("$schema"):
            return schema_path
        candidate = schema_path.parent / "validation" / schema_path.name
        if candidate.is_file():
            return candidate.resolve()
        raise FileNotFoundError(
            f"找不到 {schema_path.name} 对应的校验 Schema：{candidate}"
        )

    @staticmethod
    def _check_inputs(
        seed_path: Path,
        seed_id: str,
        schema_path: Path,
        contract_path: Path | None,
        validation_schema_path: Path | None = None,
    ) -> dict[str, object]:
        if not seed_id.strip():
            raise ValueError("seed_id 不能为空")
        validation_schema_path = validation_schema_path or schema_path
        for label, path in (
            ("种子文件", seed_path),
            ("环境结构示例", schema_path),
            ("环境校验 Schema", validation_schema_path),
        ):
            if not path.is_file():
                raise FileNotFoundError(f"{label}不存在：{path}")
        if contract_path is not None and not contract_path.is_file():
            raise FileNotFoundError(f"环境契约文档不存在：{contract_path}")

        try:
            seed_document = json.loads(seed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"种子文件不是合法 JSON：{error}") from error
        if isinstance(seed_document, dict) and isinstance(seed_document.get("themes"), list):
            themes = seed_document["themes"]
        elif isinstance(seed_document, dict) and "theme_id" in seed_document:
            themes = [seed_document]
        elif isinstance(seed_document, list):
            themes = seed_document
        else:
            raise ValueError("种子文件必须是单个种子对象、种子数组或包含 themes 数组的对象")
        matches = [item for item in themes if isinstance(item, dict) and item.get("theme_id") == seed_id]
        if len(matches) != 1:
            raise ValueError(f"seed_id 必须在 themes 中唯一存在，实际匹配 {len(matches)} 项：{seed_id}")

        try:
            schema = json.loads(validation_schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"环境校验 Schema 不是合法 JSON：{error}") from error
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("环境校验 Schema 根节点必须是 object")
        return dict(matches[0])
