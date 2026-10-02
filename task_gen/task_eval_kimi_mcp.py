"""Kimi presentation adapter around the unchanged public environment MCP API."""
from copy import deepcopy
import json
from pathlib import Path
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from env_gen.tool_gen.mcp_protocol import serve_jsonrpc, tool_call_result
from task_gen.task_eval_mcp import TaskEvalMcpServer
from task_gen.tool_result_reader import ResultReader


_SCHEMA_MAP_CHILDREN = (
    '$defs', 'definitions', 'dependencies', 'dependentSchemas',
    'patternProperties', 'properties',
)
_SCHEMA_SINGLE_CHILDREN = (
    'additionalItems', 'additionalProperties', 'contains', 'contentSchema',
    'else', 'if', 'not', 'propertyNames', 'then', 'unevaluatedItems',
    'unevaluatedProperties',
)
_SCHEMA_ARRAY_CHILDREN = ('allOf', 'anyOf', 'oneOf', 'prefixItems')


def _json_schema_type(value):
    if value is None:
        return 'null'
    if type(value) is bool:
        return 'boolean'
    if type(value) is int:
        return 'integer'
    if type(value) is float:
        return 'number'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, dict):
        return 'object'
    return None


def _normalize_kimi_tool_schema(schema):
    """Return an equivalent schema accepted by Kimi Code's tool converter."""
    normalized = deepcopy(schema)

    def visit(node):
        if not isinstance(node, dict):
            return
        enum = node.get('enum')
        if isinstance(enum, list) and enum:
            groups = {}
            for value in enum:
                value_type = _json_schema_type(value)
                if value_type is None:
                    continue
                groups.setdefault(value_type, []).append(value)
            # Kimi treats integer + number as the single JSON Schema number type.
            if 'number' in groups and 'integer' in groups:
                groups['number'] = groups.pop('integer') + groups['number']
            if len(groups) > 1:
                branches = [
                    {'type': value_type, 'enum': values}
                    for value_type, values in groups.items()
                ]
                node.pop('enum')
                # The enum already fixes every accepted value, so a matching
                # parent type is redundant. Each branch carries its own type.
                node.pop('type', None)
                if any(key in node for key in ('anyOf', 'oneOf')):
                    node.setdefault('allOf', []).append({'anyOf': branches})
                else:
                    node['anyOf'] = branches
        for key in _SCHEMA_MAP_CHILDREN:
            children = node.get(key)
            if isinstance(children, dict):
                for child in children.values():
                    visit(child)
        for key in _SCHEMA_SINGLE_CHILDREN:
            visit(node.get(key))
        for key in _SCHEMA_ARRAY_CHILDREN:
            children = node.get(key)
            if isinstance(children, list):
                for child in children:
                    visit(child)
        items = node.get('items')
        if isinstance(items, list):
            for child in items:
                visit(child)
        else:
            visit(items)

    visit(normalized)
    return normalized


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
                tool['inputSchema'] = _normalize_kimi_tool_schema(tool['inputSchema'])
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
