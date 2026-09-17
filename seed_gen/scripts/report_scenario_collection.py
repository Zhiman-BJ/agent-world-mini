"""Report all 160 source scenes; never confuse partial output with full coverage."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import subprocess

from seed_gen.scripts.build_joint_scenario_seeds import counts, file_sha, read, require
from seed_gen.scripts.select_python_ref_tools import _canonical_sha256

BASE = Path('seed_gen/scenario_collection')
OUT = Path('seed_gen/pypi_outputs/scenario_collection')


def main():
    inventory = read(BASE/'inventory.json')
    require(file_sha(inventory['classification_source']) == inventory['classification_sha256'], 'Inventory is stale')
    preserved = Path('seed_gen/pypi_outputs/semiconductor_scenario_02_0916.json')
    preserved_payload = read(preserved)
    require(len(preserved_payload) == 22, 'Unexpected existing 02 count')
    rows = []
    for scene in inventory['scenarios']:
        sid = scene['scenario_id']
        entry = {'scenario_id':sid,'name':scene['domain']['level3'],'status':'pending_research'}
        if sid.startswith('02.'):
            entry.update(status='preserved_existing_02', artifact=preserved.as_posix(), artifact_sha256=file_sha(preserved),
                         verification_scope='User stated previously completed; existing snapshot retained, not re-collected or promoted to new pilot runtime gate.')
        else:
            seed_path, report_path, profile_path = OUT/f'{sid}.json', OUT/f'reports/{sid}.json', BASE/f'profiles/{sid}.json'
            if seed_path.exists() and report_path.exists() and profile_path.exists():
                seed, report, profile = read(seed_path)[0], read(report_path), read(profile_path)
                require(counts(seed['init_ref_tools']) == seed['environment']['nums'] == report['nums'], f'Count mismatch: {sid}')
                require(report['profile_sha256'] == _canonical_sha256(profile), f'Profile drift: {sid}')
                require(report['static_verification'] == 'passed' and report['unexpected_tool_schema_errors'] == 0, f'Static failure: {sid}')
                require(all(r['reextraction'] == 'identical' for r in report['source_reextraction']), f'Source failure: {sid}')
                for evidence in profile['runtime_reports']:
                    require(file_sha(evidence['path']) == evidence['sha256'], f'Runtime drift: {sid}')
                entry.update(status='seed_and_fixed_tasks_verified' if report['pilot_task_gate_passed'] else 'seed_static_only',
                             artifact=seed_path.as_posix(), artifact_sha256=file_sha(seed_path), nums=report['nums'],
                             packages=seed['others']['pypi_package'], urls=len(seed['environment']['basic_info']['url']),
                             tasks=len(seed['init_ref_tasks']), runtime_reports=report['runtime_reports'])
        rows.append(entry)
    counts_by_l1 = {l1:dict(Counter(r['status'] for r in rows if r['scenario_id'].startswith(l1+'.'))) for l1 in inventory['counts_by_l1']}
    report = {'scope':inventory['scope'],'classification_sha256':inventory['classification_sha256'],
              'total_to_collect':inventory['to_collect'],'status_counts':dict(Counter(r['status'] for r in rows)),
              'counts_by_l1':counts_by_l1,'all_requested_scenes_complete':all(r['status']=='seed_and_fixed_tasks_verified' for r in rows if not r['scenario_id'].startswith('02.')),
              'scenarios':rows}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'coverage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='scenarios'},ensure_ascii=False))
    # Capture each independent fixture environment without mutating it. The
    # atomate2 phonons extra and phonopy 4 must not share a dependency lock.
    for name in ('materials', 'workflow', 'phonons', 'defects'):
        executable = Path(f'.venv-scenario-{name}/Scripts/python.exe')
        if executable.exists():
            freeze = subprocess.check_output(['C:/Apps/anaconda3/Scripts/uv.exe', 'pip', 'freeze', '--python', str(executable)], text=True, encoding='utf-8')
            (BASE/f'requirements-{name}.freeze.txt').write_text(freeze, encoding='utf-8')


if __name__=='__main__':
    main()
