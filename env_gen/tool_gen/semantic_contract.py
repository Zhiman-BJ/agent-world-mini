"""Static checks for public tool contracts that can be established from code.

These checks deliberately avoid guessing whether conditionally accessed inputs
are mandatory. Domain algorithms still need executable acceptance tests.
"""
from __future__ import annotations

import ast
from functools import lru_cache
import re
from typing import Any, Iterator


def schema_nodes(schema: Any, pointer: str = "") -> Iterator[tuple[str, dict]]:
    if not isinstance(schema, dict):
        return
    yield pointer, schema
    for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
        for name, child in (schema.get(key) or {}).items():
            yield from schema_nodes(child, pointer + "/" + key + "/" + str(name).replace("~", "~0").replace("/", "~1"))
    for key in ("items", "additionalProperties", "contains", "propertyNames", "not", "if", "then", "else"):
        yield from schema_nodes(schema.get(key), pointer + "/" + key)
    for key in ("prefixItems", "oneOf", "anyOf", "allOf"):
        for index, child in enumerate(schema.get(key, [])):
            yield from schema_nodes(child, pointer + "/" + key + "/" + str(index))


def _literal_key(node: ast.AST, argument: str) -> str | None:
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name) and node.value.id == argument:
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return node.slice.value
    return None


def _reads(node: ast.AST, argument: str) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return set()
    # Only the test is unconditional in a conditional expression. Boolean RHS
    # and comprehension bodies may never execute.
    if isinstance(node, ast.IfExp):
        return _reads(node.test, argument) | (_reads(node.body, argument) & _reads(node.orelse, argument))
    if isinstance(node, ast.BoolOp):
        return _reads(node.values[0], argument)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return _reads(node.generators[0].iter, argument)
    key = _literal_key(node, argument)
    found = {key} if key else set()
    for child in ast.iter_child_nodes(node):
        found.update(_reads(child, argument))
    return found


