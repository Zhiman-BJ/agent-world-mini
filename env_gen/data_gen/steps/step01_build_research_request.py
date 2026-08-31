"""Step 01：把一个 Seed 编译成机器可读调研要求。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from env_gen.data_gen.policy import ResearchPolicy, compile_research_request


def build_research_request(
    seed: dict[str, Any],
    policy: ResearchPolicy,
    output_path: Path,
) -> dict[str, Any]:
    """编译并保存调研要求；该步骤不调用模型。"""

    request = compile_research_request(seed, policy)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return request
