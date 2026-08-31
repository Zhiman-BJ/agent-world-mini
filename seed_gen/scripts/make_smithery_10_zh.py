"""Extract and visualise the first ten Smithery environment seeds."""

from __future__ import annotations

import copy
import html
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "seed_gen" / "data"
SOURCE = DATA_DIR / "smithery_140_v1_0824.json"
OUTPUT = DATA_DIR / "smithery_10_zh_v1_0824.json"
HTML_OUTPUT = DATA_DIR / "smithery_10_zh_v1_0824.html"

TRANSLATIONS = {
    "theagenttimes/news": "Agent News 是面向 AI 智能体经济的研究与验证服务。它汇总结构化新闻事件、文章、产品与行动元数据以及外部研究证据，用于回答有关 AI 智能体、工具、MCP 服务器和框架的问题，并生成有来源依据的建议。其工作流强调基于引用的论断、置信度与相关性诊断、伦理评级、来源溯源验证、编辑标准，以及在证据不足时明确返回证据不足。它还支持文章发现、主题中心、信任度指标、串式评论和可选的使用情况报告。",
    "onesignal/onesignal": "OneSignal 是一个多渠道客户互动平台，用于创建、定向、发送和衡量推送通知、电子邮件、短信、应用内消息以及 iOS Live Activities。它支持受众细分、用户身份与订阅、可复用模板、由自定义事件驱动的用户旅程、活动分析和订阅生命周期管理。",
    "exa": "Exa 是一项网络研究服务，可搜索当前公开信息，并从网页中提取干净、结构化的内容供分析使用。",
    "upstash/context7-mcp": "Context7 是面向开发者的研究与文档检索服务，可识别软件库，并提供当前且与版本对应的 API 文档和代码示例，用于编程辅助工作流。",
    "OjasKord/url-safety-validator-mcp": "URL Safety Validator 是面向 AI 智能体工作流的 URL 风险筛查服务，会在导航或转发之前评估外部提供的商户、供应商和支付链接。它可识别钓鱼、恶意软件、域名仿冒、可疑重定向以及其他信誉或域名层面的威胁。",
    "keenable/web-search": "这是一个免费且无需账户的远程网络研究服务，允许智能体使用日期和站点筛选条件搜索经过排序的网络索引，并以干净的 Markdown 形式检索已索引页面，以便分析和引用。",
    "re-port-flow/reportflow-mcp": "ReportFlow 是通过 MCP 提供的、基于模板的文档自动化服务。它允许 AI 智能体发现用户的 ReportFlow 设计，检查每个设计所需的参数模式，并为发票、合同和对账单等文档生成单个 PDF 或批量 ZIP 压缩包。其工作流支持基于浏览器的账户认证、本地或工作区感知的文件保存、同步和异步生成，以及可选的自然语言到参数建议。智能体应验证必填参数，并从用户处获取缺失值，而不是自行编造数据。",
    "sidneybissoli/ibge-br-mcp": "这是一个只读研究服务，用于从 IBGE 官方 API 获取实时且带来源的数据。它支持巴西地理查询与编码、人口和人口普查研究、经济与社会指标、市镇比较、健康统计、调查发现、发布追踪和地图获取。典型工作流是先确定地点或调查，再检查可用表格和元数据，查询精确数值，并保留 IBGE 来源信息。",
    "sailquery/niche": "Niche 是面向创作者、智能体和品牌的编辑情报与内容生产服务。它扫描最新的一手来源，对新兴故事进行聚类和排序，推荐有依据的编辑角度，并将选定想法转换为基于来源、适合各平台的 LinkedIn、X、Instagram、新闻简报、文章、图片和短视频内容。它还支持品牌与声音档案、内容修订、发布审批工作流、会话管理，以及包含丰富来源信息的编辑日历导出。",
    "sidneybissoli/medical-terminologies-mcp": "这是一个开源的医学术语研究与互操作服务，用于搜索、解析、验证、分类和交叉引用临床概念与编码。它支持诊断、实验室观察、药物、文献索引、药物类别，以及巴西葡萄牙语疾病编码，覆盖 ICD-11、ICD-10/CID-10、SNOMED CT、LOINC、RxNorm、MeSH、ATC 和 NDC。典型工作流包括代码查询、层级导航、术语验证、ICD-10 到 ICD-11 的迁移、药物成分和类别分析、面板或答案集检查、多语言概念发现，以及面向版本的术语研究。部分术语间映射（尤其是 LOINC 到 SNOMED CT）需要获得许可或经过认证的外部数据集，可能只能返回数据获取指南。",
}


def extract_records() -> list[dict]:
    records = json.loads(SOURCE.read_text(encoding="utf-8"))
    selected = []
    for record in records[:10]:
        item = copy.deepcopy(record)
        name = item["environment"]["basic_info"]["name"]
        if name not in TRANSLATIONS:
            raise KeyError(f"Missing translation for {name}")
        env = item["environment"]
        item["environment"] = {
            "basic_info": env["basic_info"],
            "description": env["description"],
            "description_zh": TRANSLATIONS[name],
            "domain": env["domain"],
        }
        selected.append(item)
    if len(selected) != 10:
        raise ValueError(f"Expected 10 records, got {len(selected)}")
    return selected


