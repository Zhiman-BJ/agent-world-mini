"""Build scenario-named seeds from the coverage table and selected package APIs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "seed_gen/pypi_scenario_selection.json"
OUT = ROOT / "seed_gen/pypi_outputs/scenario_seeds"
REPORTS = ROOT / "seed_gen/pypi_outputs/scenario_selection_reports"
LEVEL1 = "半导体 02 器件与物理仿真"
HEADERS = ["L2", "L3", "应用场景", "场景具体描述", "包 / 包组合", "关系", "可合成任务"]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def parse_scenarios(markdown: str) -> list[dict]:
    """Read the user-edited table, including visually merged L2 cells."""
    rows = []
    active = False
    l2 = ""
    for line in markdown.splitlines():
        if not line.startswith("|"):
            active = False
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells == HEADERS:
            active = True
            continue
        if not active or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        require(len(cells) == len(HEADERS), f"Malformed scenario row: {line}")
        l2 = cells[0] or l2
        require(bool(re.fullmatch(r"02\.\d{2} .+", l2)), f"Invalid L2: {l2}")
        match = re.fullmatch(r"(02\.\d{2}\.\d{2}) .+", cells[1])
        require(match is not None, f"Invalid L3: {cells[1]}")
        require(cells[1].startswith(l2.split()[0] + "."), "L2/L3 mismatch")
        require(all(cells[1:]), f"Empty scenario metadata: {cells[1]}")
        rows.append({"id": match[1], "l2": l2, "l3": cells[1], "scenario": cells[2],
                     "description": cells[3], "table_packages": cells[4],
                     "package_relation": cells[5], "possible_task": cells[6]})
    require(bool(rows), "No scenario table found")
    require(len({row["id"] for row in rows}) == len(rows), "Duplicate scenario IDs")
    return rows


def key(tool: dict) -> tuple:
    return tool["module"], tool["name"], tool["type"]


def qualified(tool: dict) -> str:
    return f"{tool['module']}.{tool['name']}"


def counts(tools: list[dict]) -> dict:
    functions = sum(t["type"] == "function" for t in tools)
    methods = sum(len(t["function"]) for t in tools if t["type"] == "class")
    return {"class": sum(t["type"] == "class" for t in tools),
            "function": functions, "class_func": methods, "all_func": functions + methods}


def matches(tool: dict, selector: str) -> bool:
    return selector in (tool["name"], qualified(tool))


def select_package(seed: dict, package_profile: dict, rule: dict) -> tuple[list, list]:
    """Capability membership comes from the profile; all API content from the selected file."""
    source = {key(t): t for t in seed["init_ref_tools"]}
    require(len(source) == len(seed["init_ref_tools"]), "Duplicate source symbols")
    capabilities = {c["id"]: c for c in package_profile["capabilities"]}
    requested = rule["capabilities"]
    require(len(requested) == len(set(requested)), "Repeated capability")
    require(set(requested) <= capabilities.keys(), f"Unknown capabilities: {requested}")
    candidates = {}
    for cap_id in requested:
        for spec in capabilities[cap_id]["symbols"]:
            symbol_key = key(spec)
            require(symbol_key in source, f"Capability symbol absent from selected input: {symbol_key}")
            candidates[symbol_key] = cap_id
    candidate_tools = [source[k] for k in candidates]
    for field in ("include", "exclude"):
        selectors = rule.get(field, [])
        require(len(selectors) == len(set(selectors)), f"Repeated {field} selector")
        for selector in selectors:
            require(any(matches(t, selector) for t in candidate_tools), f"Unmatched {field}: {selector}")
    methods = rule.get("methods", {})
    used_methods = set()
    chosen = []
    decisions = []
    for original in seed["init_ref_tools"]:
        identity = key(original)
        reason = "outside_scenario_capabilities"
        keep = identity in candidates
        if keep and "include" in rule:
            keep = any(matches(original, s) for s in rule["include"])
            reason = "outside_scenario_symbol_allowlist"
        if keep and any(matches(original, s) for s in rule.get("exclude", [])):
            keep = False
            reason = "excluded_scenario_symbol"
        selected = None
        if keep:
            selected = copy.deepcopy(original)
            selectors = [s for s in methods if matches(original, s)]
            require(len(selectors) <= 1, f"Ambiguous method selection: {identity}")
            if selectors:
                require(original["type"] == "class", "Methods requested on a function")
                selector = selectors[0]
                used_methods.add(selector)
                names = methods[selector]
                available = {m["name"] for m in original["function"]}
                require(len(names) == len(set(names)), f"Repeated methods: {identity}")
                require(set(names) <= available, f"Unavailable methods: {identity}: {set(names) - available}")
                require("__init__" not in available or "__init__" in names,
                        f"Cannot remove available constructor: {identity}")
                selected["function"] = [m for m in selected["function"] if m["name"] in names]
            chosen.append(selected)
        selected_methods = [m["name"] for m in (selected or {}).get("function", [])]
        decisions.append({"module": original["module"], "name": original["name"],
                          "type": original["type"], "selected": keep,
                          "capability": candidates.get(identity),
                          "reason": rule["reason"] if keep else reason,
                          "selected_methods": selected_methods,
                          "excluded_methods": [m["name"] for m in original.get("function", [])
                                               if m["name"] not in selected_methods]})
    require(used_methods == set(methods), f"Unused method filters: {set(methods) - used_methods}")
    require(bool(chosen), f"Package has no tools: {rule['package']}")
    return chosen, decisions


def merge_tools(groups: list[list[dict]]) -> list[dict]:
    """Deduplicate qualified identities; conflicting metadata is an error, never overwritten."""
    merged = {}
    for group in groups:
        for tool in group:
            identity = key(tool)
            if identity not in merged:
                merged[identity] = copy.deepcopy(tool)
                continue
            previous = merged[identity]
            if tool["type"] == "function":
                require(previous == tool, f"Conflicting function: {identity}")
                continue
            require({k: v for k, v in previous.items() if k != "function"} ==
                    {k: v for k, v in tool.items() if k != "function"}, f"Conflicting class: {identity}")
            methods = {m["name"]: m for m in previous["function"]}
            for method in tool["function"]:
                name = method["name"]
                require(name not in methods or methods[name] == method, f"Conflicting method: {identity}.{name}")
                if name not in methods:
                    previous["function"].append(copy.deepcopy(method))
                    methods[name] = method
    return list(merged.values())


def scenario_validator() -> Draft202012Validator:
    """Adapt only the explicitly requested envelope; preserve the original API schema."""
    schema = copy.deepcopy(read_json(ROOT / "schemas/validation/env_seeds.schema.json"))
    defs = schema["$defs"]
    defs["environmentSeed"]["properties"]["schema_version"] = {"const": "scenario-1.0"}
    defs["environmentSeed"]["properties"]["others"] = {
        "type": "array", "minItems": 1, "items": {"$ref": "#/$defs/otherInformation"}}
    defs["basicInfo"]["properties"]["description"] = {"type": "string"}
    defs["basicInfo"]["required"].append("description")
    env = defs["seedEnvironment"]
    env["properties"]["basic_info"] = {"type": "array", "minItems": 1,
                                         "items": {"$ref": "#/$defs/basicInfo"}}
    for field in ("scenario", "package_relation", "possible_task"):
        env["properties"][field] = {"type": "string", "minLength": 1}
        env["required"].append(field)
    env["properties"]["pypi_package"] = {"type": "array", "minItems": 1, "uniqueItems": True,
                                          "items": {"type": "string", "minLength": 1}}
    env["required"].append("pypi_package")
    defs["domain"] = {"type": "object", "required": ["level1", "level2", "level3"],
                      "additionalProperties": False,
                      "properties": {"level1": {"const": LEVEL1},
                                     "level2": {"type": "string", "pattern": r"^02\.\d{2} .+$"},
                                     "level3": {"type": "string", "pattern": r"^02\.\d{2}\.\d{2} .+$"}}}
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_seed(payload: list, row: dict, sources: list, selected_groups: list,
                  target: dict, exception: str, validator: Draft202012Validator) -> int:
    seed = payload[0]
    env = seed["environment"]
    require(seed["global_id"] == "semiconductor_scenario_" + row["id"].replace(".", "_"), "Wrong scenario ID")
    for field in ("scenario", "description", "package_relation", "possible_task"):
        require(env[field] == row[field], f"Table mapping mismatch: {field}")
    require(env["domain"] == {"level1": LEVEL1, "level2": row["l2"], "level3": row["l3"]}, "Domain mismatch")
    require(env["pypi_package"] == [s["environment"]["basic_info"]["name"] for s in sources], "Package mismatch")
    require(env["basic_info"] == [dict(s["environment"]["basic_info"], description=s["environment"]["description"])
                                  for s in sources], "Package metadata/description changed")
    require(seed["others"] == [s["others"] for s in sources], "Source others changed")
    require(env["nums"] == counts(seed["init_ref_tools"]), "Incorrect counts")
    require(target["min"] <= env["nums"]["all_func"] <= target["max"] or bool(exception), "Count exception needs justification")
    # Validate every copied callable against the selected source, including all documentation and signatures.
    for source, tools in zip(sources, selected_groups, strict=True):
        index = {key(t): t for t in source["init_ref_tools"]}
        for tool in tools:
            original = index[key(tool)]
            if tool["type"] == "class":
                require({k: v for k, v in tool.items() if k != "function"} ==
                        {k: v for k, v in original.items() if k != "function"}, "Changed class metadata")
                require(all(m in original["function"] for m in tool["function"]), "Changed or invented method")
            else:
                require(tool == original, "Changed or invented function")
    require(seed["init_ref_tools"] == merge_tools(selected_groups), "Merged tool mismatch")
    errors = list(validator.iter_errors(payload))
    expected = [e for e in errors if e.validator == "minLength" and e.instance == ""
                and list(e.absolute_path)[:2] == [0, "init_ref_tools"]
                and list(e.absolute_path)[-1] == "description"]
    require(len(errors) == len(expected), "Unexpected schema errors: " + "; ".join(
        str(e)[:400] for e in errors if e not in expected))
    return len(expected)


def build() -> dict[Path, str]:
    config = read_json(PROFILE)
    table = ROOT / config["source_table"]
    source_dir = ROOT / config["source_directory"]
    rows = parse_scenarios(table.read_text(encoding="utf-8"))
    rules = {r["id"]: r for r in config["scenarios"]}
    require(len(rules) == len(config["scenarios"]), "Duplicate scenario rules")
    require(set(rules) == {r["id"] for r in rows}, "Scenario profile does not cover the exact table")
    # Match the plain package profile filename, never a composite/application seed with the same package name.
    catalog = {}
    for path in sorted((ROOT / "seed_gen/pypi_selection_profiles").glob("*.json")):
        profile = read_json(path)
        name = profile["package"].casefold()
        require(name not in catalog, f"Ambiguous package profile: {name}")
        catalog[name] = (path, profile)
    validator = scenario_validator()
    files = {}
    summary = []
    for row in rows:
        rule = rules[row["id"]]
        sources, groups, source_reports = [], [], []
        seen = set()
        for selection in rule["packages"]:
            name = selection["package"].casefold()
            require(name not in seen, f"Repeated package: {name}")
            seen.add(name)
            profile_path, package_profile = catalog[name]
            path = source_dir / profile_path.name
            payload = read_json(path)
            require(isinstance(payload, list) and len(payload) == 1, f"Not a package seed: {path}")
            source = payload[0]
            info = source["environment"]["basic_info"]
            require(info["name"].casefold() == name and info["version"] == package_profile["version"], "Package identity mismatch")
            tools, decisions = select_package(source, package_profile, selection)
            sources.append(source)
            groups.append(tools)
            source_reports.append({"package": info["name"], "version": info["version"],
                                   "source": path.relative_to(ROOT).as_posix(), "source_sha256": digest(path),
                                   "capability_profile": profile_path.relative_to(ROOT).as_posix(),
                                   "capability_profile_sha256": digest(profile_path),
                                   "source_nums": counts(source["init_ref_tools"]), "selected_nums": counts(tools),
                                   "reason": selection["reason"], "decisions": decisions})
        tools = merge_tools(groups)
        env = {"basic_info": [dict(s["environment"]["basic_info"], description=s["environment"]["description"])
                              for s in sources],
               "scenario": row["scenario"], "description": row["description"],
               "pypi_package": [s["environment"]["basic_info"]["name"] for s in sources],
               "package_relation": row["package_relation"], "possible_task": row["possible_task"],
               "domain": {"level1": LEVEL1, "level2": row["l2"], "level3": row["l3"]}, "nums": counts(tools)}
        payload = [{"global_id": "semiconductor_scenario_" + row["id"].replace(".", "_"),
                    "schema_version": "scenario-1.0", "environment": env, "init_ref_tools": tools,
                    "init_ref_tasks": [], "others": [copy.deepcopy(s["others"]) for s in sources]}]
        exception = rule.get("count_exception", "")
        empty_count = validate_seed(payload, row, sources, groups, config["target_all_func"], exception, validator)
        report = {"scenario_id": row["id"], "global_id": payload[0]["global_id"],
                  "source_table": config["source_table"], "table_sha256": digest(table),
                  "scenario_profile": PROFILE.relative_to(ROOT).as_posix(), "profile_sha256": digest(PROFILE),
                  "table_package_combination": row["table_packages"], "packages": source_reports,
                  "nums": env["nums"], "top_level_tool_records": len(tools),
                  "target_all_func": config["target_all_func"], "count_exception": exception,
                  "boundaries": rule.get("boundaries", []),
                  "source_empty_description_count": empty_count,
                  "unexpected_schema_errors": 0, "runtime_verified": False}
        files[OUT / (row["id"] + ".json")] = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        files[REPORTS / (row["id"] + ".json")] = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        summary.append({"id": row["id"], "packages": env["pypi_package"], "nums": env["nums"],
                        "top_level_tool_records": len(tools), "count_exception": exception,
                        "empty_description_count": empty_count, "boundaries": report["boundaries"]})
    lines = ["# 场景种子：半导体 02 器件与物理仿真", "",
             f"从覆盖报告的 {len(rows)} 个场景及 package_select_func 中已有精选包重组。每个编号 JSON 保留原有单元素数组外壳。",
             "场景信息直接读取用户维护的 Markdown 表，不重新生成或覆盖该表。", "",
             "## 字段与来源", "",
             "- environment.basic_info 是包信息列表，每项为原 basic_info 加原 environment.description。",
             "- scenario、description、package_relation、possible_task 分别逐字对应表中的应用场景、场景具体描述、关系、可合成任务。",
             "- pypi_package 是实际贡献工具的 Python 包名列表；可选组合按场景选用，外部求解器、缺失发布包和非包类别不伪造 basic_info。原表组合文本与依赖边界保存在对应筛选报告中。",
             "- domain 使用指定中文 level1 和原表 L2/L3；Markdown 留空的 L2 由上一行补全。",
             "- others 与 basic_info 按相同包顺序排列，完整保留各包原 others；其中旧 package 级筛选数字和路径是来源快照，不是场景级统计。",
             "- init_ref_tools 保留源字段和文档；按 module/name/type 合并，类方法按名称去重，冲突报错。每个工具的包来源可查筛选报告。",
             "- init_ref_tasks 暂为空；possible_task 是任务设计方向，还没有生成或执行任务。",
             "- schema_version 为 scenario-1.0，明确区分 basic_info/others 为对象的旧包级 1.1 格式。", "",
             "## 筛选与计数", "",
             "筛选配置：../../pypi_scenario_selection.json。按场景选能力组，再指定排除符号或类方法；不是 LLM 评分或按数量截断。",
             "数量口径为 all_func = function + class_func；包括源选集已统计的属性和构造方法，不表示所有条目均是可直接独立调用的函数。",
             "顶层条目数 = class + function，单独列出，避免把类容器误认为只有一个操作。",
             "同一包可以在不同场景保留不同子集；多后端是可选路线，不承诺数组、模型、状态可直接互换。", "",
             "| 场景 | 实际合并包 | 类 | 函数 | 类方法 | all_func | 顶层条目 |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for item in summary:
        n = item["nums"]
        lines.append(f"| [{item['id']}]({item['id']}.json) | {' + '.join(item['packages'])} | {n['class']} | {n['function']} | {n['class_func']} | {n['all_func']} | {item['top_level_tool_records']} |")
    lines.extend(["", "## 数量例外与验证边界", ""])
    for item in summary:
        if item["count_exception"]:
            lines.append(f"- {item['id']}（{item['nums']['all_func']}）：{item['count_exception']}")
    lines.extend(["", "筛选报告位于 ../scenario_selection_reports/，包含所有候选工具的保留/剔除决定、方法子集、源文件哈希及依赖边界。",
                  "验证覆盖全部表格映射、来源元数据、工具/方法逐字段子集、合并冲突、计数和产物可重算性。旧工具 Schema 的空 description 问题按来源报告，不补造说明、不修改旧 Schema；其他结构错误必须为零。",
                  "本轮未安装整套依赖、运行物理仿真、实现包间桥接或生成可执行任务。已有选集可能未展开继承构造方法、属性或操作符；构建运行环境时需补做适配与小样例验证。",
                  "原报告中的功率守恒、无源性等验收条件须按材料损耗/有源性具体化，不能不加条件地用于全部模型。", "",
                  "## 重生成与检查", "", "```powershell",
                  "C:\\Apps\\anaconda3\\python.exe -m seed_gen.scripts.build_physics_scenario_seeds",
                  "C:\\Apps\\anaconda3\\python.exe -m seed_gen.scripts.build_physics_scenario_seeds --check",
                  "```", ""])
    files[OUT / "README.md"] = "\n".join(lines)
    files[REPORTS / "summary.json"] = json.dumps({"scenario_count": len(summary), "scenarios": summary}, ensure_ascii=False, indent=2) + "\n"
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Recompute all seeds and compare without writing")
    args = parser.parse_args()
    files = build()
    for directory in (OUT, REPORTS):
        existing = set(directory.glob("*.json"))
        expected = {p for p in files if p.parent == directory and p.suffix == ".json"}
        require(not existing - expected, f"Unexpected stale artifacts: {existing - expected}")
    for path, text in files.items():
        if args.check:
            require(path.exists() and path.read_text(encoding="utf-8") == text, f"Artifact differs: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    count = sum(path.parent == OUT and path.suffix == ".json" for path in files)
    print(f"{'Verified' if args.check else 'Generated'} {count} scenario seeds and their selection reports.")


if __name__ == "__main__":
    main()
