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
    exploration_seconds: int = 720
    exploration_total_seconds: int = 1080
    max_exploration_rounds: int = 3
    integration_seconds: int = 1500
    integration_total_seconds: int = 2100
    max_integration_rounds: int = 4
    collection_seconds: int = 2400
    description_seconds: int = 720
    repair_seconds: int = 480
    full_download_record_limit: int = 50_000
    large_source_record_target: int = 25_000
    max_relation_edges: int = 100_000
    max_raw_bytes: int = 512 * 1024 * 1024
    max_derived_bytes: int = 64 * 1024 * 1024
    max_workspace_bytes: int = 768 * 1024 * 1024
    max_single_file_bytes: int = 256 * 1024 * 1024
    max_raw_files: int = 200
    max_sources: int = 50
    min_sample_records: int = 100
    min_sample_units: int = 4
    max_collection_rounds: int = 4
    collection_total_seconds: int = 2520
    min_collection_round_seconds: int = 300
    max_no_progress_rounds: int = 2
    max_parallel_downloads: int = 4
    max_source_attempts: int = 2

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
        if self.exploration_seconds == 0 or self.exploration_total_seconds == 0:
            raise ValueError("CollectionPolicy 的来源探索预算必须大于 0")
        if self.max_exploration_rounds == 0:
            raise ValueError("CollectionPolicy.max_exploration_rounds 必须大于 0")
        if self.integration_seconds == 0 or self.integration_total_seconds == 0:
            raise ValueError("CollectionPolicy 的集成预算必须大于 0")
        if self.max_integration_rounds == 0:
            raise ValueError("CollectionPolicy.max_integration_rounds 必须大于 0")
        if self.max_collection_rounds == 0:
            raise ValueError("CollectionPolicy.max_collection_rounds 必须大于 0")
        if self.collection_total_seconds == 0:
            raise ValueError("CollectionPolicy.collection_total_seconds 必须大于 0")
        if self.min_collection_round_seconds == 0:
            raise ValueError(
                "CollectionPolicy.min_collection_round_seconds 必须大于 0"
            )
        if self.max_no_progress_rounds == 0:
            raise ValueError("CollectionPolicy.max_no_progress_rounds 必须大于 0")
        if self.max_parallel_downloads == 0:
            raise ValueError("CollectionPolicy.max_parallel_downloads 必须大于 0")
        if self.max_source_attempts == 0:
            raise ValueError("CollectionPolicy.max_source_attempts 必须大于 0")


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
    max_collection_rounds: int = 4
    max_repair_rounds: int = 2
    enable_web_search: bool = True
    preserve_failed: bool = True

    def __post_init__(self) -> None:
        if not self.global_id.strip():
            raise ValueError("global_id 不能为空")
        if self.output_dir is not None and self.output_root is not None:
            raise ValueError("output_dir 和 output_root 不能同时设置")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if self.max_collection_rounds <= 0:
            raise ValueError("max_collection_rounds 必须大于 0")
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
