"""Render a Smithery seed JSON file using the existing Chinese HTML layout."""
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
source_json = DATA / "smithery_10_zh_v1_0902.json"
template = DATA / "smithery_10_zh_v1_0824.html"
output = DATA / "smithery_10_zh_v1_0902.html"

seeds = json.loads(source_json.read_text(encoding="utf-8"))
html = template.read_text(encoding="utf-8")
payload = json.dumps(seeds, ensure_ascii=False, separators=(",", ":"))
html, replaced = re.subn(
    r"const seeds = .*?\];\s*\nconst esc",
    lambda _match: "const seeds = " + payload + ";\nconst esc",
    html,
    count=1,
    flags=re.S,
)
if replaced != 1:
    raise RuntimeError("无法在 HTML 样例中定位 seeds 数据段")
html = html.replace("Smithery 前 10 个环境种子（中文描述）", "Smithery 前 10 个环境种子（0902，中文描述）")
output.write_text(html, encoding="utf-8")
print(f"wrote {output} ({len(seeds)} environments)")
