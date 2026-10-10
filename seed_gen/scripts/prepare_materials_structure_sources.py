"""Bind reviewed release evidence to clean sources and new complete raw indexes."""
from __future__ import annotations
import copy
import json
from pathlib import Path

from seed_gen.scripts.extract_release_python_seeds import build_seed, git
from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require

BASE = Path('seed_gen/scenario_collection')
MANIFEST = BASE / 'materials_structure_sources.json'
RAW = Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection')


def main():
    evidence = {r['url']: r for path in sorted((BASE / 'research').glob('*web/index.json')) for r in read(path)}
    records = []
    old = {s['name']: s for s in read('seed_gen/pypi_materials_sources.json')}
    specs = [
        {'name': 'pymatgen-core', 'index': 1, 'directory': 'pymatgen-core', 'source_root': 'src', 'modules': ['pymatgen'],
         'repository': 'https://github.com/materialsproject/pymatgen-core', 'documentation': 'https://pymatgen.org/',
         'description': 'pymatgen-core提供晶体、晶格、组成、对称性、文件转换及材料计算结果分析的基础数据结构和方法，作为当前材料场景的统一结构状态。',
         'tag': 'v2026.8.30'},
        copy.deepcopy(old['ase']), copy.deepcopy(old['spglib']),
        {'name': 'jarvis-tools', 'index': 50, 'directory': 'jarvis-tools', 'modules': ['jarvis'],
         'repository': 'https://github.com/atomgptlab/jarvis', 'documentation': 'https://jarvis-tools.readthedocs.io/en/master/',
         'description': 'JARVIS-Tools提供原子结构、材料描述符、数据库和多种电子结构计算工具接口，用于数据驱动材料设计。独立Atoms对象需转换后才能与pymatgen状态共用。',
         'tag': 'v2026.4.2'},
        copy.deepcopy(old['matminer']),
        {'name': 'seekpath', 'index': 51, 'directory': 'seekpath', 'modules': ['seekpath'],
         'repository': 'https://github.com/materialscloud-org/seekpath', 'documentation': 'https://seekpath.readthedocs.io/en/latest/',
         'description': 'SeeK-path基于spglib识别晶体对称性，按HPKOT约定给出标准原胞、倒易晶格和高对称k路径，并支持按指定间距生成显式路径点。它不计算电子能带。',
         'tag': 'v2.2.1'},
    ]
    for spec in specs:
        name = spec['name']
        root = Path('seed_pypi_raw') / spec['directory']
        meta_record = evidence[f'https://pypi.org/pypi/{name}/json']
        meta = read(meta_record['json_file'])
        spec.update(pypi=f'https://pypi.org/project/{name}/', pypi_version=meta['info']['version'], checked_on='2026-09-17')
        if name != 'ase':
            release_api = spec['repository'].replace('https://github.com/', 'https://api.github.com/repos/') + '/releases/latest'
            api_record = evidence[release_api]
            require(api_record['status'] == 200, f'Release API not retrieved: {name}')
            release = read(api_record['json_file'])
            require(release['tag_name'] == spec['tag'] and not release['prerelease'] and not release['draft'], f'Release not reviewed: {name}')
            spec.update(release_api=release_api, release_url=release['html_url'],
                        release_published_at=release['published_at'], github_prerelease=False)
        else:
            require(meta['info']['version'] == spec['tag'], 'ASE PyPI/tag changed')
            remote = git(root, 'ls-remote', '--tags', 'origin', f'refs/tags/{spec["tag"]}^{{}}')
            require(remote.split()[0] == spec['commit'], 'ASE official remote tag changed')
        commit = git(root, 'rev-parse', 'HEAD')
        require(git(root, 'rev-parse', spec['tag'] + '^{commit}') == commit, 'Wrong checkout tag')
        require(not git(root, 'status', '--porcelain'), 'Source checkout is dirty')
        remote = git(root, 'remote', 'get-url', 'origin')
        require(remote.removesuffix('.git') == spec['repository'], f'Wrong official remote: {name}')
        spec['commit'] = commit
        spec['notes'] = [
            '2026-09-17核对官方PyPI身份、当前发布源及本地remote/tag/HEAD/工作区。',
            '新全量索引保存在独立scenario_collection子目录，保留历史包和02场景产物。',
        ]
        if name == 'jarvis-tools':
            spec['notes'].append('PyPI主页指向atomgptlab/jarvis-tools并重定向atomgptlab/jarvis；原usnistgov/jarvis不是当前PyPI指向的发布源。按指南优先最新GitHub非预发布Release v2026.4.2；PyPI当前2026.6.12，版本差异保留，不将默认分支当Release。')
        if name == 'ase':
            spec['notes'].append('官方GitLab标签与PyPI版本3.29.0一致；多个旧文档链接404，保留失败HTTP记录，桥接依据固定源码及实际运行。')
        records.append({'package': name, 'remote': remote, 'commit': commit, 'tag': spec['tag'],
                        'clean_status': git(root, 'status', '--porcelain'), 'submodules': git(root, 'submodule', 'status').splitlines(),
                        'pypi_evidence': meta_record, 'pypi_json_sha256': file_sha(meta_record['json_file']),
                        'official_release_evidence': evidence.get(spec.get('release_api', ''))})
    RAW.mkdir(parents=True, exist_ok=True)
    for spec, record in zip(specs, records):
        payload = build_seed(spec, Path('seed_pypi_raw'))
        filename = f'{spec["name"]}_{spec["tag"]}.json'
        historical = RAW.parent / filename
        if historical.exists():
            record['legacy_tools_identical'] = read(historical)[0]['init_ref_tools'] == payload[0]['init_ref_tools']
        path = RAW / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        record.update(raw_path=path.as_posix(), nums=payload[0]['environment']['nums'])
        print(spec['name'], payload[0]['environment']['nums'], 'legacy tools identical:', record.get('legacy_tools_identical'))
    MANIFEST.write_text(json.dumps(specs, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (BASE / 'research/materials_source_checks.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
