"""Discover package identity and extract explicitly reviewed design/lab releases."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

import requests

from seed_gen.scripts.extract_release_python_seeds import build_seed, git

BASE = Path('seed_gen/scenario_collection/l1_design_lab')
RAW = Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_design_lab')
PACKAGES = ['gdsfactory', 'kfactory', 'klayout', 'gdstk', 'volare', 'hdl21', 'skidl', 'vlsirtools',
            'amaranth', 'pymtl3', 'pyrtl', 'myhdl', 'cocotb', 'pyuvm', 'cocotbext-axi', 'cocotb-bus',
            'vcdvcd', 'pyvcd', 'pyverilog', 'hdlConvertor', 'siliconcompiler', 'librelane', 'fusesoc',
            'edalize', 'pade', 'align', 'laygo2', 'lithosim', 'qcodes', 'pymeasure', 'laboneq',
            'zhinst-toolkit', 'pylablib', 'qcodes-contrib-drivers', 'RsInstrument', 'pyvisa', 'pyvisa-py', 'pyvisa-sim']


def discovery(name):
    url = f'https://pypi.org/pypi/{name}/json'
    r = requests.get(url, timeout=45)
    result = dict(query_name=name, url=url, status=r.status_code, checked_on='2026-09-17')
    if r.status_code == 200:
        data = r.json()
        result['info'] = {k: data['info'].get(k) for k in ['name', 'version', 'summary', 'project_urls', 'home_page', 'requires_python', 'requires_dist']}
        result['latest_files'] = [{k: f.get(k) for k in ['filename', 'upload_time_iso_8601', 'digests']} for f in data['urls']]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discover', action='store_true')
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    (BASE/'research').mkdir(parents=True, exist_ok=True)
    if args.discover:
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = list(pool.map(discovery, PACKAGES))
        (BASE/'research/package_discovery.json').write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        for r in records:
            print(r['query_name'], r['status'], r.get('info', {}).get('version'), r.get('info', {}).get('project_urls'))
    if args.manifest:
        specs = json.loads(args.manifest.read_text(encoding='utf-8'))
        RAW.mkdir(parents=True, exist_ok=True)
        for spec in specs:
            root = Path('seed_pypi_raw')/spec['directory']
            if not root.exists():
                root.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(['git', 'clone', '--depth', '1', '--branch', spec['tag'], spec['repository']+'.git', str(root)], check=True)
            if git(root, 'remote', 'get-url', 'origin').removesuffix('.git') != spec['repository']:
                raise ValueError('Remote mismatch: '+str(root))
            spec['commit'] = git(root, 'rev-parse', 'HEAD')
            result = build_seed(spec, Path('seed_pypi_raw'))
            destination = RAW/f'{spec["name"]}_{spec["tag"]}.json'
            destination.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            print(destination, result[0]['environment']['nums'])
        args.manifest.write_text(json.dumps(specs, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
