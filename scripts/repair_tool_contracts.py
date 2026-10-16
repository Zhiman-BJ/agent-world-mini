#!/usr/bin/env python3
"""Plan or apply evidence-based contract repairs to ToolGen definitions.

Each repair is tied to an executable pattern. This command writes an audit plan;
publication uses a separately verified manifest with backups and source hashes.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from env_gen.tool_gen.semantic_contract import contract_issues, schema_nodes, reversible_null_sort


PUBLISH_DERIVATIVES_CODE = '''
def _publish_derivatives(root, source, documents):
    import os, tempfile
    from pathlib import Path
    root = root.resolve()
    source = source.resolve()
    targets = []
    for target, content in documents:
        target = target.resolve()
        target.relative_to(root)
        if target == source or target.exists() or target in targets:
            raise ValueError('output collides with source or an existing file')
        targets.append(target)
    created = []
    try:
        with tempfile.TemporaryDirectory(prefix='.derived-artifacts-', dir=root) as temporary:
            staged = []
            for index, ((_, content), target) in enumerate(zip(documents, targets)):
                stage = Path(temporary) / str(index)
                stage.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
                staged.append((stage, target))
            for stage, target in staged:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(stage, target)
                created.append(target)
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise

'''

RTL_PORT_DECLARATIONS = '''
def _declared_ports(header):
    import re
    header = re.sub(r'/\\*.*?\\*/|//[^\\n]*', '', header, flags=re.S)
    header = re.sub(r'\\(\\*.*?\\*\\)', '', header, flags=re.S)
    pieces, start, depth = [], 0, 0
    for index, character in enumerate(header):
        if character in '([{': depth += 1
        elif character in ')]}': depth -= 1
        elif character == ',' and depth == 0:
            pieces.append(header[start:index]); start = index + 1
    pieces.append(header[start:])
    result = {'inputs': [], 'outputs': [], 'inouts': []}
    direction = None
    for piece in pieces:
        declaration = re.match(r'\\s*(input|output|inout)\\b', piece)
        if declaration:
            direction = {'input': 'inputs', 'output': 'outputs', 'inout': 'inouts'}[declaration.group(1)]
            piece = piece[declaration.end():]
        if direction is None: continue
        piece = re.sub(r'\\[[^\\]]*\\]', '', piece.split('=', 1)[0])
        names = re.findall(r'[A-Za-z_]\\w*', piece)
        if names and names[-1] not in {'wire', 'reg', 'logic', 'signed', 'unsigned'}:
            result[direction].append(names[-1])
    return result

'''


SWEEP_CODE = '''def run(arguments, context):
    import itertools, json, os, re, tempfile
    from pathlib import Path

    def failure(path, message):
        return {'success': False, 'error': {'code': 'invalid_argument', 'path': path, 'message': message, 'retryable': False}}

    sid = arguments.get('scope_id')
    iid = arguments.get('input_id')
    parameters = arguments.get('parameters')
    if sid not in ('sensor_evidence', 'comparator_evidence'):
        return failure('scope_id', 'invalid scope_id')
    if not isinstance(iid, str) or not iid:
        return failure('input_id', 'input_id is required')
    if not isinstance(parameters, dict) or not parameters:
        return failure('parameters', 'parameters is required')
    created = []
    try:
        record = context.records.get('simulation_input', {'input_id': iid})
        if not record:
            return {'success': False, 'error': {'code': 'not_found', 'path': 'input_id', 'message': 'simulation input not found', 'retryable': False}}
        root = context.scope_root(sid).resolve()
        relative = Path(record['deck_path'])
        source = (root / relative).resolve()
        source.relative_to(root)
        if relative.is_absolute() or '..' in relative.parts or not source.is_file():
            return {'success': False, 'error': {'code': 'not_found', 'path': 'deck_path', 'message': 'source deck not found', 'retryable': False}}
        pairs = []
        for name, values in parameters.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) or not isinstance(values, list) or not values:
                return failure('parameters', 'invalid parameter values')
            pairs.append((name, values))
        directory = Path(arguments.get('output_dir', 'sweeps'))
        if directory.is_absolute() or '..' in directory.parts:
            return failure('output_dir', 'output_dir must be relative')
        destination = (root / directory).resolve()
        destination.relative_to(root)
        original = source.read_text()
        runs = []
        documents = []
        for index, combination in enumerate(itertools.product(*[values for _, values in pairs])):
            relative_deck = str(directory / f'sweep_{index:03d}.spice')
            target = (root / relative_deck).resolve()
            target.relative_to(root)
            if target == source or target.exists():
                return failure('output_dir', 'output collides with source or an existing file')
            text = '* generated parameter sweep\\n* source_input_id=' + iid + '\\n.param ' + ' '.join(f'{name}={value}' for (name, _), value in zip(pairs, combination)) + '\\n' + original
            documents.append((target, text))
            runs.append({'index': index, 'parameters': {name: value for (name, _), value in zip(pairs, combination)}, 'deck_path': relative_deck})
        model_scope = arguments.get('model_scope_id', 'gf180_models')
        conditions_path = str(directory / 'conditions.json')
        conditions_target = (root / conditions_path).resolve()
        conditions_target.relative_to(root)
        if conditions_target == source or conditions_target.exists():
            return failure('output_dir', 'conditions output collides with source or an existing file')
        documents.append((conditions_target, json.dumps({'source_input_id': iid, 'source_deck_path': record['deck_path'], 'model_scope_id': model_scope, 'runs': runs}, ensure_ascii=False, indent=2)))
        # Stage all bytes before publishing. Exclusive hard links prevent an
        # intervening writer from being overwritten; failures remove our files.
        with tempfile.TemporaryDirectory(prefix='.parameter-sweep-', dir=root) as temporary:
            staged = []
            for index, (target, text) in enumerate(documents):
                stage = Path(temporary) / str(index)
                stage.write_text(text)
                staged.append((stage, target))
            destination.mkdir(parents=True, exist_ok=True)
            for stage, target in staged:
                os.link(stage, target)
                created.append(target)
        return {'success': True, 'data': {'scope_id': sid, 'source_input_id': iid, 'source_deck_path': record['deck_path'], 'model_scope_id': model_scope, 'run_count': len(runs), 'decks': [run['deck_path'] for run in runs], 'conditions_path': conditions_path, 'original_preserved': True}}
    except (KeyError, TypeError, ValueError, OSError) as error:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        return {'success': False, 'error': {'code': 'query_error', 'path': 'input_id', 'message': str(error), 'retryable': False}}
'''


def stage_derived_writes(source: str, source_name: str, *, report_size_before_return=False) -> str:
    tree = ast.parse(source)
    calls_staged = 0
    publications = 0

    def publish():
        return ast.Expr(value=ast.Call(func=ast.Name(id='_publish_derivatives', ctx=ast.Load()), args=[ast.Name(id='root', ctx=ast.Load()), ast.Name(id=source_name, ctx=ast.Load()), ast.Name(id='documents', ctx=ast.Load())], keywords=[]))

    class Rewrite(ast.NodeTransformer):
        def visit_Call(self, node):
            nonlocal calls_staged
            node = self.generic_visit(node)
            if isinstance(node.func, ast.Attribute) and node.func.attr in ('write_text', 'write_bytes'):
                calls_staged += 1
                return ast.Call(func=ast.Attribute(value=ast.Name(id='documents', ctx=ast.Load()), attr='append', ctx=ast.Load()), args=[ast.Tuple(elts=[node.func.value, node.args[0]], ctx=ast.Load())], keywords=[])
            return node

        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'mkdir':
                return ast.Pass()
            return self.generic_visit(node)

        def visit_Assign(self, node):
            nonlocal publications
            node = self.generic_visit(node)
            if report_size_before_return and any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == 'report' and isinstance(target.slice, ast.Constant) and target.slice.value == 'report_size' for target in node.targets):
                publications += 1
                return [publish(), node]
            return node

        def visit_Return(self, node):
            nonlocal publications
            node = self.generic_visit(node)
            if not report_size_before_return and isinstance(node.value, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == 'success' and isinstance(v, ast.Constant) and v.value is True for k, v in zip(node.value.keys, node.value.values)):
                publications += 1
                return [publish(), node]
            return node

    tree = Rewrite().visit(tree)
    assert calls_staged == 2 and publications == 1, (calls_staged, publications)
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    run.body.insert(0, ast.Assign(targets=[ast.Name(id='documents', ctx=ast.Store())], value=ast.List(elts=[], ctx=ast.Load())))
    return PUBLISH_DERIVATIVES_CODE + ast.unparse(ast.fix_missing_locations(tree)) + '\n'


def repair_tool(tool: dict) -> tuple[dict, list[str]]:
    result = copy.deepcopy(tool)
    rules = []
    source = result.get('internal', {}).get('code', '')
    fields = result.get('inputSchema', {}).get('properties', {})
    # These handlers and their enum explicitly promise the same default paper.
    if (result.get('name') in {'validate_paper_bioc', 'convert_paper_figure', 'assemble_paper_figure_montage'}
            and fields.get('paper_id', {}).get('enum') == ['PMC8072673', 'PMC6584666']
            and "arguments['paper_id']" in source):
        source = source.replace("arguments['paper_id']", "arguments.get('paper_id', 'PMC8072673')")
        result['internal']['code'] = source
        fields['paper_id']['default'] = 'PMC8072673'
        rules.append('declared_paper_default_implemented')
    for issue in contract_issues(result):
        if issue['rule'] == 'unconditional_input_optional':
            field = issue['pointer'].rsplit('/', 1)[-1]
            required = result['inputSchema'].setdefault('required', [])
            if field not in required:
                required.append(field)
                rules.append('required_straight_line_input')
        elif issue['rule'] == 'nonempty_input_described_empty':
            node = next(node for pointer, node in schema_nodes(result['inputSchema'], '/inputSchema') if pointer == issue['pointer'])
            description = node['description']
            clauses = re.split(r'[；。]', description)
            kept = [clause for clause in clauses if clause and not re.search(r'无法[^；。]*时为空|(?<!不)允许为空|(?<!不)可以为空|(?<!不)可为空', clause)]
            node['description'] = '；'.join(kept + ['输入时须为非空字符串']) + '。'
            rules.append('nonempty_input_description')

    if result.get('name') == 'compare_grating_segments' and 'segment must be an inclusive in-range pair' in source:
        for field in ('dataset_id', 'segment_a', 'segment_b'):
            required = result['inputSchema'].setdefault('required', [])
            if field not in required:
                required.append(field)
                rules.append('required_grating_comparison_input')

    if 'for index,path in enumerate(paths):' in source and "arguments.get('left_runtime_paths'" in source and 'mapping must have the same length' not in source:
        guard = """    for side,scope in (('left',left),('right',right)):
        if scope in ('pyuvm_runtime','pyuvm_build'):
            key=side+('_runtime_paths' if scope=='pyuvm_runtime' else '_build_paths')
            mapping=arguments.get(key,paths)
            if not isinstance(mapping,list) or len(mapping)!=len(paths):
                return {'success':False,'error':{'code':'invalid_input','path':key,'message':'mapping must have the same length as paths','retryable':False}}
