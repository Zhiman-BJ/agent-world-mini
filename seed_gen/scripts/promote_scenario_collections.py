"""Promote only fully verified L1 worker profiles into the shared build input.

Workers keep their own sources, evidence, runtime reports and output directories.
This coordinator verifies a completed snapshot before copying its unchanged
profile; the normal shared builder then creates the canonical seeds and coverage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from seed_gen.scripts.build_joint_scenario_seeds import build_profile, file_sha, read, require
from seed_gen.scripts.select_python_ref_tools import _canonical_sha256

BASE = Path('seed_gen/scenario_collection')
OUT = Path('seed_gen/pypi_outputs/scenario_collection')
OWNERS = {'l1_design_lab': ('03', '04'), 'l1_test_fab': ('05', '06'),
          'l1_metrology_quality': ('07', '08'), 'materials_solver': ('01',),
          'lithography': ('03',), 'analog_bridge': ('03',)}


def reviewed_profile(lane, path):
    before = file_sha(path)
    profile = read(path)
    sid = profile['scenario_id']
    require(sid.split('.')[0] in OWNERS[lane], f'Scene outside lane ownership: {lane}/{sid}')
    if lane=='materials_solver':
        require(sid in ('01.07.01','01.07.02'), 'Scene outside solver assignment')
    if lane=='lithography':
        require(sid in ('03.12.01','03.12.02','03.12.03','03.12.04'), 'Scene outside lithography assignment')
    if lane=='analog_bridge':
        require(sid in ('03.11.01','03.11.04'), 'Scene outside analog bridge assignment')
    if lane=='l1_design_lab':
        require(not sid.startswith('03.12.') and sid not in ('03.11.01','03.11.04'),
                'Scene reassigned to a dedicated worker lane')
    require(path.name == sid+'.json', 'Profile filename does not match scene')
    output = OUT/lane/(sid+'.json')
    audit = OUT/lane/'reports'/(sid+'.json')
    require(output.exists() and audit.exists(), f'Worker output not ready: {sid}')
    saved_report = read(audit)
    require(saved_report['profile_sha256'] == _canonical_sha256(profile), f'Worker profile drift: {sid}')
    require(saved_report['static_verification'] == 'passed' and saved_report['pilot_task_gate_passed'],
            f'Worker has not passed source/static/task gates: {sid}')
    # Re-extract release source and validate all runtime/web hashes and API refs.
    payload, report = build_profile(profile)
    require(payload == read(output) and report == saved_report, f'Worker snapshot differs from regeneration: {sid}')
    require(file_sha(path) == before, f'Worker profile changed during review: {sid}')
    target = BASE/'profiles'/(sid+'.json')
    if target.exists():
        prior = read(target)
        require(Path(prior['research_file']).is_relative_to(BASE/lane), f'Another owner controls {sid}')
    return target, profile, {'scenario_id':sid, 'lane':lane, 'worker_profile':path.as_posix(),
                             'profile_sha256':_canonical_sha256(profile), 'nums':report['nums'],
                             'tasks':len(payload[0]['init_ref_tasks']), 'status':'verified_for_shared_build'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lane', choices=list(OWNERS), action='append')
    parser.add_argument('--scene', action='append', help='Promote only these completed scene IDs')
    args = parser.parse_args()
    requested = set(args.scene or [])
    prepared = []
    for lane in args.lane or OWNERS:
        for path in sorted((BASE/lane/'profiles').glob('*.json')):
            if requested and path.stem not in requested:
                continue
            # In-progress profiles are not silently treated as finished seeds.
            if not (OUT/lane/path.name).exists() or not (OUT/lane/'reports'/path.name).exists():
                continue
            prepared.append(reviewed_profile(lane,path))
    found = {profile['scenario_id'] for _,profile,_ in prepared}
    require(not requested-found, f'Requested scenes not ready: {sorted(requested-found)}')
    require(prepared, 'No completed worker snapshots ready for promotion')
    require(len(found)==len(prepared), 'Duplicate worker ownership')
    # All review completes before changing any coordinator input.
    for target,profile,evidence in prepared:
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(evidence,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
