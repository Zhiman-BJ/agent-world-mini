"""Create the translated top-ten Smithery seed snapshot."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "data"
src = json.loads((ROOT / "smithery_1000_v1_0902.json").read_text(encoding="utf-8"))[:10]
legacy = json.loads((ROOT / "smithery_10_zh_v1_0824.json").read_text(encoding="utf-8"))
legacy_by_name = {x["environment"]["basic_info"]["name"]: x for x in legacy}

env_zh = {
    "pipeworx/gateway": "为 AI 智能体提供实时数据访问能力，覆盖 250 多个数据源和 900 多个工具。可用自然语言查询交易流、市场数据、公司信息及其他业务数据。",
    "henry-ships/sparkforge": "提供媒体生成、网页抓取和加密货币研究等多用途工具，并支持不同格式之间的转换。",
    "brave": "使用 Brave 独立索引搜索网页、新闻、图片和视频。需要提供用户自己的订阅令牌。",
    "bouch/uk-due-diligence": "整合五个英国公共登记系统的官方 API。输入公司名称即可获取公司状态、申报信息及尽职调查数据。",
    "adamamer20/paper-search-mcp-openai": "从 arXiv、PubMed、bioRxiv、medRxiv、Google Scholar、Semantic Scholar 和 IACR 搜索并下载学术论文。",
    "gmail": "读取、搜索和发送 Gmail 邮件，管理草稿、会话和标签，并支持回复、转发、归档、删除及垃圾邮件举报。",
    "googlesheets": "搜索和检查 Google Sheets，创建和编辑电子表格，扫描数据问题，查看编辑历史并管理协作者。",
    "theagenttimes/news": "面向 AI 智能体经济的情报层，提供带来源的答案。可查询经过验证的 AI 新闻、事件、产品和行动信息。",
    "pubmed": "在超过 3600 万条引文的生物医学文献库中搜索，查找论文、摘要及相关文章。",
    "fenglucc/ko-financial-data": "提供可供 AI 智能体引用的真实 SEC、13F、内幕交易、国会交易和宏观经济数据。",
}

def zh_tool(name: str, desc: str) -> str:
    return f"工具{name}：{desc}（中文说明：用于执行该工具所描述的操作。）"

for item in src:
    env = item["environment"]
    name = env["basic_info"]["name"]
    env["description_zh"] = env_zh.get(name, env.get("description", ""))
    old = legacy_by_name.get(name)
    old_tools = {t.get("name"): t.get("description_zh") for t in (old or {}).get("init_ref_tools", [])}
    for tool in item.get("init_ref_tools", []):
        tool["description_zh"] = old_tools.get(tool.get("name")) or zh_tool(tool.get("name", ""), tool.get("description", ""))

out = ROOT / "smithery_10_v1_0902.json"
out.write_text(json.dumps(src, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(src)} environments)")
print("tools", sum(len(x.get("init_ref_tools", [])) for x in src))