"""
        result['internal']['code'] = source.replace('    files=[]\n', guard + '    files=[]\n', 1)
        assert result['internal']['code'] != source
        for field in ('left_runtime_paths', 'right_runtime_paths', 'left_build_paths', 'right_build_paths'):
            if field in fields:
                fields[field]['description'] = fields[field].get('description', '与 paths 逐项对应的来源文件路径列表').rstrip('。') + '；可省略，省略时沿用 paths；提供时长度须与 paths 相同，否则返回 invalid_input。'
        rules.append('parallel_path_length_guard')

    if result.get('name') == 'prepare_parameter_sweep' and '* generated parameter sweep' in source and 'original_preserved' in source and 'Exclusive hard links' not in source:
        result['internal']['code'] = SWEEP_CODE
        result['description'] = '读取现有仿真测试台，按参数取值的笛卡尔积生成测试台副本及 conditions.json；副本在原文本前追加参数定义，清单记录来源输入、模型范围和各组参数。仅准备文本文件，不读取或验证模型、不执行仿真，也不保证覆盖原文中后续的同名参数定义。输出与原输入或已有文件重合时拒绝写入；发布失败时回收本次已创建的文件，保留原输入和既有输出。'
        fields['model_scope_id']['description'] = '声明的器件模型来源范围，固定为 gf180_models；写入 conditions.json 并在结果中返回，不触发模型读取、可用性验证或仿真。'
        fields['output_dir']['description'] = '所选证据 Scope 内的相对输出目录，默认为 sweeps；不得越界。将生成 sweep_000.spice 等文件及 conditions.json，任一目标与原输入或已有文件重合时失败。'
        result['usageConditions']['preconditions'] = ['指定仿真输入可读取，参数及非空取值列表已给出；本次目标文件均未存在且不与原输入重合。']
        result['usageConditions']['sideEffects'] = ['在证据 Scope 中生成参数测试台副本及条件清单；保留原输入和既有文件，发布失败时移除本次已发布文件。']
        rules.append('sweep_artifact_provenance_and_preservation')

    if result.get('name') == 'prepare_monte_carlo_plan' and '* generated Monte Carlo plan' in source and '_publish_derivatives' not in source:
        result['internal']['code'] = stage_derived_writes(source.replace("'seed':seed+i", "'seed':seed"), 'q')
        result['description'] = '读取现有比较器测试台，按指定随机种子和分布生成失配偏置样本、测试台副本及 plan.json；所有样本依次来自同一随机序列。仅准备计划和文本，不读取或验证模型、不执行仿真；副本前置参数不保证覆盖原文后续同名定义。输出与原输入或已有文件重合时拒绝写入，发布失败时回收本次已创建文件。'
        if 'output_dir' in fields:
            fields['output_dir']['description'] = 'comparator_evidence Scope 内的相对输出目录，默认为 sim/mc_plan；生成 mc0000.spice 等文件和 plan.json，任一目标与原输入或已有文件重合时失败。'
        result['usageConditions']['sideEffects'] = ['生成 Monte Carlo 计划及测试台副本；保留原输入和既有文件，发布失败时移除本次已发布文件。']
        for pointer, node in schema_nodes(result.get('outputSchema', {})):
            if pointer.endswith('/items/properties/seed'):
                node['description'] = '生成整组样本使用的随机种子；各样本按 sample_index 从同一随机序列依次取值。'
        rules.append('monte_carlo_artifact_preservation')

    if result.get('name') == 'convert_scope_artifacts' and "out_rel=stem+'.converted.'+fmt" in source and '_publish_derivatives' not in source:
        source_fixed = source.replace("rows=['tag,attributes,text']+[json.dumps([e.tag,dict(e.attrib),(e.text or '').strip()],ensure_ascii=False) for e in tree.iter()]\n                converted_bytes=('\\n'.join(rows)+'\\n').encode()", "stream=io.StringIO(newline=''); writer=csv.writer(stream); writer.writerow(['tag','attributes','text']); writer.writerows((e.tag,json.dumps(dict(e.attrib),ensure_ascii=False),(e.text or '').strip()) for e in tree.iter())\n                converted_bytes=stream.getvalue().encode()")
        source_fixed = source_fixed.replace("lines=text.splitlines(); converted_bytes=('line_number,text\\n'+'\\n'.join(f'{i+1},{json.dumps(line,ensure_ascii=False)}' for i,line in enumerate(lines))+'\\n').encode(); summary={'kind':'text','line_count':len(lines)}", "lines=text.splitlines(); stream=io.StringIO(newline=''); writer=csv.writer(stream); writer.writerow(['line_number','text']); writer.writerows((i+1,line) for i,line in enumerate(lines)); converted_bytes=stream.getvalue().encode(); summary={'kind':'text','line_count':len(lines)}")
        assert source_fixed != source
        result['internal']['code'] = stage_derived_writes(source_fixed, 'source', report_size_before_return=True)
        result['description'] = result['description'].rstrip('。') + '。转换产物和 JSON 报告均须为新文件；目标与源文件或已有文件重合时失败，发布失败时移除本次已创建文件。'
        rules.append('conversion_artifact_preservation_and_csv')

    if reversible_null_sort(source):
        for pointer, node in schema_nodes(result.get('inputSchema', {})):
            description = node.get('description', '')
            if '/sort' not in pointer or not re.search(r'空值|缺失值|为 null', description):
                continue
            text = '升序时空值排在非空值之后，降序时排在非空值之前'
            if text in description or '空值记录排在非空值之后（升序时）或之前（降序时）' in description:
                continue
            pattern = r'(?:排序键为空的记录|该字段为 null 的记录|传感器值为空的记录|空值|缺失值)[^；。]*(?:排|排序|位置|末尾)[^；。]*'
            fixed_description, count = re.subn(pattern, text, description)
            if count and fixed_description != description:
                node['description'] = fixed_description
                rules.append('null_sort_direction_description')

    for key in ('inputSchema', 'outputSchema'):
        for pointer, node in schema_nodes(result.get(key, {})):
            if node.get('type') != 'string':
                continue
            if node.get('description') == '用于划分统计分组的一个或多个字段。':
                node['description'] = '用于划分统计分组的字段。'
                rules.append('scalar_grouping_description')
            if node.get('description') == '按顺序应用的结果排序规则。':
                node['description'] = '用于结果排序的字段。'
                rules.append('scalar_ordering_description')
    for pointer, node in schema_nodes(result.get('outputSchema', {})):
        if (pointer.endswith('/properties/groups/items/properties/values')
                and node.get('type') == 'object' and node.get('properties')
                and node.get('description') == '测试源码中的输入值。'
                and 'counts[metric]' in source
                and all(field.get('type') in ('integer', 'number') for field in node['properties'].values())):
            node['description'] = '当前分组内所选指标的累计统计值；键名对应 metrics，每项指标的含义见其字段说明。'
            rules.append('grouped_metric_values_description')
        if (pointer.endswith('/properties/encoded_hex')
                and node.get('description') in {'含四字节长度前缀的完整线路字节十六进制表示。', 'SECS 数据项编码字节的十六进制表示，包含数据项类型、长度及内容。'}
                and ((result.get('name') in {'update_simulated_status_value', 'update_equipment_constant_value'}
                      and 'encoded = typed.encode()' in source)
                     or (result.get('name') == 'encode_decode_secs_message'
                         and 'encoded = original.encode()' in source))):
            text = ('消息数据体的 SECS 编码结果（十六进制）；无数据体时为空字符串。'
                    if result.get('name') == 'encode_decode_secs_message'
                    else 'SECS 数据项编码字节的十六进制表示，包含数据项类型、长度及内容。')
            if node.get('description') != text:
                node['description'] = text
                rules.append('secs_data_item_encoding_description')
    if result.get('name') == 'generate_reliability_review' and "out.suffix.lower()!='.md'" in source:
        text = 'qualification_reports Scope 内新建 Markdown 报告的相对路径，扩展名须为 .md；不得越界或指向已有文件。例如 reviews/batch_review.md。'
        if fields['output_name'].get('description') != text:
            fields['output_name']['description'] = text
            rules.append('report_output_path_description')
    if result.get('name') == 'compare_workflow_variants' and "'different'" in source:
        for _, node in schema_nodes(result.get('outputSchema', {})):
            if node.get('description') == '该维度在所有参与工作流间是否完全一致；true 表示至少一个工作流与其余不同。':
                node['description'] = '该维度在参与工作流间是否存在差异；true 表示至少一个工作流与其余不同。'
                rules.append('comparison_boolean_description')
    if result.get('name') == 'assess_gixrd_xrr_comparability' and 'len(xfits)' in source:
        for pointer, node in schema_nodes(result.get('outputSchema', {})):
            if pointer.endswith('/properties/xrr_coverage_sample_count'):
                text = 'XRR 系列中 coverage_percent 非空的拟合记录数量；同一样件的多条拟合分别计数。'
                if node.get('description') != text:
                    node['description'] = text
                    rules.append('coverage_fit_count_description')
    if result.get('name') == 'crosscheck_xrd_raman_structure' and "published_media(context, 'F4', 'a–d')" in source:
        for pointer, node in schema_nodes(result.get('outputSchema', {})):
            if pointer.endswith('/properties/evidence/properties/panel'):
                text = '本次引用的发表图板范围，固定为图 4 的 a–d 子图。'
                if node.get('description') != text:
                    node['description'] = text
                    rules.append('published_panel_description')
    if result.get('name') == 'analyze_simulation_head_coverage' and 'module_names' in source:
        for pointer, node in schema_nodes(result.get('outputSchema', {})):
            if pointer.endswith('/properties/entries/items/properties/name'):
                text = '当前覆盖维度中的条目键，即模块名、模型 section 名或配置参数名。'
                if node.get('description') != text:
                    node['description'] = text
                    rules.append('simulation_entry_name_description')
    if result.get('name') == 'select_eds_lines' and 'signal.set_lines' in source:
        for pointer, node in schema_nodes(result.get('outputSchema', {})):
            if pointer.endswith('/properties/signal_line_names'):
                text = '设置元素及显式谱线后，信号内存元数据中的谱线名称列表；仅用于当前调用，不写回信号文件。'
                if node.get('description') != text:
                    node['description'] = text
                    rules.append('eds_line_metadata_description')
    if result.get('name') == 'open_research_source' and "row['article_path'].split(':',1)" in source:
        text = 'study 记录中的 article_path，格式为 surge_2019:<相对 PDF 路径> 或 surge_2026:<相对 HTML 路径>；也接受对应 aw://<scope>/<路径>，必须与所选研究的记录一致。'
        if fields['article_path'].get('description') != text:
            fields['article_path']['description'] = text
            rules.append('research_source_qualified_path_description')
    if result.get('name') == 'get_vasp_run_steps' and "limit=1000, order_by='ionic_step'" in source:
        for pointer, node in schema_nodes(result.get('outputSchema', {})):
            if pointer.endswith('/properties/steps'):
                text = '该作业的 VASP 离子步记录列表，按步索引升序最多返回 1000 条本地记录；指定 ionic_step 时仅返回该步。'
                if node.get('description') != text:
                    node['description'] = text
                    rules.append('vasp_step_limit_description')
    if result.get('name') == 'check_stdf_record_consistency' and 'len(keys)!=len(set(keys))' in source:
        for key, value in [('minItems', 1), ('uniqueItems', True)]:
            if fields['comparison_keys'].get(key) != value:
                fields['comparison_keys'][key] = value
                rules.append('comparison_keys_validation')

    if result.get('name') == 'query_run_details' and 'truncated[section] = offset + limit < len(rows)' in source:
        texts = {
            'truncated': '各数组明细集合在本次返回区间之后是否还有记录；仅跳过区间之前的记录不使该标记为 true，未请求的集合为 false。',
            'counts': '各数组明细集合的完整匹配记录数，未请求的集合为 0；summary 不在此映射中。',
        }
        for pointer, node in schema_nodes(result.get('outputSchema', {})):
            for field, text in texts.items():
                if pointer.endswith('/properties/' + field) and node.get('description') != text:
                    node['description'] = text
                    rules.append('collection_pagination_description')
    if re.search(r"(?m)^SOURCE\s*=\s*['\"]job['\"]", source) and result.get('name') == 'relation_job_to_benchmark_instance':
        for pointer, node in schema_nodes(result.get('outputSchema', {})):
            if pointer.endswith('/properties/source') and node.get('description') == '结果归档。':
                node['description'] = '配对中的 job 作业记录，包含作业主键、所属实例及作业约束字段。'
                rules.append('relation_source_record_description')
    if result.get('name') == 'analyze_hdf5_spectral_data' and "out.write_text(json.dumps({'analysis':" in source:
        text = '读取 ge_workspace 中的 HDF5 数据集，转换为浮点数组，报告形状及有限值状态，或沿指定轴积分后求和、报告按绝对最大值归一化后的形状、沿其余维度取均值后检测峰值。结果写入 JSON 文件；输出须在同一 Scope 内且与源文件不同，已有目标文件会被覆盖。'
        if result.get('description') != text:
            result['description'] = text
            rules.append('hdf5_output_overwrite_description')
    if result.get('name') == 'inspect_rtl_sdc' and '  for d,names in re.findall(' in source and '_declared_ports' not in source:
        start = source.index('  for d,names in re.findall(')
        end = source.index('  modules[m.group(1)]', start)
        replacement = "  ports=_declared_ports(m.group(2))\n  ins,outs,ios=ports['inputs'],ports['outputs'],ports['inouts']\n"
        result['internal']['code'] = RTL_PORT_DECLARATIONS + source[:start] + replacement + source[end:]
        rules.append('rtl_ansi_port_direction_parsing')
    selectors = 'filters={"report_id":rid,"sample_id":sid,"trion_state_id":state_id}'
    if result.get('name') == 'analyze_g_factor_uncertainty_windows' and selectors in source:
        result['internal']['code'] = source.replace(selectors, 'filters={"sample_id":sid,"trion_state_id":state_id}\n    if rid is not None: filters["report_id"]=rid', 1)
        rules.append('optional_report_selector_omitted_from_record_filters')

    # This exact expression additionally establishes interval overlap filtering.
    compact = re.sub(r'\s+', '', source)
    if "key=lambdar:(r[key]isNone,r[key]ifr[key]isnotNoneelse'',r['condition_id'])" in compact and "reverse=sort.get('descending',False)" in compact:
        for _, node in schema_nodes(result.get('inputSchema', {})):
            description = node.get('description', '')
            if '空值排在非空值之前' in description:
                node['description'] = description.replace('空值排在非空值之前', '升序时空值排在非空值之后，降序时排在非空值之前')
                rules.append('null_sort_direction_description')
        if 'energy_min_j_cm2' in fields.get('filters', {}).get('properties', {}):
            energy = fields['filters']['properties']
            for key, text in [('energy_min_j_cm2', '请求能量区间的下限，单位 J/cm²，含端点；记录的能量区间与请求区间有交集即可命中，能量范围缺失的记录被排除。'), ('energy_max_j_cm2', '请求能量区间的上限，单位 J/cm²，含端点；记录的能量区间与请求区间有交集即可命中，上限不得低于下限。')]:
                if energy[key].get('description') != text:
                    energy[key]['description'] = text
                    rules.append('energy_interval_overlap_description')

    if "len(parts)<4" in compact and "parts[0]!='projects'" in compact and "target.suffix.lower()notin('.csv','.json')" in compact:
        for schema_key in ('inputSchema', 'outputSchema'):
            for pointer, node in schema_nodes(result.get(schema_key, {})):
                if pointer.endswith('/properties/output_file'):
                    text = '相对于 circuit_projects Scope 根目录的输出路径，须为 projects/<project_id>/<子目录>/<文件名>.csv 或 .json，project_id 必须与指定运行一致。例如 projects/my_project/exports/terminals.csv。目标文件存在时会覆盖其内容。'
                    if node.get('description') != text:
                        node['description'] = text
                        rules.append('project_export_path_description')
    # Restore record lookup results for the four confirmed bidirectional tools.
    relation_targets = {
        'relate_calculations_host_id_to_hosts': ('calculations', 'calculation_id'),
        'relate_calculations_configuration_id_to_configurations': ('calculations', 'calculation_id'),
        'relate_calculations_reference_id_to_calculations': ('calculations', 'calculation_id'),
        'relate_configurations_host_id_to_hosts': ('configurations', 'configuration_id'),
    }
    if result.get('name') in relation_targets:
        collection, identifier = relation_targets[result['name']]
        pattern = r"\{'relation_count':len\((\w+)\)\}"
        replacement = lambda match: ("{'relation_count':len(" + match[1] + "),'" + collection
                                    + "':[{'" + identifier + "':r['" + identifier
                                    + "']} for r in " + match[1] + "]}")
        fixed, count = re.subn(pattern, replacement, source)
        if count:
            if count != 1 or "context.records.list('" + collection + "'" not in source:
                raise ValueError('unexpected relation implementation: ' + result['name'])
            result['internal']['code'] = fixed
            for pointer, node in schema_nodes(result.get('outputSchema', {})):
                if pointer.endswith('/properties/data'):
                    node['properties'][collection] = {
                        'type': 'array',
                        'items': {'type': 'object', 'properties': {identifier: {'type': 'string'}},
                                  'required': [identifier], 'additionalProperties': False},
                        'description': '关联记录的标识列表；反向查询最多返回10000条匹配记录。',
                    }
            rules.append('relation_reverse_record_identifiers')

    if result.get('name') == 'k_mesh_from_samples':
        fixed = source.replace("json.dumps(meta,ensure_ascii=False,indent=2)+'\\\\n'",
                               "json.dumps(meta,ensure_ascii=False,indent=2)+'\\n'")
        if fixed != source:
            result['internal']['code'] = fixed
            rules.append('mesh_metadata_json_newline')

    if result.get('name') == 'simulate_openilt_aerial_image':
        guard = "expected_word not in record['config_id'].lower()"
        exclusion = "condition == 'focus' and 'defocus' in record['config_id'].lower()"
        if guard in source and exclusion not in source:
            result['internal']['code'] = source.replace(guard, '(' + guard + ' or (' + exclusion + '))')
            rules.append('optical_focus_condition_match')
    return result, rules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    entries = []
    for path in args.files:
        document = json.loads(path.read_text())
        for index, tool in enumerate(document['tools']):
            fixed, rules = repair_tool(tool)
            if rules:
                entries.append({'path': str(path), 'index': index, 'name': tool['name'], 'rules': rules, 'before_sha256': hashlib.sha256(json.dumps(tool, sort_keys=True).encode()).hexdigest(), 'after': fixed})
    args.plan.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'tools_changed': len(entries), 'plan': str(args.plan)}))


if __name__ == '__main__':
    main()
