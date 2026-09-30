from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from env_gen.tool_gen.compiler import ToolGenerator
from env_gen.tool_gen.runtime import ToolPackage
from tests import test_kimi_mcp as fixtures


def test_tool_requires_success_and_checked_result(tmp_path):
    fixtures.KimiMcpTests()._make_delivery(tmp_path)
    package = ToolPackage.load(tmp_path / 'support')
    failure = {'calls': [{'tool': 'get_ticket', 'arguments': {'ticket_id': 'missing'}}],
               'expect_success': False, 'expect_changed': False}
    assert ToolGenerator._run_tests(package, 'get_ticket', [failure]) == ['missing_successful_test']
    success = {'calls': [{'tool': 'get_ticket', 'arguments': {'ticket_id': 'ticket-1'}}],
               'expect_success': True, 'expect_changed': False}
    assert ToolGenerator._run_tests(package, 'get_ticket', [success]) == ['missing_success_result_assertion']
    success['expected_data'] = 'unchecked'
    assert ToolGenerator._run_tests(package, 'get_ticket', [success]) == ['missing_success_result_assertion']
    success['expected_data'] = {'ticket_id': 'ticket-1', 'status': 'open'}
    assert ToolGenerator._run_tests(package, 'get_ticket', [success, failure]) == []
    success['expected_data']['status'] = 'resolved'
    assert 'test_0:returned_data_does_not_match_expectation' in ToolGenerator._run_tests(package, 'get_ticket', [success])


def test_write_checks_actual_record(tmp_path):
    fixtures.KimiMcpTests()._make_delivery(tmp_path)
    package = ToolPackage.load(tmp_path / 'support')
    test = {'calls': [{'tool': 'resolve_ticket', 'arguments': {'ticket_id': 'ticket-1'}}],
            'expect_success': True, 'expect_changed': True,
            'expected_records': [{'record_set_id': 'tickets', 'key': {'ticket_id': 'ticket-1'}, 'values': {'status': 'resolved'}}]}
    assert ToolGenerator._run_tests(package, 'resolve_ticket', [test]) == []
    test['expected_records'][0]['values']['status'] = 'open'
    assert 'test_0:record_does_not_match_expectation:tickets' in ToolGenerator._run_tests(package, 'resolve_ticket', [test])


def test_normalized_chain_preserves_record_assertions():
    from env_gen.tool_gen.compiler import _normalize_tests
    checks = [{'record_set_id': 'tickets', 'key': {'ticket_id': 'ticket-1'}, 'values': {'status': 'resolved'}}]
    normalized = _normalize_tests([{'tool': 'resolve_ticket', 'arguments': {'ticket_id': 'ticket-1'},
                                   'expect_changed': True, 'expected_records': checks}])
    assert normalized[0]['expected_records'] == checks


def test_container_plan_calls_image_preparation(tmp_path):
    import json
    generation = tmp_path / 'tool_generation'
    generation.mkdir()
    plan = {'python': '3.12', 'container': {'apt_packages': ['ngspice']}}
    (generation / 'software_plan.json').write_text(json.dumps(plan))
    generator = ToolGenerator(SimpleNamespace(), software_repair_attempts=0)
    with patch('env_gen.tool_gen.docker_runtime.build_image') as docker, patch('env_gen.tool_gen.software.prepare_software') as local:
        generator._prepare_software(tmp_path)
        docker.assert_called_once_with(tmp_path)
        local.assert_not_called()
