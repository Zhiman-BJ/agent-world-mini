"""Pin reviewed ML-potential and quantum candidates to official releases/tags."""
import json
from pathlib import Path

from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require
from seed_gen.scripts.extract_release_python_seeds import build_seed, git

BASE = Path('seed_gen/scenario_collection')


def main():
    web = {row['url']: row for row in read(BASE/'research/materials_quantum_release_web/index.json')}
    discovery = {row['query_name']: row for row in read(BASE/'research/materials_package_discovery.json')}
    definitions = [
        ('matgl', 'matgl', 'materialyzeai/matgl', 'materialsvirtuallab/matgl', 'v4.0.3', 'src', ['matgl'], 'https://matgl.ai',
         'MatGL使用图神经网络构建材料能量、力和其他性质模型，提供模型加载、结构图转换、训练及ASE计算器接口；不同模型权重、图后端及硬件要求需单独验证。'),
        ('chgnet', 'chgnet', 'CederGroupHub/chgnet', 'CederGroupHub/chgnet', 'v0.4.2', '.', ['chgnet'], 'https://cedergrouphub.github.io/chgnet/',
         'CHGNet是带磁矩信息约束的预训练通用原子势，提供晶体图、能量/力/应力预测、结构弛豫、分子动力学及训练接口；模型适用性和误差不由接口验证保证。'),
        ('mace-torch', 'mace', 'ACEsuit/mace', 'ACEsuit/mace', 'v0.3.16', '.', ['mace'], 'https://mace-docs.readthedocs.io/',
         'MACE提供等变原子图神经网络势、模型训练和预训练势计算器，可连接ASE执行能量/力计算、弛豫及动力学；模型文件与数值后端需独立固定。'),
        ('pythtb', 'pythtb', 'pythtb/pythtb', 'pythtb/pythtb', 'v2.0.2', '.', ['pythtb'], 'https://pythtb.readthedocs.io/en/latest/',
         'PythTB提供紧束缚晶格、轨道、跃迁和哈密顿量建模，可计算能带、本征态、Berry相位、Wilson环及拓扑指标，适合小型解析可验证晶格模型。'),
        ('pybinding', 'pybinding', 'dean0x7d/pybinding', 'dean0x7d/pybinding', 'v0.9.5', '.', ['pybinding'], 'https://docs.pybinding.site/',
         'pybinding将晶格、几何、边界和扰动组合成紧束缚模型，提供稀疏求解、局域态和输运相关接口；核心C++扩展、继承接口和编译兼容需单独核查。'),
        ('wannierberri', 'wannierberri', 'wannier-berri/wannier-berri', 'wannier-berri/wannier-berri', 'v1.7.0', '.', ['wannierberri'], 'https://wannier-berri.org',
         'WannierBerri利用Wannier紧束缚表示插值能带和Berry相关量，支持布里渊区积分、输运响应与Wannier数据读取；具体积分精度、物理对称和输入矩阵约定需要验证。'),
        ('tbmodels', 'tbmodels', 'Z2PackDev/TBmodels', 'Z2PackDev/TBmodels', 'v1.4.3', '.', ['tbmodels'], 'https://tbmodels.greschd.ch',
         'TBmodels用于构造、变换、求解和保存紧束缚模型，能读取Wannier90数据并表示轨道与跃迁矩阵，可作为拓扑计算的哈密顿量来源。'),
        ('abipy', 'abipy', 'abinit/abipy', 'abinit/abipy', 'v1.0.0', '.', ['abipy'], 'https://abinit.github.io/abipy/',
         'AbiPy提供ABINIT输入生成、任务/工作流管理及电子结构输出分析，覆盖结构、能带、声子和响应计算；实际DFT求解需单独安装ABINIT与赝势数据。'),
    ]
    specs, checks = [], []
    for index, (name, directory, repository, release_repository, tag, source_root, modules, docs, description) in enumerate(definitions, 121):
        api = 'https://api.github.com/repos/'+release_repository+'/releases/latest'
        evidence = web[api]
        info = discovery[name]
        if evidence['status'] == 200:
            release = read(evidence['json_file'])
            require(release['tag_name']==tag and not release['draft'] and not release['prerelease'], f'Release review needed: {name}')
            released, release_url, prerelease = release['published_at'], release['html_url'], False
            rule = 'Latest official stable GitHub Release at checked_on; use its exact tag even if PyPI differs.'
        else:
            require(evidence['status']==404 and info['info']['version']==tag.removeprefix('v'), f'No matching stable release: {name}')
            released = min(f['upload_time_iso_8601'] for f in info['latest_pypi_files'] if not f.get('yanked'))
            release_url, prerelease = 'https://github.com/'+repository+'/tree/'+tag, None
            rule = 'Official GitHub Releases absent (404); latest stable PyPI version agrees with exact official Git tag. Date is PyPI upload, not a fabricated GitHub Release.'
        root = Path('seed_pypi_raw')/directory
        commit = git(root, 'rev-parse', 'HEAD')
        require(git(root, 'remote', 'get-url', 'origin').removesuffix('.git')=='https://github.com/'+repository, f'Remote mismatch: {name}')
        require(git(root, 'rev-parse', tag+'^{commit}')==commit, f'Tag mismatch: {name}')
        spec = dict(name=name, index=index, directory=directory, source_root=source_root, modules=modules,
                    repository='https://github.com/'+repository, tag=tag, commit=commit, documentation=docs,
                    pypi=f'https://pypi.org/project/{name}/', pypi_version=info['info']['version'],
                    release_published_at=released, checked_on='2026-09-17', github_prerelease=prerelease,
                    release_api=api, release_url=release_url, selection_rule=rule, description=description,
                    notes=['完整静态Python候选池；不据此声明原生扩展、继承/动态导出、模型权重、外部求解器或任务已验证。'])
        if name=='wannierberri':
            spec['notes'].append('GitHub最新正式Release仍为v1.7.0，PyPI为26.7.0；按当前指南优先正式Release，保留差异而不跟随默认分支。')
        if name=='matgl':
            spec['notes'].append('旧官方仓库materialsvirtuallab/matgl重定向materialyzeai/matgl；主页明确链接该仓库。')
        payload = build_seed(spec, Path('seed_pypi_raw'))
        raw = Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection')/f'{name}_{tag}.json'
        raw.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        specs.append(spec)
        checks.append({'package': name, 'commit': commit, 'release': evidence, 'raw_path': raw.as_posix(),
                       'raw_sha256': file_sha(raw), 'nums': payload[0]['environment']['nums'],
                       'tracked_status': git(root, 'status', '--porcelain', '--untracked-files=no')})
        print(name, tag, payload[0]['environment']['nums'], flush=True)
    (BASE/'materials_quantum_sources.json').write_text(json.dumps(specs, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (BASE/'research/materials_quantum_source_checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
