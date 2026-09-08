"""DataGen 的唯一外部配置入口。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_RESEARCH_MODEL = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_ENVIRONMENT_SCHEMA = Path("schemas/environment.schema.json")
DEFAULT_SEED_VALIDATION_SCHEMA = Path("schemas/validation/env_seeds.schema.json")
DEFAULT_OSS_OUTPUT_ROOT = Path(
    "/mnt/oss-bucket/sunshuo/AgentWorld/environment/data_gen_v3"
)


@dataclass(frozen=True)
class CollectionPolicy:
    """Codex 采集会话的时间、空间和完成证据边界。"""

    max_total_seconds: int = 4200
    scenario_research_seconds: int = 480
    scenario_research_total_seconds: int = 720
    max_scenario_research_attempts: int = 2
    source_collection_total_seconds: int = 2400
    download_timeout_seconds: int = 900
    integration_seconds: int = 1500
    integration_total_seconds: int = 2100
    max_integration_rounds: int = 4
    max_raw_bytes: int = 512 * 1024 * 1024
    max_derived_bytes: int = 64 * 1024 * 1024
    max_workspace_bytes: int = 768 * 1024 * 1024
    max_single_file_bytes: int = 256 * 1024 * 1024
    max_raw_files: int = 200
    min_seed_coverage_percent: int = 90
    # Step 2 的最低验收线，不是达到后立即停止的采集目标。
    min_scenario_coverage_percent: int = 75
    max_no_progress_rounds: int = 2

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"CollectionPolicy.{name} 必须是非负整数")
        if self.max_total_seconds == 0:
            raise ValueError("CollectionPolicy.max_total_seconds 必须大于 0")
        if self.scenario_research_seconds == 0:
            raise ValueError("CollectionPolicy.scenario_research_seconds 必须大于 0")
        if self.scenario_research_total_seconds == 0:
            raise ValueError(
                "CollectionPolicy.scenario_research_total_seconds 必须大于 0"
            )
        if self.max_scenario_research_attempts == 0:
            raise ValueError(
                "CollectionPolicy.max_scenario_research_attempts 必须大于 0"
            )
        if self.source_collection_total_seconds == 0:
            raise ValueError("CollectionPolicy.source_collection_total_seconds 必须大于 0")
        if self.download_timeout_seconds == 0:
            raise ValueError("CollectionPolicy.download_timeout_seconds 必须大于 0")
        if self.integration_seconds == 0 or self.integration_total_seconds == 0:
            raise ValueError("CollectionPolicy 的集成预算必须大于 0")
        if self.max_integration_rounds == 0:
            raise ValueError("CollectionPolicy.max_integration_rounds 必须大于 0")
        if self.max_no_progress_rounds == 0:
            raise ValueError("CollectionPolicy.max_no_progress_rounds 必须大于 0")
        for name in ("min_seed_coverage_percent", "min_scenario_coverage_percent"):
            if not 1 <= getattr(self, name) <= 100:
                raise ValueError(f"CollectionPolicy.{name} 必须位于 1 到 100")


@dataclass(frozen=True)
class DataGenConfig:
    """一次环境生成所需的全部调用方参数。"""

    seed_path: Path
    global_id: str
    schema_path: Path = DEFAULT_ENVIRONMENT_SCHEMA
    seed_validation_schema_path: Path = DEFAULT_SEED_VALIDATION_SCHEMA
    contract_path: Path | None = None
    output_dir: Path | None = None
    output_root: Path | None = None
    overwrite: bool = False
    model: str | None = DEFAULT_RESEARCH_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    timeout_seconds: int = 4200
    max_repair_rounds: int = 2
    enable_web_search: bool = True
    preserve_failed: bool = True
    allow_partial_integration: bool = False

    def __post_init__(self) -> None:
        if not self.global_id.strip():
            raise ValueError("global_id 不能为空")
        if self.output_dir is not None and self.output_root is not None:
            raise ValueError("output_dir 和 output_root 不能同时设置")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if self.max_repair_rounds < 0:
            raise ValueError("max_repair_rounds 不能小于 0")
        if self.reasoning_effort not in {"minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError("reasoning_effort 必须是 minimal、low、medium、high 或 xhigh")


__all__ = [
    "CollectionPolicy",
    "DEFAULT_OSS_OUTPUT_ROOT",
    "DEFAULT_REASONING_EFFORT",
    "DEFAULT_RESEARCH_MODEL",
    "DEFAULT_ENVIRONMENT_SCHEMA",
    "DEFAULT_SEED_VALIDATION_SCHEMA",
    "DataGenConfig",
]
