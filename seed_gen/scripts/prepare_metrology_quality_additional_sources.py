"""Versioned optical/quality pools; read-only reuse of reviewed team sources."""
import argparse
import json
from pathlib import Path
from seed_gen.scripts.build_joint_scenario_seeds import read,require,file_sha
from seed_gen.scripts.extract_release_python_seeds import build_seed,git

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_metrology_quality')
DEFS={
 'optical':[
 ('pyElli','PyEllips/pyElli','v0.23.1','src',['elli'],'https://pyelli.readthedocs.io/en/latest/',
  'pyElli通过色散、材料、层和光学堆栈对象构建椭偏模型，支持2x2/4x4电磁传播、Psi/Delta及Mueller结果、标准数据读取与拟合集成，可用于可复现薄膜厚度和光学常数分析。'),
 ('lmfit','lmfit/lmfit-py','1.3.4','.', ['lmfit'],'https://lmfit.github.io/lmfit-py/',
  'lmfit以带名称、边界和约束的参数及残差/模型接口组织非线性最小二乘、拟合诊断和参数不确定性；本批用作光学模型到观测数据的优化补充。')],
 'quality':[
 ('networkx','networkx/networkx','networkx-3.6.1','.', ['networkx'],'https://networkx.org/documentation/stable/',
  'NetworkX提供带属性图、关系查询、DAG、连通与路径分析及图序列化，可承载制造谱系和质量证据的引用结构；图连通或关联不能自动证明根因。')]
}

def main():
    p=argparse.ArgumentParser();p.add_argument('batch',choices=DEFS);args=p.parse_args()
    web={r['url']:r for path in (BASE/'research').glob('*web/index.json') for r in read(path)}
    discovery={r['query_name']:r for r in read(BASE/'research/package_discovery.json')}
    specs=[];checks=[];RAW.mkdir(parents=True,exist_ok=True)
    for idx,(name,repo,tag,source_root,modules,docs,desc) in enumerate(DEFS[args.batch],720 if args.batch=='optical' else 730):
        api=f'https://api.github.com/repos/{repo}/releases/latest';rel=read(web[api]['json_file'])
        require(rel['tag_name']==tag and not rel['draft'] and not rel['prerelease'],'Review latest release')
        directory='l1_metrology_quality/'+name;root=Path('seed_pypi_raw')/directory
        require(git(root,'remote','get-url','origin').removesuffix('.git')=='https://github.com/'+repo,'Repository mismatch')
        require(not git(root,'status','--porcelain'),'Source changed')
        notes=['数据资料子模块未初始化；静态API仅解析发布包Python源码；固定样例使用显式材料常数，不依赖可变在线数据库。'] if name=='pyElli' else []
        specs.append(dict(name=name,index=idx,directory=directory,source_root=source_root,modules=modules,
            repository='https://github.com/'+repo,tag=tag,commit=git(root,'rev-parse','HEAD'),documentation=docs,
            description=desc,pypi=f'https://pypi.org/project/{name}/',pypi_version=discovery[name]['info']['version'],
            checked_on='2026-09-17',release_api=api,release_url=rel['html_url'],release_published_at=rel['published_at'],
            github_prerelease=False,notes=notes))
        checks.append(dict(package=name,release_evidence=web[api],tracked_status=git(root,'status','--porcelain')))
    if args.batch=='quality':
        shared=Path('seed_gen/scenario_collection/l1_test_fab/statistics_sources.json')
        for spec in read(shared):
            if spec['name'] in ['ruptures','scipy','pandas','scikit-learn']:
                specs.append(spec)
                checks.append(dict(package=spec['name'],reused_manifest=shared.as_posix(),
                                   reuse_rule='Exact reviewed release and immutable checkout reused read-only; fresh extraction compared in builder.'))
    for spec in specs:
        payload=build_seed(spec,Path('seed_pypi_raw'));destination=RAW/f'{spec["name"]}_{spec["tag"]}.json'
        destination.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        check=next(c for c in checks if c['package']==spec['name']);check.update(raw_path=destination.as_posix(),
            raw_sha256=file_sha(destination),commit=spec['commit'],nums=payload[0]['environment']['nums'])
        print(spec['name'],spec['tag'],check['nums'],flush=True)
    (BASE/f'{args.batch}_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/f'research/{args.batch}_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
