"""Re-extract explicitly reviewed official releases for metrology/quality."""
import json
from pathlib import Path
from seed_gen.scripts.build_joint_scenario_seeds import read, require, file_sha
from seed_gen.scripts.extract_release_python_seeds import build_seed, git

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_metrology_quality')
DEFINITIONS=[
    ('reliability','MatthewReid854/reliability','v0.9.0','reliability','https://reliability.readthedocs.io/en/latest/',
     'reliability提供寿命分布拟合、删失数据分析、非参数可靠度、加速寿命试验与可修复系统统计，可将受控试验观测转为可靠度、分位寿命及不确定性诊断。统计拟合不代替失效机制确认和实际资格认证。'),
    ('surpyval','derrynknife/SurPyval','v0.18.0','surpyval','https://surpyval.readthedocs.io/en/latest/',
     'SurPyval提供包含删失和截断的生存统计、参数与非参数分布、回归、重复事件及退化轨迹分析，并支持模型序列化。模型结论受观测机制、分布和应力适用范围限制。'),
    ('lifelines','CamDavidsonPilon/lifelines','v0.30.3','lifelines','https://lifelines.readthedocs.io/en/latest/',
     'lifelines提供Kaplan-Meier等寿命统计和Cox/AFT生存回归，支持事件删失、协变量、模型诊断与预测，适合批次或应力关联的可靠性统计；不把观察相关性自动解释为因果。'),
]

def main():
    web={r['url']:r for r in read(BASE/'research/reliability_web/index.json')}
    discovery={r['query_name']:r for r in read(BASE/'research/package_discovery.json')}
    specs=[]; checks=[]; RAW.mkdir(parents=True,exist_ok=True)
    for index,(name,repo,tag,module,docs,description) in enumerate(DEFINITIONS,701):
        api=f'https://api.github.com/repos/{repo}/releases/latest'
        release=read(web[api]['json_file']); repository='https://github.com/'+repo
        require(release['tag_name']==tag and not release['prerelease'] and not release['draft'],'Unreviewed release')
        directory='l1_metrology_quality/'+name; root=Path('seed_pypi_raw')/directory
        require(git(root,'remote','get-url','origin').removesuffix('.git')==repository,'Wrong origin')
        require(not git(root,'status','--porcelain'),'Source worktree changed')
        spec=dict(name=name,index=index,directory=directory,source_root='.',modules=[module],repository=repository,
                  tag=tag,commit=git(root,'rev-parse','HEAD'),documentation=docs,description=description,
                  pypi=f'https://pypi.org/project/{name}/',pypi_version=discovery[name]['info']['version'],
                  checked_on='2026-09-17',release_api=api,release_url=release['html_url'],
                  release_published_at=release['published_at'],github_prerelease=False,notes=[])
        if name=='surpyval': spec['notes']=['最新官方非预发布GitHub Release为v0.18.0；PyPI为0.19.0，按指南固定官方Release。在线latest文档可领先，API按发布源码。']
        payload=build_seed(spec,Path('seed_pypi_raw')); destination=RAW/f'{name}_{tag}.json'
        destination.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec);checks.append(dict(package=name,release_evidence=web[api],remote=repository,
            raw_path=destination.as_posix(),raw_sha256=file_sha(destination),commit=spec['commit'],
            tracked_status=git(root,'status','--porcelain'),submodules=git(root,'submodule','status').splitlines(),
            nums=payload[0]['environment']['nums']))
        print(name,tag,payload[0]['environment']['nums'])
    (BASE/'reliability_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'research/reliability_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__': main()
