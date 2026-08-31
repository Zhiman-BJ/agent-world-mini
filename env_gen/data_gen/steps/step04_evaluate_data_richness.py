"""Step 04：根据画像计算环境丰富度和下一轮数据缺口。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from env_gen.data_gen.quality import (
    RichnessPolicy,
    build_quality_profile,
    validate_quality_profile,
)


def evaluate_data_richness(
    package_root: Path,
    *,
    research_request: dict[str, Any],
    checkpoint: dict[str, Any],
    source_inventory: dict[str, Any],
    data_profile: dict[str, Any],
    policy: RichnessPolicy,
    schema_path: Path,
    output_path: Path,
    history_path: Path,
) -> tuple[dict[str, Any], list[str]]:
    """写出本轮质量画像，并返回 Schema 错误供 Pipeline 统一处理。"""

    profile = build_quality_profile(
        package_root,
        research_request=research_request,
        checkpoint=checkpoint,
        source_inventory=source_inventory,
        policy=policy,
        data_profile=data_profile,
    )
    errors = validate_quality_profile(profile, schema_path)
    if errors:
        return profile, errors
    payload = json.dumps(profile, ensure_ascii=False, indent=2) + "\n"
    output_path.write_text(payload, encoding="utf-8")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(payload, encoding="utf-8")
    return profile, []
