"""Step 02：构造首轮真实数据调研和下载 Prompt。"""

from __future__ import annotations

from pathlib import Path

from env_gen.data_gen.acquisition import AcquisitionPolicy


def _input_paths(
    *,
    seed_path: Path,
    seed_id: str,
    schema_path: Path,
    contract_path: Path | None,
) -> str:
    contract_line = f"- 人类可读环境契约：{contract_path}\n" if contract_path else ""
    return (
        f"- 种子文件：{seed_path}\n"
        f"- 本次唯一目标 seed_id：{seed_id}\n"
        f"- 环境契约结构示例：{schema_path}\n"
        f"{contract_line}"
        "- 输出根目录：当前工作目录\n"
    )


def build_collection_prompt(
    *,
    seed_path: Path,
    seed_id: str,
    schema_path: Path,
    contract_path: Path | None,
    request_path: Path,
    source_inventory_path: Path,
    source_inventory_schema_path: Path,
    checkpoint_path: Path,
    policy: AcquisitionPolicy,
) -> str:
    """返回首轮采集 Prompt；执行 Agent 的职责留给 Pipeline。"""

    return f"""你是 Agent-World 的真实数据采集 Agent。当前只执行数据面发现和第一轮批量采集；不要生成 environment.json、sources.json、research_report.json、工具或任务。

先读取以下文件，只处理目标 seed_id：
{_input_paths(seed_path=seed_path, seed_id=seed_id, schema_path=schema_path, contract_path=contract_path)}
- 机器可读调研请求：{request_path}
- 数据面清单 Schema：{source_inventory_schema_path}

先调查种子允许来源中的公开 API、批量下载、官方仓库和文件集合。把发现的每个数据面写入 {source_inventory_path}，明确 URL、核心/扩展优先级、实体、分页方式、官方总量、关联数据面和当前采集状态。不能只列已经下载的接口；尚未处理但与业务直接相关的数据面也要写成 pending。complete 必须提供 exhaustion_evidence，说明游标结束、reported_total 已取完、仓库已经枚举或规定的分层采集已经完成。

然后在 workspace/raw、workspace/entities、workspace/derived 中采集真实公开数据。必须实际访问来源并检查响应，禁止用模型记忆、模板或脚本规则生成业务记录。执行以下采集规则：
1. reported_total 不超过 {policy.full_download_record_limit} 的结构化数据面尽量完整下载。
2. 更大的核心数据面按时间、类别、地域和数值区间做稳定分层，目标规模为 {policy.large_surface_record_target} 条；不能只取排序前若干页。
3. 关系数据最多保留 {policy.max_relation_edges} 条真实边；必须继续取得被引用目标，优先形成闭合关系。
4. lookup、类别、国家、机构等小型定义表尽量完整下载；时间序列保留选中对象的完整时间段，不能只取一个年份。
5. 单个核心文件不超过 {policy.max_single_file_bytes} bytes 时完整下载。文件集合先枚举元数据，再按格式、时间、类别、大小和关系选择互补文件。
6. raw 总量不超过 {policy.max_raw_bytes} bytes，workspace 不超过 {policy.max_workspace_bytes} bytes，raw 文件不超过 {policy.max_raw_files} 个，来源不超过 {policy.max_sources} 个。

raw 保存原始响应；entities 只能保存通过可复现程序从 raw 抽取的实体记录；derived 只保存紧凑索引或统计。复杂数组/对象留在 raw 或拆成关系实体。不能为了达到任何数字重复文件、复制记录或制造字段。环境是否 rich 由后续程序根据能力和任务组合空间计算，Agent 不得自行宣布质量通过。

数据文件完成后，先更新 source_inventory 的 raw_files、records_collected、collection_status 和 exhaustion_evidence，最后写 {checkpoint_path}。checkpoint 路径相对 workspace 根目录，ready 必须让 source_file_map 精确覆盖每个 raw 文件。核心真实公开数据确实不存在时使用 insufficient_public_data，不能补造替代记录。

checkpoint 必须符合以下结构：
{{
  "schema_version": "1.0",
  "request_sha256": "复制 research_request.request_sha256",
  "status": "ready 或 insufficient_public_data",
  "summary": "基于实际访问结果的说明",
  "raw_files": ["raw/records_page_001.json"],
  "entity_files": [],
  "derived_files": [],
  "source_urls": ["实际访问的完整 http/https URL"],
  "source_file_map": [
    {{"url": "实际访问的 URL", "file_paths": ["raw/records_page_001.json"]}}
  ],
  "synthetic_business_record_count": 0
}}

所有路径必须真实存在。source_inventory 写完后再写 checkpoint；checkpoint 写完立即结束。"""
