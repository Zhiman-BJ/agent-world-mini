"""Step 0: prepare one isolated DataGen run from a selected Seed."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from env_gen.data_gen.analysis.seed import load_selected_seed
from env_gen.data_gen.config import DataGenConfig

from .common.constants import (
    CONTROL_RUN_CONFIG,
    CONTROL_SELECTED_SEED,
    WORKFLOW_VERSION,
)
from .common.control_io import control_path, write_json


def prepare_generation_run(
    run_dir: Path,
    config: DataGenConfig,
    *,
    limits: Mapping[str, int],
    quality: Mapping[str, int] | None = None,
) -> str:
    """Select the Seed and write the minimal context shared by later steps."""

    run_dir = run_dir.resolve()
    for relative in ("provenance", ".datagen/drafts"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)

    project_root = Path(__file__).resolve().parents[3]
    checkpoint_root = Path(__file__).resolve().parents[1] / "analysis/checkpoint_schemas"
    schema_root = project_root / "schemas"
    seed_path = config.seed_path.resolve()
    seed_validation = config.seed_validation_schema_path.resolve()
    environment_schema = (schema_root / "validation/environment-v2.schema.json").resolve()
    contract = (config.contract_path or schema_root / "环境契约-v2.0.md").resolve()
    schema_paths = {
        "scenario_research_schema_path": checkpoint_root / "scenario_research.schema.json",
        "source_plan_schema_path": checkpoint_root / "source_plan.schema.json",
        "source_inventory_schema_path": checkpoint_root / "source_inventory.schema.json",
        "integration_plan_schema_path": checkpoint_root / "integration_plan.schema.json",
        "integration_profile_schema_path": checkpoint_root / "integration_profile.schema.json",
        "environment_quality_profile_schema_path": (
            checkpoint_root / "environment_quality_profile.schema.json"
        ),
        "environment_v2_schema_path": environment_schema,
    }
    required = (seed_path, seed_validation, contract, *schema_paths.values())
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "DataGen 准备文件缺失：" + ", ".join(str(path) for path in missing)
        )

    selected_seed_path = control_path(run_dir, CONTROL_SELECTED_SEED)
    seed, seed_sha256 = load_selected_seed(
        seed_path,
        config.global_id,
        seed_validation,
    )
    run_config = {
        "workflow_version": WORKFLOW_VERSION,
        "seed_global_id": config.global_id,
        "seed_sha256": seed_sha256,
        "seed_path": str(seed_path),
        "contract_path": str(contract),
        **{name: str(path.resolve()) for name, path in schema_paths.items()},
        "collection_policy": dict(limits),
        "environment_quality_policy": dict(quality or {}),
    }
    write_json(control_path(run_dir, CONTROL_RUN_CONFIG), run_config)
    write_json(selected_seed_path, seed)
    return seed_sha256
