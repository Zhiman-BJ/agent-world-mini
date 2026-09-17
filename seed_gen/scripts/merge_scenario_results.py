"""Merge published L1 partitions and export L3 files without rerunning research.

This checks layout/content consistency only. Existing source and runtime
verification claims are preserved, including the older 02 snapshot.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from seed_gen.scripts.build_joint_scenario_seeds import counts, read, require

DEFAULT_OUTPUT = Path('seed_gen/pypi_outputs/final_results')


def collect_partitions(directory):
    seeds, seen, global_ids = [], set(), set()
    paths = sorted(directory.glob('semiconductor_scenario_[0-9][0-9].json'))
    require(paths, 'No L1 partitions found')
    for path in paths:
        l1 = path.stem[-2:]
        payload = read(path)
        require(isinstance(payload, list) and payload, f'Empty/invalid partition: {path}')
        for seed in payload:
            sid = seed['environment']['domain']['level3'].split()[0]
            require(re.fullmatch(r'\d{2}\.\d{2}\.\d{2}', sid), f'Invalid L3 ID: {sid}')
            require(sid.startswith(l1 + '.'), f'Scene in wrong L1 partition: {sid}')
            require(sid not in seen, f'Duplicate L3 ID: {sid}')
            require(seed['global_id'] not in global_ids, f'Duplicate global ID: {seed["global_id"]}')
            require(counts(seed['init_ref_tools']) == seed['environment']['nums'], f'Count mismatch: {sid}')
            require(all(isinstance(t, str) for t in seed['init_ref_tasks']), f'Non-string task: {sid}')
            seen.add(sid)
            global_ids.add(seed['global_id'])
            seeds.append(seed)
    return sorted(seeds, key=lambda s: s['environment']['domain']['level3'].split()[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    seeds = collect_partitions(args.output_dir)
    outputs = {args.output_dir/'semiconductor_scenario_collection.json': seeds}
    for seed in seeds:
        sid = seed['environment']['domain']['level3'].split()[0]
        outputs[args.output_dir/'l3'/f'{sid}.json'] = [seed]
    # Never silently leave removed/renamed scenes in a published L3 directory.
    extra = set((args.output_dir/'l3').glob('*.json')) - set(outputs)
    require(not extra, f'Unexpected L3 files; review before removing: {sorted(extra)}')
    # Verify every existing L3 before any write. A divergent local edit is not
    # overwritten merely because a partition happens to contain the same ID.
    for path, payload in outputs.items():
        if path.parent.name == 'l3' and path.exists():
            require(read(path) == payload, f'L3 differs from partition: {path}')
        if args.check:
            require(path.exists() and read(path) == payload, f'Artifact drift: {path}')
    if not args.check:
        for path, payload in outputs.items():
            if path.exists() and read(path) == payload:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(f'{"Verified" if args.check else "Merged"} {len(seeds)} scenes from L1 partitions; L3 files: {len(seeds)}.')


if __name__ == '__main__':
    main()
