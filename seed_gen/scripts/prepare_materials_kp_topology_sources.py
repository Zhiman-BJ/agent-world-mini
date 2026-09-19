"""Pin kdotpy tag and verify untaged Z2Pack release against its PyPI archive."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import requests

from seed_gen.scripts.build_joint_scenario_seeds import read, require, file_sha
from seed_gen.scripts.extract_release_python_seeds import build_seed, git

BASE = Path('seed_gen/scenario_collection')


def z2pack_release():
    info = requests.get('https://pypi.org/pypi/z2pack/2.2.1/json', timeout=30)
    info.raise_for_status()
    data = info.json()
    source = next(f for f in data['urls'] if f['packagetype']=='sdist')
    response = requests.get(source['url'], timeout=60)
    response.raise_for_status()
    require(hashlib.sha256(response.content).hexdigest()==source['digests']['sha256'], 'PyPI archive hash mismatch')
    archive_path = BASE/'research/z2pack-2.2.1.tar.gz'
    archive_path.write_bytes(response.content)
    root = Path('seed_pypi_raw/z2pack')
    commit = 'a82c83afbdac8e43a593c22c06de19cfe752d354'
    require(git(root, 'rev-parse', 'HEAD')==commit, 'Unexpected Z2Pack commit')
    archived = {}
    with tarfile.open(fileobj=io.BytesIO(response.content), mode='r:gz') as archive:
        for member in archive.getmembers():
            if member.isfile() and '/src/z2pack/' in member.name and member.name.endswith('.py'):
                path = member.name.split('/', 1)[1]
                archived[path] = archive.extractfile(member).read()
    git_files = set(git(root, 'ls-tree', '-r', '--name-only', 'HEAD', '--', 'src/z2pack').splitlines())
    git_files = {p for p in git_files if p.endswith('.py')}
    require(set(archived)==git_files, 'Archive/Git package file set mismatch')
    require(archived, 'Empty archive package')
    for path, content in archived.items():
        blob = subprocess.check_output(['git', '-C', str(root), 'show', commit+':'+path])
        require(blob==content, 'Archive differs from Git source: '+path)
    return data, {'archive': archive_path.as_posix(), 'sha256': source['digests']['sha256'],
                  'url': source['url'], 'commit': commit, 'equal_python_files': sorted(archived),
                  'status': 'all_release_python_files_byte_identical',
                  'checkout_note': 'Windows rejects test filenames containing <lambda>; cone sparse checkout includes src/doc/examples and root files. One-command core.protectNTFS=false allowed index population; no source files changed. Tests outside sparse cone are not part of API extraction.'}


def main():
    metadata, equivalence = z2pack_release()
    specs = []
    definitions = [
        ('kdotpy', 'v1.4.1', '771ffd704a611e2b15e32f7d1b62eda1bb7ae0d2',
         'https://git.physik.uni-wuerzburg.de/kdotpy/kdotpy', 'https://kdotpy.physik.uni-wuerzburg.de',
         '2026-06-26', 'kdotpy实现Kane多带k·p模型，计算闪锌矿半导体的体材料、量子阱和条带能带，支持材料参数、应变、磁场与光学观察量；多数高层用法为命令行，Python参考入口需明确状态与单位。'),
        ('z2pack', '2.2.1', equivalence['commit'], 'https://github.com/Z2PackDev/Z2Pack', 'https://z2pack.greschd.ch',
         min(f['upload_time_iso_8601'] for f in metadata['urls']),
         'Z2Pack沿闭合k空间回路计算Wannier电荷中心和Wilson环，支持自适应收敛及Chern/Z2不变量，可使用显式哈密顿量或TBmodels模型作为输入。'),
    ]
    for index, (name, tag, commit, repository, docs, date, description) in enumerate(definitions, 141):
        root = Path('seed_pypi_raw')/name
        require(git(root, 'remote', 'get-url', 'origin').removesuffix('.git')==repository, 'Repository mismatch')
        notes = ['完整发布Python静态候选；运行任务、继承/动态入口另行核查。']
        if name=='z2pack':
            notes += ['PyPI2.2.1无相同Git tag；不是以默认分支冒充发布版。所有发布Python文件与固定提交逐字节一致，核查证据见research/z2pack_source_equivalence.json。', equivalence['checkout_note']]
        spec = dict(name=name, directory=name, index=index, source_root='src', modules=[name],
                    repository=repository, documentation=docs, pypi=f'https://pypi.org/project/{name}/',
                    pypi_version=tag.removeprefix('v'), tag=tag, commit=commit, release_published_at=date,
                    checked_on='2026-09-17', github_prerelease=None, notes=notes, description=description,
                    release_api=('https://pypi.org/pypi/z2pack/2.2.1/json' if name=='z2pack' else repository+'/-/tags/v1.4.1'),
                    release_url=('https://pypi.org/project/z2pack/2.2.1/' if name=='z2pack' else repository+'/-/tags/v1.4.1'),
                    selection_rule=('Latest stable PyPI2.2.1, exact Git source equivalence; no matching release tag.' if name=='z2pack' else 'Official latest stable tag v1.4.1 agrees with PyPI1.4.1; date from official tag page.'))
        if name=='z2pack':
            spec['ref']=commit
        payload = build_seed(spec, Path('seed_pypi_raw'))
        path = Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection')/f'{name}_{tag}.json'
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        specs.append(spec)
        print(name, payload[0]['environment']['nums'], flush=True)
    (BASE/'materials_kp_topology_sources.json').write_text(json.dumps(specs, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (BASE/'research/z2pack_source_equivalence.json').write_text(json.dumps(equivalence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
