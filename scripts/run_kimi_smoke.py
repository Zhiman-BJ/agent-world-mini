"""Live Kimi/MCP smoke test: discover an unknown value/token, then update state."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_gen.task_eval import _run_agent
from task_gen.tool_graph.run_io import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config/task_eval_kimi.yaml'))
    parser.add_argument('--model')
    parser.add_argument('--output-root', type=Path, default=Path('runs/kimi_smoke'))
    args = parser.parse_args()
    root = args.output_root.resolve() / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    state = root / 'state'
    state.mkdir(parents=True)
    initial = {'count': 100 + secrets.randbelow(900), 'token': secrets.token_hex(12)}
    (state / 'counter.json').write_text(json.dumps(initial))
    tools = [{
        'name': 'read_counter', 'description': 'Read the current count and update token for the sample counter.',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'outputSchema': {'type': 'object'},
        'internal': {'code': "def run(arguments, context):\n import json\n return {'success': True, **json.loads((context.workspace_root / 'counter.json').read_text())}"},
    }, {
        'name': 'set_counter', 'description': 'Set the sample counter. Requires its current update token, returned by read_counter.',
        'inputSchema': {'type': 'object', 'properties': {'count': {'type': 'integer'}, 'token': {'type': 'string'}},
                        'required': ['count', 'token'], 'additionalProperties': False},
        'outputSchema': {'type': 'object'},
        'internal': {'code': "def run(arguments, context):\n import json\n p = context.workspace_root / 'counter.json'\n data = json.loads(p.read_text())\n if arguments['token'] != data['token']: return {'success': False, 'error': 'Invalid token'}\n data['count'] = arguments['count']\n p.write_text(json.dumps(data))\n return {'success': True, 'count': data['count']}"},
    }]
    server = root / 'server.json'
    trace = root / 'calls.jsonl'
    server.write_text(json.dumps({'tools': tools, 'environment': {}, 'workspace': str(state),
        'trace': str(trace), 'max_tool_calls': 6, 'timeout': 10,
        'memory_limit': 2147483648, 'write_limit': 268435456}))
    config = load_config(args.config, {'model': args.model})
    answer = _run_agent('把示例计数器在当前值基础上增加 7，确认保存后的值，并告诉我原值和新值。',
                        state, server, trace, {**config.llm, 'agent_backend': 'kimi'})
    final = json.loads((state / 'counter.json').read_text())
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    passed = (final['count'] == initial['count'] + 7 and final['token'] == initial['token']
              and str(initial['count']) in answer and str(final['count']) in answer
              and all(call['error'] is None for call in calls)
              and [call['tool'] for call in calls].count('read_counter') >= 2)
    report = {'passed': passed, 'model': config.llm['model'], 'initial_count': initial['count'],
              'final_count': final['count'], 'answer': answer, 'tool_calls': len(calls)}
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'run_dir': str(root), **report}, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit('Kimi live smoke failed; see report and agent logs')


if __name__ == '__main__':
    main()
