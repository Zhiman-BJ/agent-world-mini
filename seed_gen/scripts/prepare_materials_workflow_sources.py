"""Bind official releases/tags for workflow, phonon and defect candidate pools."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require
from seed_gen.scripts.extract_release_python_seeds import build_seed, git

BASE = Path('seed_gen/scenario_collection')
MANIFEST = BASE/'materials_workflow_physics_sources.json'
RAW = Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection')
NAMES = ['atomate2','jobflow','custodian','phonopy','phono3py','py-sc-fermi','doped','pymatgen-analysis-defects','shakenbreak']


def main():
    old = {s['name']:s for p in [Path('seed_gen/pypi_materials_sources.json'),Path('seed_gen/pypi_device_defect_sources.json')] for s in read(p)}
    old['atomate2'] = {'name':'atomate2','index':2,'repository':'https://github.com/materialsproject/atomate2',
                      'documentation':'https://materialsproject.github.io/atomate2/','directory':'atomate2','source_root':'src','modules':['atomate2'],
                      'tag':'v0.1.5','description':'atomate2基于jobflow编排材料计算与后处理，提供可配置的VASP及其他计算后端的Maker、Job和Flow，并通过custodian处理外部计算错误。'}
    evidence = {r['url']:r for p in (BASE/'research').glob('*web/index.json') for r in read(p)}
    discovery_path = BASE/'research/materials_package_discovery.json'
    discovery = {r['query_name']:r for r in read(discovery_path)}
    specs, checks = [], []
    for name in NAMES:
        spec = copy.deepcopy(old[name])
        repo = spec['repository']
        api = repo.replace('https://github.com/','https://api.github.com/repos/')+'/releases/latest'
        entry = evidence.get(api) or evidence.get(api.replace('/shakenbreak/','/ShakeNBreak/'))
        require(entry is not None, f'Missing official release lookup: {name}')
        py = discovery[name]
        require(py['status']==200,'Missing PyPI identity')
        if entry['status']==200:
            release = read(entry['json_file'])
            require(not release['prerelease'] and not release['draft'],'Not a stable release')
            if name=='py-sc-fermi':
                require(release['tag_name']=='3.0.0','Review new release first')
                spec.update(tag='3.0.0',directory='py-sc-fermi_3.0.0')
            else:
                require(spec['tag']==release['tag_name'],f'Latest release changed: {name}')
            spec.update(release_published_at=release['published_at'],github_prerelease=False,release_api=api,release_url=release['html_url'],
                        selection_rule='Latest official non-prerelease GitHub release; PyPI version differences recorded.')
        else:
            require(name in ('phonopy','phono3py') and entry['status']==404,'Unexpected release lookup failure')
            require(spec['tag'].removeprefix('v')==py['info']['version'],'PyPI and tag disagree')
            spec.update(release_url=repo+'/releases/tag/'+spec['tag'],release_api=api,github_prerelease=None,
                        release_published_at=min(f['upload_time_iso_8601'] for f in py['latest_pypi_files']),
                        selection_rule='GitHub releases/latest returned404; latest stable PyPI version plus exact official remote tag and tagged version source checked. Timestamp is PyPI publication.')
        root=Path('seed_pypi_raw')/spec['directory']
        commit=git(root,'rev-parse','HEAD')
        require(git(root,'rev-parse',spec['tag']+'^{commit}')==commit,'Wrong local release tag')
        require(not git(root,'status','--porcelain','--untracked-files=no'),'Tracked source changes')
        remote=git(root,'remote','get-url','origin')
        require(remote.removesuffix('.git').lower()==repo.lower(),'Repository identity mismatch')
        if name in ('phonopy','phono3py'):
            tags=git(root,'ls-remote','--tags','origin','refs/tags/'+spec['tag'],'refs/tags/'+spec['tag']+'^{}')
            require(commit in tags,'Remote tag no longer matches checkout')
        spec.update(commit=commit,checked_on='2026-09-17',pypi=f'https://pypi.org/project/{name}/',pypi_version=py['info']['version'])
        spec['notes']=['核查官方仓库、文档、发布记录和本地tag/HEAD/remote；全量API静态解析不等于外部求解器已运行。']
        if name=='py-sc-fermi':
            spec['description']='py-sc-fermi 根据预先计算的缺陷形成能、电子态密度和晶胞体积，以电中性条件自洽求解费米能级、缺陷及电子和空穴浓度。3.0版采用固定温度快照和位点排斥统计，低浓度下回到稀释模型，并支持指定冻结浓度、位点池和元素约束。'
            spec['notes']+=['最新GitHub稳定Release3.0.0比PyPI2.2.2新，使用独立源码目录并从该发布源码安装；保留旧2.2.2索引和目录。']
        if name in ('phonopy','phono3py'):
            spec['notes']+=['官方releases/latest无Release，使用PyPI稳定版与官方远程标签；不能沿用旧清单的GitHub Release表述。']
        if name=='atomate2':
            spec['notes']+=['phonons可选依赖要求phonopy<4；本批工作流与phonopy4.5.0声子场景使用独立环境，不宣称可直接混装。']
        payload=build_seed(spec,Path('seed_pypi_raw'))
        path=RAW/f'{name}_{spec["tag"]}.json'
        path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        legacy=RAW.parent/path.name
        record={'package':name,'tag':spec['tag'],'commit':commit,'remote':remote,'tracked_status':git(root,'status','--porcelain','--untracked-files=no'),
                'submodules':git(root,'submodule','status').splitlines(),'release_evidence':entry,
                'pypi_discovery_file':discovery_path.as_posix(),'pypi_discovery_sha256':file_sha(discovery_path),
                'pypi_evidence':py,'raw_path':path.as_posix(),'nums':payload[0]['environment']['nums']}
        if legacy.exists():
            record['legacy_tools_identical']=read(legacy)[0]['init_ref_tools']==payload[0]['init_ref_tools']
        specs.append(spec); checks.append(record)
        print(name,spec['tag'],record['nums'],flush=True)
    MANIFEST.write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'research/materials_workflow_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
