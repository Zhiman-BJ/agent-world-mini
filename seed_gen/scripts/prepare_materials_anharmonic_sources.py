"""Bind hiPhive's official GitLab release and full source API index."""
import json
from pathlib import Path
from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require
from seed_gen.scripts.extract_release_python_seeds import build_seed, git


def main():
    base = Path('seed_gen/scenario_collection')
    web = read(base/'research/materials_anharmonic_web/index.json')
    release = read(web[0]['json_file'])[0]
    require(release['tag_name'] == '1.5' and not release.get('upcoming_release'), 'Review latest release')
    root = Path('seed_pypi_raw/hiphive')
    commit = git(root, 'rev-parse', 'HEAD')
    require(commit == '1c4dbc710081e9128e657ffa2d64f67abe71d078' == git(root, 'rev-parse', '1.5^{commit}'), 'Wrong release checkout')
    require(not git(root, 'status', '--porcelain', '--untracked-files=no'), 'Dirty release source')
    require(git(root, 'remote', 'get-url', 'origin') == 'https://gitlab.com/materials-modeling/hiphive.git', 'Wrong upstream')
    discovery = next(r for r in read(base/'research/materials_package_discovery.json') if r['query_name'] == 'hiphive')
    require(discovery['info']['version'] == '1.5', 'PyPI changed')
    spec = {'name': 'hiphive', 'index': 24, 'tag': '1.5', 'commit': commit, 'directory': 'hiphive', 'modules': ['hiphive'],
            'repository': 'https://gitlab.com/materials-modeling/hiphive', 'documentation': 'https://hiphive.materialsmodeling.org/',
            'pypi': 'https://pypi.org/project/hiphive/', 'pypi_version': '1.5', 'checked_on': '2026-09-17', 'github_prerelease': None,
            'release_api': web[0]['url'], 'release_url': release['_links']['self'], 'release_published_at': release['released_at'],
            'selection_rule': 'Latest official GitLab release1.5 agrees with current PyPI stable version; annotated tag peeled and remote verified.',
            'description': 'hiPhive 使用晶体对称性和高阶簇展开从原子位移与力数据拟合力常数，提供训练结构容器、力常数势、ASE计算器及phonopy/phono3py格式转换，可用于晶格动力学和非谐模型构建。',
            'notes': ['本场景候选角色为力常数拟合；若已有发布示例力数据，优先phono3py直接生成，避免额外模型拟合链。', 'ShakeNBreak内部rattle使用hiphive，但这不等于本场景已验证力常数拟合。']}
    raw = Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/hiphive_1.5.json')
    payload = build_seed(spec, Path('seed_pypi_raw'))
    raw.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (base/'materials_anharmonic_sources.json').write_text(json.dumps([spec], ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    check = {'package': 'hiphive', 'commit': commit, 'release_evidence': web[0], 'raw_path': raw.as_posix(), 'raw_sha256': file_sha(raw),
             'remote': git(root, 'remote', 'get-url', 'origin'), 'tracked_status': git(root, 'status', '--porcelain', '--untracked-files=no'),
             'submodules': git(root, 'submodule', 'status').splitlines(), 'nums': payload[0]['environment']['nums']}
    (base/'research/materials_anharmonic_source_checks.json').write_text(json.dumps(check, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(check['nums'])


if __name__ == '__main__':
    main()
