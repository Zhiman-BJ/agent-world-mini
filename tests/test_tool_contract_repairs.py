import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SPEC = importlib.util.spec_from_file_location('contract_repairs', Path(__file__).resolve().parents[1] / 'scripts/repair_tool_contracts.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


@pytest.fixture
def sweep(tmp_path):
    source = tmp_path / 'base.spice'
    source.write_text('* existing input\n.end\n')
    record = {'input_id': 'input-1', 'deck_path': 'base.spice'}
    context = SimpleNamespace(scope_root=lambda scope: tmp_path,
                              records=SimpleNamespace(get=lambda *args: record))
    namespace = {}
    exec(repair.SWEEP_CODE, namespace)
    arguments = {'scope_id': 'sensor_evidence', 'input_id': 'input-1', 'parameters': {'v': [1, 2]}}
    return namespace['run'], context, arguments, record, source


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_sweep_records_model_origin_and_combinations(sweep, tmp_path):
    run, context, args, record, source = sweep
    before = source.read_bytes()
    args['parameters']['t'] = [20, 30]
    result = run(args, context)
    assert result['success'] and result['data']['run_count'] == 4
    conditions = json.loads((tmp_path / result['data']['conditions_path']).read_text())
    assert conditions['model_scope_id'] == 'gf180_models'
    assert conditions['runs'][3]['parameters'] == {'v': 2, 't': 30}
    assert source.read_bytes() == before
    assert result['data']['original_preserved'] is True


def test_sweep_rejects_source_collision_before_writing(sweep, tmp_path):
    run, context, args, record, source = sweep
    directory = tmp_path / 'sweeps'
    directory.mkdir()
    source.rename(directory / 'sweep_000.spice')
    record['deck_path'] = 'sweeps/sweep_000.spice'
    before = files(tmp_path)
    result = run(args, context)
    assert result['success'] is False and result['error']['path'] == 'output_dir'
    assert files(tmp_path) == before


@pytest.mark.parametrize('name', ['sweep_000.spice', 'sweep_001.spice', 'conditions.json'])
def test_sweep_preserves_existing_outputs(sweep, tmp_path, name):
    run, context, args, record, source = sweep
    (tmp_path / 'sweeps').mkdir()
    (tmp_path / 'sweeps' / name).write_text('previous output')
    before = files(tmp_path)
    assert run(args, context)['success'] is False
    assert files(tmp_path) == before


def test_sweep_rolls_back_partially_published_files(sweep, tmp_path):
    run, context, args, record, source = sweep
    import os
    real_link = os.link
    count = 0

    def link(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('injected publication failure')
        return real_link(*args, **kwargs)

    before = files(tmp_path)
    with patch('os.link', link):
        result = run(args, context)
    assert result['success'] is False
    assert files(tmp_path) == before


def test_sweep_symlink_alias_to_source_is_rejected(sweep, tmp_path):
    run, context, args, record, source = sweep
    (tmp_path / 'sweeps').mkdir()
    (tmp_path / 'sweeps/sweep_000.spice').symlink_to(source)
    before = source.read_bytes()
    assert run(args, context)['success'] is False
    assert source.read_bytes() == before


def test_sweep_rejects_directory_escape(sweep, tmp_path):
    run, context, args, record, source = sweep
    args['output_dir'] = '../outside'
    before = files(tmp_path)
    assert run(args, context)['success'] is False
    assert files(tmp_path) == before


def test_declared_paper_default_is_executed_and_stays_optional():
    tool = {'name': 'validate_paper_bioc', 'inputSchema': {'type': 'object', 'properties': {'paper_id': {'type': 'string', 'enum': ['PMC8072673', 'PMC6584666'], 'description': '缺省时按主研究 PMC8072673 处理。'}}}, 'internal': {'code': "def run(arguments, context):\n paper_id=arguments['paper_id']\n return paper_id\n"}}
    fixed, rules = repair.repair_tool(tool)
    namespace = {}
    exec(fixed['internal']['code'], namespace)
    assert namespace['run']({}, None) == 'PMC8072673'
    assert namespace['run']({'paper_id': 'PMC6584666'}, None) == 'PMC6584666'
    assert 'paper_id' not in fixed['inputSchema'].get('required', [])
    assert repair.repair_tool(fixed) == (fixed, [])


def test_parallel_mapping_can_be_repaired_before_descriptions_are_filled():
    code = "def run(arguments, context):\n    left=right='pyuvm_runtime'\n    paths=arguments.get('paths',[])\n    mapping=arguments.get('left_runtime_paths',paths)\n    files=[]\n    for index,path in enumerate(paths):\n        files.append(mapping[index])\n    return files\n"
    tool = {'inputSchema': {'properties': {'left_runtime_paths': {'type': 'array'}}}, 'internal': {'code': code}}
    fixed, rules = repair.repair_tool(tool)
    assert 'parallel_path_length_guard' in rules
    assert fixed['inputSchema']['properties']['left_runtime_paths']['description']


def test_paper_default_is_shared_by_source_and_described_definitions():
    tool = {'name': 'validate_paper_bioc', 'inputSchema': {'type': 'object', 'properties': {'paper_id': {'type': 'string', 'enum': ['PMC8072673', 'PMC6584666']}}}, 'internal': {'code': "def run(arguments, context):\n return arguments['paper_id']\n"}}
    fixed, _ = repair.repair_tool(tool)
    assert fixed['inputSchema']['properties']['paper_id']['default'] == 'PMC8072673'
    assert 'paper_id' not in fixed['inputSchema'].get('required', [])
    ns = {}; exec(fixed['internal']['code'], ns)
    assert ns['run']({}, None) == 'PMC8072673'


@pytest.mark.parametrize('header,expected', [
    ('input a, output b', {'inputs': ['a'], 'outputs': ['b'], 'inouts': []}),
    ('input wire [7:0] a, b, output logic y, inout pad', {'inputs': ['a', 'b'], 'outputs': ['y'], 'inouts': ['pad']}),
    ('input a /* comment, output fake */, output b', {'inputs': ['a'], 'outputs': ['b'], 'inouts': []}),
    ('a, b', {'inputs': [], 'outputs': [], 'inouts': []}),
])
def test_ansi_port_directions(header, expected):
    ns = {}; exec(repair.RTL_PORT_DECLARATIONS, ns)
    assert ns['_declared_ports'](header) == expected


def test_optional_report_id_does_not_request_a_null_primary_key():
    tool = {'name': 'analyze_g_factor_uncertainty_windows', 'inputSchema': {'properties': {}}, 'internal': {'code': 'def run(arguments, context):\n    rid=arguments.get("report_id"); sid=arguments.get("sample_id"); state_id=arguments.get("trion_state_id")\n    filters={"report_id":rid,"sample_id":sid,"trion_state_id":state_id}\n    return filters\n'}}
    fixed, rules = repair.repair_tool(tool)
    ns = {}; exec(fixed['internal']['code'], ns)
    assert ns['run']({'report_id': None, 'sample_id': 's', 'trion_state_id': 't'}, None) == {'sample_id': 's', 'trion_state_id': 't'}
    assert ns['run']({'report_id': 'r', 'sample_id': 's', 'trion_state_id': 't'}, None) == {'report_id': 'r', 'sample_id': 's', 'trion_state_id': 't'}
    assert repair.repair_tool(fixed) == (fixed, [])


def test_grouped_metrics_and_test_inputs_keep_distinct_contracts():
    tool = {'name': 'compare_drivers', 'internal': {'code': 'counts[metric]'}, 'outputSchema': {'properties': {'groups': {'items': {'properties': {'values': {'type': 'object', 'description': '测试源码中的输入值。', 'properties': {'driver_count': {'type': 'integer'}}}}}}}}}
    fixed, rules = repair.repair_tool(tool)
    assert rules == ['grouped_metric_values_description']
    assert '累计统计值' in fixed['outputSchema']['properties']['groups']['items']['properties']['values']['description']
    assert repair.repair_tool(fixed) == (fixed, [])
    tool['outputSchema']['properties']['groups']['items']['properties']['values']['type'] = 'array'
    assert repair.repair_tool(tool) == (tool, [])


def test_secs_data_encoding_and_hsms_frame_keep_distinct_contracts():
    tool = {'name': 'update_equipment_constant_value', 'internal': {'code': 'encoded = typed.encode()'}, 'outputSchema': {'properties': {'encoded_hex': {'type': 'string', 'description': '含四字节长度前缀的完整线路字节十六进制表示。'}}}}
    fixed, rules = repair.repair_tool(tool)
    assert rules == ['secs_data_item_encoding_description']
    assert '数据项类型' in fixed['outputSchema']['properties']['encoded_hex']['description']
    assert repair.repair_tool(fixed) == (fixed, [])
    tool['name'] = 'build_decode_hsms_data_frame'
    tool['internal']['code'] = 'encoded = HsmsMessage(header, payload).blocks[0].encode()'
    assert repair.repair_tool(tool) == (tool, [])
    tool['name'] = 'encode_decode_secs_message'
    tool['internal']['code'] = 'encoded = original.encode()'
    fixed, _ = repair.repair_tool(tool)
    assert '无数据体时为空字符串' in fixed['outputSchema']['properties']['encoded_hex']['description']
    assert repair.repair_tool(fixed) == (fixed, [])
