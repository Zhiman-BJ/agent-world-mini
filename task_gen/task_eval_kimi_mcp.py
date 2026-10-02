"""Kimi presentation adapter around the unchanged public environment MCP API."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from env_gen.tool_gen.mcp_protocol import serve_jsonrpc
from task_gen.task_eval_mcp import TaskEvalMcpServer


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


class KimiMcpAdapter:
    """Adapt schemas for Kimi without changing MCP tool semantics.

    Long results are returned unchanged. Kimi Code owns externalization into
    its session-local ``tool-results`` files and can recover them with native
    Read/Grep.
    """

    def __init__(self, server):
        self.server = server

    def handle(self, request):
        method = request.get('method')
        result = self.server.handle(request)
        if method == 'tools/list':
            result = {'tools': [dict(tool) for tool in result['tools']]}
            for tool in result['tools']:
                tool['inputSchema'] = _normalize_kimi_tool_schema(tool['inputSchema'])
                schema = tool.pop('outputSchema', None)
                if schema is not None:
                    tool['description'] += '\nOutput contract: ' + json.dumps(schema, ensure_ascii=False)
        return result


def _arguments(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path, nargs='?')
    parser.add_argument('--binding', type=Path)
    parser.add_argument('--state-root', type=Path)
    parser.add_argument('--trace', type=Path)
    parser.add_argument('--max-tool-calls', type=int)
    parser.add_argument('--timeout', type=int)
    parser.add_argument('--memory-limit', type=int)
    parser.add_argument('--write-limit', type=int)
    parser.add_argument('--process-limit', type=int, default=1024)
    parser.add_argument('--review-choice-seed', type=Path)
    options = parser.parse_args(argv)
    if options.config is not None:
        if any(
            value is not None
            for value in (
                options.binding,
                options.state_root,
                options.trace,
                options.max_tool_calls,
                options.timeout,
                options.memory_limit,
                options.write_limit,
                options.review_choice_seed,
            )
        ):
            parser.error('config positional argument cannot be combined with binding options')
        return options
    required = {
        '--binding': options.binding,
        '--state-root': options.state_root,
        '--trace': options.trace,
        '--max-tool-calls': options.max_tool_calls,
        '--timeout': options.timeout,
        '--memory-limit': options.memory_limit,
        '--write-limit': options.write_limit,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error('missing required arguments: ' + ', '.join(missing))
    for name in ('max_tool_calls', 'timeout', 'memory_limit', 'write_limit', 'process_limit'):
        if getattr(options, name) < 1:
            parser.error('--' + name.replace('_', '-') + ' must be positive')
    return options


def _server_config(options):
    if options.config is not None:
        return json.loads(options.config.read_text(encoding='utf-8'))
    config = {
        'binding_path': str(options.binding),
        'state_root': str(options.state_root),
        'trace': str(options.trace),
        'max_tool_calls': options.max_tool_calls,
        'timeout': options.timeout,
        'memory_limit': options.memory_limit,
        'write_limit': options.write_limit,
        'process_limit': options.process_limit,
    }
    if options.review_choice_seed is not None:
        config['review_choice_seed'] = json.loads(
            options.review_choice_seed.read_text(encoding='utf-8')
        )
    return config


def main(argv=None):
    config = _server_config(_arguments(argv))
    adapter = KimiMcpAdapter(TaskEvalMcpServer(config))
    serve_jsonrpc(adapter.handle)


if __name__ == '__main__':
    main()
