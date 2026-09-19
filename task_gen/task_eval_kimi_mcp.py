"""Kimi presentation adapter around the unchanged public environment MCP API."""
import json
from pathlib import Path
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from env_gen.tool_gen.mcp_protocol import serve_jsonrpc, tool_call_result
from task_gen.task_eval_mcp import TaskEvalMcpServer
from task_gen.tool_result_reader import ResultReader


class KimiResultAdapter:
    def __init__(self, server, config):
        self.server = server
        self.reader = ResultReader(config['tool_result_page_chars'])
        self.trace = Path(config['result_read_trace'])
        self.result_index = self.trace.with_name('result_index.jsonl')
        if any(t['name'] == self.reader.name for t in server.handle({'method': 'tools/list'})['tools']):
            raise ValueError('read_tool_result 工具名称冲突')

    def handle(self, request):
        method = request.get('method')
        params = request.get('params')
        if method == 'tools/call' and isinstance(params, dict) and params.get('name') == self.reader.name:
            arguments = params.get('arguments', {})
            try:
                if not isinstance(arguments, dict):
                    raise ValueError('工具 arguments 必须是 object')
                payload, error = self.reader.read(arguments), None
            except ValueError as caught:
                payload, error = None, str(caught)
            with self.trace.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'tool': self.reader.name, 'arguments': arguments,
                                         'result': payload, 'error': error}, ensure_ascii=False) + '\n')
            return tool_call_result(payload if error is None else {'error': error}, is_error=error is not None)
        result = self.server.handle(request)
        if method == 'tools/list':
            # Only the Kimi-facing presentation has page envelopes. The public
            # environment server and task definitions keep their original schema.
            # Execution validates original outputSchema before this adapter runs.
            result = {'tools': [dict(tool) for tool in result['tools']]}
            for tool in result['tools']:
                schema = tool.pop('outputSchema', None)
                if schema is not None:
                    tool['description'] += '\nOutput contract: ' + json.dumps(schema, ensure_ascii=False)
            result['tools'].append(self.reader.tool)
        elif method == 'tools/call':
            payload = result['structuredContent']
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            if len(raw) > self.reader.page_chars:
                page = self.reader.preview(raw)
                with self.result_index.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'result_id': page['result_id'], 'tool': params['name'],
                                             'sequence': self.server.calls,
                                             'total_chars': len(raw)}) + '\n')
                result = tool_call_result(page, is_error=result.get('isError', False))
        return result


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    adapter = KimiResultAdapter(TaskEvalMcpServer(config), config)
    serve_jsonrpc(adapter.handle)


if __name__ == '__main__':
    main()
