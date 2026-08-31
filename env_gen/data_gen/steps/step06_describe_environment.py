"""Step 06：让 Agent 基于冻结的数据声明最终环境语义。"""

from __future__ import annotations

from pathlib import Path


def build_environment_description_prompt(
    *,
    seed_path: Path,
    seed_id: str,
    schema_path: Path,
    contract_path: Path | None,
    request_path: Path,
    checkpoint_path: Path,
    source_inventory_path: Path,
    data_profile_path: Path,
    quality_profile_path: Path,
    done_path: Path,
) -> str:
    """构造环境描述 Prompt；Agent 负责语义，不能再修改业务数据。"""

    contract_line = f"- 人类可读环境契约：{contract_path}\n" if contract_path else ""
    return f"""你是 Agent-World 的环境语义描述 Agent。真实数据采集已经结束，workspace 中的业务文件已经冻结。你要理解这些文件并声明环境，不能继续下载数据，也不能新增、删除或修改 workspace/raw、workspace/entities、workspace/derived 中的任何文件。

读取：
- 种子文件：{seed_path}
- 本次唯一目标 seed_id：{seed_id}
- 环境契约结构示例：{schema_path}
{contract_line}- 调研请求：{request_path}
- 数据提交点：{checkpoint_path}
- 数据面清单：{source_inventory_path}
- Python 提取的数据事实画像：{data_profile_path}
- 当前丰富度与缺口：{quality_profile_path}

这里有两类信息，不能混淆：
1. data_profile 中的路径、格式、字段类型、数量和取值覆盖是程序观察到的事实。
2. 文件的业务含义、实体边界、字段含义和业务关系由你结合 Seed、来源和实际内容判断。

写出以下三个文件：
1. environment.json：严格符合环境契约结构示例。resources 必须对应实际 workspace 文件或目录；准确声明 data_type、storage_type、path、format、writable、source_resources 和适用的 entity_schema。不要为了统一形式强行把所有原始文件抽成实体。
2. provenance/sources.json：每个 raw 资源和 raw 文件必须映射到实际访问过的 URL，包含来源类型、带时区 retrieved_at 和许可证或访问说明。
3. provenance/research_report.json：逐项回答 research_request；relations 只能声明有明确业务语义且数据值通过闭合验证的关系，字段名相似或取值偶然重合只能写入 relation_gaps。

硬约束：
- 不得在 environment.json 中放入调研过程、质量评分、工具或任务。
- writable 是环境设计决策：已有真实业务数据默认只读，只有明确的输出目录可写。
- 不得把 Python 的关系候选直接当作业务事实，必须结合来源语义检查。
- 不得伪造记录或用模型记忆补齐缺失值。
- 三份描述必须与 checkpoint 和实际文件完全一致。

完成并自检后，最后写入 {done_path}，内容为 {{"status":"ready"}}，然后立即结束。"""
