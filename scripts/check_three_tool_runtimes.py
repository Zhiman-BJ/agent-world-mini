"""Replay one previous runtime failure per environment on a disposable state."""
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from task_gen.task_eval_mcp import call_environment_tool


def main():
    results = []
    for name in sys.argv[1:] or ['pypi_atomate2_5', 'pypi_doped_6', 'pypi_pymatgen_core_4']:
        root = next((ROOT / 'runs/three_search_trial_20260917' / name).glob('*/run.json')).parent
        found = False
        for trace in sorted((root / 'tasks').glob('*/tool_calls.jsonl')):
            for line in trace.read_text().splitlines():
                call = json.loads(line)
                if not any(s in str(call.get('error')) for s in ['ModuleNotFoundError', 'OpenBLAS']):
                    continue
                server = json.loads((trace.parent / 'server.json').read_text())
                with tempfile.TemporaryDirectory() as temporary:
                    state = Path(temporary) / 'state'
                    shutil.copytree(trace.parent / 'initial', state)
                    outcome = call_environment_tool(
                        call['tool'], call['arguments'], {t['name']: t for t in server['tools']}, state,
                        timeout=300, memory_limit=2 * 1024**3, write_limit=256 * 1024**2,
                        environment=server['environment'],
                    )
                row = {'environment': name, 'source': str(trace), 'old_error': call['error'], 'outcome': outcome}
                results.append(row)
                print(name, call['tool'], outcome.get('error'), flush=True)
                found = True
                break
            if found:
                break
        assert found, name
    output = ROOT / ('runs/runtime_preflight_' + '_'.join(r['environment'] for r in results) + '.json')
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    assert all(not r['outcome'].get('error') for r in results), output


if __name__ == '__main__':
    main()