def translate_text(text: str, retries: int = 3) -> str:
    """Translate one source description through Google Translate's mobile endpoint."""
    if not text.strip():
        return text
    for attempt in range(retries):
        try:
            response = requests.get(
                "https://translate.google.com/m",
                params={"sl": "auto", "tl": "zh-CN", "q": text},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=45,
            )
            response.raise_for_status()
            node = BeautifulSoup(response.text, "html.parser").select_one(".result-container")
            translated = node.get_text(" ", strip=True) if node else ""
            if translated:
                return translated
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    # Preserve a searchable Chinese marker if a remote translation is temporarily unavailable.
    return f"（中文翻译暂不可用）{text}"


def add_tool_translations(records: list[dict]) -> None:
    jobs = []
    for record in records:
        for tool in record["init_ref_tools"]:
            jobs.append(tool)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(translate_text, tool["description"]): tool for tool in jobs}
        for future in as_completed(futures):
            futures[future]["description_zh"] = future.result()


def render_html(records: list[dict]) -> str:
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    title = "Smithery 前 10 个环境种子（中文描述）"
    prefix = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>""" + html.escape(title) + """</title>
  <style>
    :root { color-scheme: light dark; --bg: light-dark(#f6f7fb,#111827); --surface: light-dark(#fff,#1f2937); --text: light-dark(#172033,#f3f4f6); --muted: light-dark(#5c667a,#b7c0d0); --line: light-dark(#dfe3ec,#374151); --accent: light-dark(#315efb,#8aa4ff); }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 2rem; background: var(--bg); color: var(--text); font: 16px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
    main { max-width: 1180px; margin: 0 auto; }
    h1 { margin: 0 0 .35rem; font-size: clamp(1.5rem, 3vw, 2.2rem); line-height: 1.25; }
    .summary { margin: 0 0 1.5rem; color: var(--muted); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(320px,1fr)); gap: 1rem; }
    article { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 1.1rem 1.2rem; box-shadow: 0 5px 18px #0001; }
    article h2 { margin: 0; font-size: 1.1rem; overflow-wrap: anywhere; }
    .meta { color: var(--muted); font-size: .88rem; margin: .25rem 0 .8rem; overflow-wrap: anywhere; }
    .meta a { color: var(--accent); }
    .label { font-weight: 600; margin: .75rem 0 .2rem; }
    .description { margin: 0; }
    .original { color: var(--muted); font-size: .92rem; }
    details { margin-top: .8rem; border-top: 1px solid var(--line); padding-top: .6rem; }
    summary { cursor: pointer; color: var(--accent); font-weight: 600; }
    ul { margin: .5rem 0 0 1.2rem; padding: 0; }
    li { margin: .2rem 0; overflow-wrap: anywhere; }
    code { font-size: .86em; }
  </style>
</head>
<body>
<main>
  <h1>""" + html.escape(title) + """</h1>
  <p class="summary" id="summary"></p>
  <section class="grid" id="seeds" aria-label="环境种子列表"></section>
</main>
<script>
const seeds = """
    suffix = """; 
const esc = value => String(value ?? '').replace(/[&<>\"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[ch]));
const summary = document.getElementById('summary');
const host = document.getElementById('seeds');
const toolCount = seeds.reduce((n, seed) => n + seed.init_ref_tools.length, 0);
summary.textContent = '共 ' + seeds.length + ' 个环境，' + toolCount + ' 个参考工具；原文与中文描述并列展示。';
for (const seed of seeds) {
  const env = seed.environment;
  const info = env.basic_info;
  const tools = seed.init_ref_tools;
  const toolItems = tools.map(tool => '<li><code>' + esc(tool.name) + '</code>：' + esc(tool.description_zh || tool.description) + '<br><span class="original">' + esc(tool.description) + '</span></li>').join('');
  const article = document.createElement('article');
  const level2 = env.domain.level2 ? ' / ' + esc(env.domain.level2) : '';
  const level3 = env.domain.level3 ? ' / ' + esc(env.domain.level3) : '';
  article.innerHTML = '<h2>' + esc(info.index) + '. ' + esc(info.name) + '</h2>' +
    '<p class="meta"><a href="' + esc(info.url) + '" target="_blank" rel="noopener">' + esc(info.url) + '</a><br>分类：' + esc(env.domain.level1) + level2 + level3 + '</p>' +
    '<div class="label">中文描述</div><p class="description">' + esc(env.description_zh) + '</p>' +
    '<div class="label">原始描述</div><p class="description original">' + esc(env.description) + '</p>' +
    '<details><summary>查看参考工具（' + tools.length + '）</summary><ul>' + (toolItems || '<li>来源未提供参考工具</li>') + '</ul></details>';
  host.appendChild(article);
}
</script>
</body>
</html>
"""
    return prefix + payload + suffix


def main() -> None:
    records = extract_records()
    add_tool_translations(records)
    OUTPUT.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HTML_OUTPUT.write_text(render_html(records), encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(records)} records)")
    print(f"wrote {HTML_OUTPUT}")


if __name__ == "__main__":
    main()
