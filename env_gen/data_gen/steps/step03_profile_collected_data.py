"""Step 03：从已落盘文件提取可验证的数据画像。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from env_gen.data_gen.analysis.capability_extraction import infer_closed_relations
from env_gen.data_gen.analysis.entity_profiling import (
    profile_entity_groups,
    profile_workspace_files,
)
from env_gen.data_gen.analysis.record_extraction import (
    deterministic_entity_groups,
)


def profile_collected_data(
    package_root: Path,
    *,
    research_request: dict[str, Any],
    checkpoint: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    """统计文件和记录事实；关系结果只是候选，不是最终业务声明。"""

    entity_groups = deterministic_entity_groups(
        package_root,
        research_request=research_request,
        checkpoint=checkpoint,
        authoritative_raw=True,
    )
    entity_profiles = profile_entity_groups(entity_groups)
    file_profiles = profile_workspace_files(package_root / "workspace", checkpoint)
    relation_candidates = infer_closed_relations(entity_groups, entity_profiles)
    profile = {
        "schema_version": "1.0",
        "request_sha256": research_request.get("request_sha256", ""),
        "summary": {
            "entity_type_count": len(entity_profiles),
            "entity_record_count": sum(
                int(item.get("record_count", 0)) for item in entity_profiles.values()
            ),
            "file_count": len(file_profiles),
            "file_bytes": sum(int(item.get("bytes", 0)) for item in file_profiles),
            "relation_candidate_count": len(relation_candidates),
        },
        "entities": entity_profiles,
        "files": file_profiles,
        "relation_candidates": relation_candidates,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return profile
