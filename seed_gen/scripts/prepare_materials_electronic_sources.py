"""Collect reviewed release pools for diffusion and electronic postprocessing."""
import json
from pathlib import Path
from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require
from seed_gen.scripts.extract_release_python_seeds import build_seed, git

BASE = Path('seed_gen/scenario_collection')


def main():
    web = {r['url']: r for r in read(BASE/'research/materials_electronic_web/index.json')}
    discovery = {r['query_name']: r for r in read(BASE/'research/materials_package_discovery.json')}
    definitions = [
        ('pymatgen-analysis-diffusion', 'materialsvirtuallab/pymatgen-analysis-diffusion', 'v2025.11.14', 'src', ['pymatgen.analysis.diffusion'],
         'https://materialsvirtuallab.github.io/pymatgen-analysis-diffusion/',
         'pymatgen-analysis-diffusion基于原子轨迹分析均方位移、扩散系数、Arrhenius关系、概率密度与迁移路径，提供AIMD后处理和NEB路径构造工具。输入轨迹及时间单位须正确，不能以短样例替代充分采样的扩散计算。'),
        ('sumo', 'SMTG-Bham/sumo', 'v3.0.0', '.', ['sumo'], 'https://smtg-bham.github.io/sumo/',
         'sumo是固体电子结构后处理工具，提供能带、态密度、带边与有效质量分析和绘图，并支持多种第一性原理输出格式；它本身不运行DFT求解。'),
        ('pyprocar', 'romerogroup/pyprocar', 'v6.5.0', '.', ['pyprocar'], 'https://pyprocar.readthedocs.io/en/latest/',
         'PyProcar提供电子能带、投影态密度、自旋纹理和费米面等分析/可视化接口，支持多种电子结构程序输出；处理对象依赖外部计算结果与正确的倒易晶格约定。'),
        ('effmass', 'lucydot/effmass', 'v2.3.0', '.', ['effmass'], 'https://effmass.readthedocs.io/en/latest/',
         'effmass从半导体电子能带片段定位极值并拟合色散，计算曲率、输运及光学有效质量与非抛物性指标。它提供数据适配、片段生成、拟合和诊断接口，结果依赖k路径单位、采样及拟合区间。'),
        ('openbandparams', 'duarte-jfs/openbandparams', 'v1.0', 'src', ['openbandparams'], 'https://duarte-jfs.github.io/openbandparams/',
         'openbandparams为III-V半导体及其合金提供带隙、有效质量、晶格、应变相关参数和温度/组分插值，并保留参数文献参考，可用于能带与异质结构模型输入准备。'),
    ]
    specs, checks = [], []
    for idx, (name, repo, tag, source_root, modules, documentation, description) in enumerate(definitions, 101):
        repository = 'https://github.com/'+repo
        api = 'https://api.github.com/repos/'+repo+'/releases/latest'
        release = read(web[api]['json_file'])
        require(release['tag_name'] == tag and not release['prerelease'] and not release['draft'], 'Review official release')
        root = Path('seed_pypi_raw')/name
        commit = git(root, 'rev-parse', 'HEAD')
        require(commit == git(root, 'rev-parse', tag+'^{commit}'), 'Release tag mismatch')
        require(git(root, 'remote', 'get-url', 'origin').removesuffix('.git') == repository, 'Repository mismatch')
        require(not git(root, 'status', '--porcelain', '--untracked-files=no'), 'Tracked source changes')
        spec = dict(name=name, index=idx, directory=name, source_root=source_root, modules=modules, repository=repository,
                    tag=tag, commit=commit, documentation=documentation, description=description, pypi=f'https://pypi.org/project/{name}/',
                    pypi_version=discovery[name]['info']['version'], checked_on='2026-09-17', release_api=api,
                    release_url=release['html_url'], release_published_at=release['published_at'], github_prerelease=False,
                    selection_rule='Latest official stable GitHub release, with current PyPI difference explicitly retained.', notes=[])
        if name == 'pymatgen-analysis-diffusion':
            spec['notes'] = ['GitHub正式版2025.11.14，PyPI2025.11.15；按指南固定正式Release并从本地源码安装。官方文档站404，研究采用发布文档/示例。']
        if name == 'effmass':
            spec['notes'] = ['GitHub正式版2.3.0，PyPI2.3.1，在线latest文档2.3.2.dev3；签名按v2.3.0。']
        if name == 'pyprocar':
            spec['identical_source_aliases'] = {'pyprocar/pyposcar/plotBands.py': 'pyprocar/pyposcar/plotbands.py'}
            spec['notes'] = ['Windows大小写文件碰撞；plotBands.py与plotbands.py Git blob均97d24d2617cd6151a4faee218ff3c02690974fbb，显式补齐同字节模块别名。', '要求NumPy<2，与sumo3.0的NumPy>=2不能直接共存；保留候选池，联合选择时处理替代关系。', '发布说明确认文档已迁往ReadTheDocs，旧GitHub Pages示例404。']
        payload = build_seed(spec, Path('seed_pypi_raw'))
        raw = Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection')/f'{name}_{tag}.json'
        raw.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        specs.append(spec)
        checks.append(dict(package=name, commit=commit, raw_path=raw.as_posix(), raw_sha256=file_sha(raw), release_evidence=web[api],
                           tracked_status=git(root, 'status', '--porcelain', '--untracked-files=no'), submodules=git(root, 'submodule', 'status').splitlines(),
                           nums=payload[0]['environment']['nums']))
        print(name, tag, payload[0]['environment']['nums'], flush=True)
    (BASE/'materials_electronic_sources.json').write_text(json.dumps(specs, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (BASE/'research/materials_electronic_source_checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
