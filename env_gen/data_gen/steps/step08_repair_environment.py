"""Step 08：构造只修复环境声明错误的 Agent Prompt。"""

from __future__ import annotations

from pathlib import Path


def build_repair_prompt(
    *,
    seed_path: Path,
    seed_id: str,
    schema_path: Path,
    contract_path: Path | None,
    request_path: Path,
    checkpoint_path: Path,
    error_path: Path,
    repair_round: int,
    done_path: Path,
) -> str:
    """返回一次环境声明修复 Prompt；业务文件保持冻结。"""

    contract_line = f"- 人类可读环境契约：{contract_path}\n" if contract_path else ""
    return f"""这是环境数据包第 {repair_round} 次声明修复。只修复校验报告指出的 environment.json、provenance/sources.json 或 provenance/research_report.json 问题。

输入：
- 种子文件：{seed_path}
- 本次唯一目标 seed_id：{seed_id}
- 环境契约结构示例：{schema_path}
{contract_line}- 调研请求：{request_path}
- 数据提交点：{checkpoint_path}
- 校验报告：{error_path}

workspace/raw、workspace/entities、workspace/derived 已冻结：不得新增、删除或修改其中任何文件。不得扩大调研范围，不得重新下载，不得编造数据，不得生成工具或任务。关系、字段和来源声明必须能被现有文件验证。

修复三份环境描述并检查路径、Schema、来源映射和关系后，最后写入 {done_path}，内容为 {{"status":"ready"}}，然后立即结束。"""
