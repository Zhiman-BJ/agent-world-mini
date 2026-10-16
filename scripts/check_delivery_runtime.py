"""Replay passed upstream test cases through TaskGen's isolated tool executor."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from env_gen.tool_gen.kimi_mcp import load_delivery
from task_gen.task_eval_mcp import call_environment_tool
from task_gen.tool_graph.step_3_chain_execute import _workspace_signature


def check(binding, names, timeout=60):
    delivery = load_delivery(binding)
    tools = {t['name']: t for t in delivery.package.tools}
    unknown = set(names) - tools.keys()
    if unknown:
        raise ValueError(f'Unknown tools: {sorted(unknown)}')
    receipt = json.loads((delivery.delivery_root / delivery.binding['tool_validation_path']).read_text())
    source = delivery.package.package_root / 'state'
    before = _workspace_signature(source)
    records = []
    for report in receipt['reports']:
        name = report.get('tool')
        if name not in names or report.get('status') != 'passed':
            continue
        test = next((t for t in report.get('tests', []) if t.get('expect_success') is True), None)
        if not test:
            records.append({'tool': name, 'passed': False, 'error': 'No positive upstream test'})
            continue
        with tempfile.TemporaryDirectory(prefix='delivery-probe-') as temporary:
            state = Path(temporary) / 'state'
            shutil.copytree(source, state)
            started = time.monotonic()
            calls = []
            for call in test['calls']:
                calls.append(call_environment_tool(call['tool'], call['arguments'], tools, state,
                    environment=delivery.package.environment, timeout=timeout,
                    memory_limit=2 * 1024**3, write_limit=256 * 1024**2,
                    software={'root': str(delivery.software_root), 'python': str(delivery.python_path)}
                             if delivery.software_root else None))
                if calls[-1]['error']:
                    break
            passed = bool(calls) and all(c['error'] is None for c in calls)
            records.append({'tool': name, 'passed': passed, 'calls': calls,
                            'duration_seconds': round(time.monotonic() - started, 3)})
            print(name, 'PASS' if passed else calls[-1]['error'] if calls else 'EMPTY', flush=True)
    assert _workspace_signature(source) == before, 'Original initial state changed'
    return {'binding': str(binding.resolve()), 'results': records,
            'passed': len(records) == len(set(names)) and all(r['passed'] for r in records)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binding', type=Path)
    parser.add_argument('--tools', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=60)
    args = parser.parse_args()
    report = check(args.binding, args.tools, args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report['passed'] else 1)
