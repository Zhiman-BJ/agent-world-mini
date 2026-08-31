"""Step 05：构造针对确定性数据缺口的扩充 Prompt。"""

from __future__ import annotations

from pathlib import Path


def build_expansion_prompt(
    *,
    seed_path: Path,
    seed_id: str,
    schema_path: Path,
    contract_path: Path | None,
    request_path: Path,
    source_inventory_path: Path,
    source_inventory_schema_path: Path,
    previous_checkpoint_path: Path,
    frontier_path: Path,
    checkpoint_path: Path,
    collection_round: int,
) -> str:
    """返回一轮追加采集 Prompt；禁止覆盖已有业务文件。"""

    contract_line = f"- 人类可读环境契约：{contract_path}\n" if contract_path else ""
    return f"""你是 Agent-World 的真实数据扩展 Agent，现在执行第 {collection_round} 轮采集。不要生成环境元数据、工具或任务，也不要重新换业务主题。

读取：
- 种子文件：{seed_path}
- 本次唯一目标 seed_id：{seed_id}
- 环境契约结构示例：{schema_path}
{contract_line}- 调研请求：{request_path}
- 数据面清单及其 Schema：{source_inventory_path}、{source_inventory_schema_path}
- 上一轮 checkpoint：{previous_checkpoint_path}
- 本轮确定性 frontier：{frontier_path}

逐项处理 frontier。重点是继续分页、发现同一官方来源中的相邻数据面、补齐真实外键目标、增加缺失的时间/类别/数值/文本维度和形成可组合关系，不是补到某个最低条数。已有 workspace/raw、entities、derived 文件是只读证据：不得删除或改写。新增分页和新数据面必须保存为新的 raw 文件，并在新的 checkpoint 中同时保留所有旧文件和新文件。

只允许真实公开数据，禁止合成、复制、扰动或模板生成记录。完成后先更新 source_inventory：新增数据面必须加入清单，partial/complete/blocked 状态必须基于实际访问结果，complete 必须有 exhaustion_evidence。最后重新写 {checkpoint_path}，让 source_file_map 精确覆盖所有 raw 文件，然后立即结束。"""