@lru_cache(maxsize=2048)
def unconditional_inputs(source: str) -> set[str]:
    """Inputs directly indexed on the straight-line path of run()."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError):
        return set()
    run = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"), None)
    if run is None or not run.args.args:
        return set()
    argument = run.args.args[0].arg
    required: set[str] = set()
    # A prior return can make a later direct lookup conditional. Stop at the
    # first branch/loop/try rather than infer requirements across control flow.
    for statement in run.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(statement, (ast.If, ast.For, ast.While, ast.Try, ast.With)) or type(statement).__name__ == "Match":
            if isinstance(statement, (ast.If, ast.While)):
                required.update(_reads(statement.test, argument))
            elif isinstance(statement, ast.For):
                required.update(_reads(statement.iter, argument))
            break
        required.update(_reads(statement, argument))
        if isinstance(statement, (ast.Return, ast.Raise)):
            break
    return required


@lru_cache(maxsize=2048)
def guarded_required_inputs(source: str) -> set[str]:
    """Recognize explicit missing-input failure guards at the top of run()."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError):
        return set()
    run = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run'), None)
    if run is None or not run.args.args:
        return set()
    argument = run.args.args[0].arg
    aliases: dict[str, str] = {}
    required: set[str] = set()

    def missing_test(node: ast.AST, alias: str):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            if isinstance(node.operand, ast.Name) and node.operand.id == alias:
                return True
            call = node.operand
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'isinstance' and len(call.args) == 2 and isinstance(call.args[0], ast.Name) and call.args[0].id == alias:
                if isinstance(call.args[1], ast.Name) and call.args[1].id in {'str', 'list', 'dict', 'int', 'float', 'bool'}:
                    return True
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == alias and len(node.ops) == 1 and isinstance(node.ops[0], ast.Is) and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value is None:
            return True
        if isinstance(node, ast.BoolOp):
            values = [missing_test(n, alias) for n in node.values]
            if isinstance(node.op, ast.Or) and any(v is True for v in values):
                return True
            if isinstance(node.op, ast.And) and all(v is True for v in values):
                return True
        return None

    def failure_return(node: ast.AST) -> bool:
        if not isinstance(node, ast.Return):
            return False
        value = node.value
        if isinstance(value, ast.Dict):
            return any(isinstance(k, ast.Constant) and k.value == 'success' and isinstance(v, ast.Constant) and v.value is False for k, v in zip(value.keys, value.values))
        return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and bool(re.search('fail|err', value.func.id, re.I))

    for statement in run.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if not isinstance(target, ast.Name):
                    continue
                aliases.pop(target.id, None)
                call = statement.value
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == 'get' and isinstance(call.func.value, ast.Name) and call.func.value.id == argument and len(call.args) == 1 and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    aliases[target.id] = call.args[0].value
        if isinstance(statement, ast.If) and any(failure_return(n) for n in statement.body):
            for alias, field in aliases.items():
                if missing_test(statement.test, alias) is True:
                    required.add(field)
        if isinstance(statement, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
            # Defaults or alternate data sources may replace a get() result.
            # Once a branch can assign the local, its absence no longer proves
            # that the original request field was missing.
            assigned = {node.id for node in ast.walk(statement) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
            for alias in assigned:
                aliases.pop(alias, None)
            if any(isinstance(node, ast.Return) and not failure_return(node) for node in ast.walk(statement)):
                break
        if isinstance(statement, ast.Return):
            break
    return required


def contract_issues(tool: dict[str, Any]) -> list[dict[str, str]]:
    """Return precise, machine-checkable contradictions, not a quality score."""
    issues: list[dict[str, str]] = []
    schema = tool.get("inputSchema", {})
    required = set(schema.get("required", []))
    fields = schema.get("properties", {})
    source = tool.get("internal", {}).get("code", "")
    must_supply = unconditional_inputs(source) | guarded_required_inputs(source)
    if any(key in schema for key in ('oneOf', 'anyOf', 'allOf', 'if')):
        must_supply = set()
    for field in sorted(must_supply - required):
        if field in fields:
            issues.append({"rule": "unconditional_input_optional", "pointer": "/inputSchema/properties/" + field, "detail": f"run()直接索引或显式拒绝缺失的{field}，但required未声明该字段"})
    for pointer, node in schema_nodes(schema, "/inputSchema"):
        description = str(node.get("description", ""))
        if node.get("type") == "string" and node.get("minLength", 0) > 0:
            if re.search(r"(?:无法[^；。]*时为空|(?<!不)允许为空|(?<!不)可以为空|(?<!不)可为空)", description):
                issues.append({"rule": "nonempty_input_described_empty", "pointer": pointer, "detail": "输入约束要求非空字符串，说明却允许空值"})
    for schema_key in ('inputSchema', 'outputSchema'):
        for pointer, node in schema_nodes(tool.get(schema_key, {}), '/' + schema_key):
            if node.get('type') == 'string' and node.get('description') in {
                '用于划分统计分组的一个或多个字段。', '按顺序应用的结果排序规则。'
            }:
                issues.append({'rule': 'scalar_described_collection', 'pointer': pointer, 'detail': '单个枚举字符串被描述为多个字段或有序规则集合'})
    return issues


@lru_cache(maxsize=2048)
def reversible_null_sort(source: str) -> bool:
    """Recognize a nullable primary key reversed as part of the full tuple."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not ((isinstance(node.func, ast.Attribute) and node.func.attr == 'sort') or (isinstance(node.func, ast.Name) and node.func.id == 'sorted')):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords}
        key, reverse = keywords.get('key'), keywords.get('reverse')
        if not isinstance(key, ast.Lambda) or not isinstance(key.body, ast.Tuple) or not key.body.elts or reverse is None:
            continue
        first = key.body.elts[0]
        if isinstance(first, ast.Compare) and len(first.ops) == 1 and isinstance(first.ops[0], ast.Is) and isinstance(first.comparators[0], ast.Constant) and first.comparators[0].value is None:
            if not isinstance(reverse, ast.Constant):
                return True
    return False


CONTRACT_REVIEW_GUIDANCE = """逐项核对公开契约与可执行行为：成功调用不可缺少的参数与required一致；省略、空字符串和null分别说明，不能把记录字段的空值规则直接复制到筛选参数。records.list 的 filters 中 None 不会忽略该条件：字段允许空值时筛选 IS NULL，否则会被类型校验拒绝；省略可选查询键时应删除该键，用户明确筛选空值的字段则保留。同名字段须核对当前返回表达式及数据来源，例如分组指标 values 与测试输入 values、SECS 数据项编码与完整 HSMS 通信帧各有其含义。核对默认值、区间含端点及交集/包含规则、升降序下空值位置、截断和分页。路径说明给出实际相对基准、目录结构与扩展名限制。参数声称参与计算、校验或写入产物时，必须有相应行为；仅回显元数据应明确说明。承诺保留原输入时，在写入前检查全部输出与输入的路径碰撞，并测试原输入与已有输出的保护。并行数组先检查长度；失败应返回已声明错误且保持原状态。针对每项承诺使用独立预期值验证，不能仅检查success。"""
