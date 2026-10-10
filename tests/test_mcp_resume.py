import json
from pathlib import Path
from unittest.mock import patch

import pytest

from env_gen.tool_gen.delivery_contract import load_delivery
from env_gen.tool_gen.mcp_server import ToolMcpServer
from tests import test_kimi_mcp as fixtures


def test_mcp_restart_preserves_task_changes(tmp_path):
    binding = fixtures.KimiMcpTests()._make_delivery(tmp_path, with_report_tool=True)
    delivery = load_delivery(binding)
    session = tmp_path / 'run/sandbox'
    trace = tmp_path / 'run/calls.jsonl'
    with ToolMcpServer(delivery, trace_path=trace, session_root=session) as server:
        response = server.handle({'method': 'tools/call', 'params': {
            'name': 'resolve_ticket', 'arguments': {'ticket_id': 'ticket-1'}}})
        assert not response['isError']
    with ToolMcpServer(delivery, trace_path=trace, session_root=session) as server:
        with patch.object(server.runtime, 'snapshot', side_effect=AssertionError('read snapshot')):
            response = server.handle({'method': 'tools/call', 'params': {
                'name': 'get_ticket', 'arguments': {'ticket_id': 'ticket-1'}}})
        assert response['structuredContent']['data']['status'] == 'resolved'
        assert server.calls == 2
    assert json.loads((session / 'session.json').read_text())['tool_calls'] == 2
    assert [json.loads(line)['sequence'] for line in trace.read_text().splitlines()] == [1, 2]


def test_mcp_rejects_unrelated_existing_session(tmp_path):
    binding = fixtures.KimiMcpTests()._make_delivery(tmp_path)
    delivery = load_delivery(binding)
    session = tmp_path / 'sandbox'
    with ToolMcpServer(delivery, session_root=session):
        pass
    path = session / 'environment.json'
    environment = json.loads(path.read_text())
    environment['environment_id'] = 'another_environment'
    path.write_text(json.dumps(environment))
    with pytest.raises(ValueError, match='不同的环境'):
        ToolMcpServer(delivery, session_root=session)
