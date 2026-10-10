"""Reviewed layout release candidates; reuse KLayout only after pin checks."""
import json
from pathlib import Path
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW
from seed_gen.scripts.extract_release_python_seeds import build_seed,git


def main():
    web={r['url']:r for r in json.loads((BASE/'research/design_release_web/index.json').read_text(encoding='utf-8'))}
    discovery={r['query_name']:r for r in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
    specs=[]
    for index,(name,repo,tag,source_root,module,description) in enumerate([
        ('gdsfactory','gdsfactory/gdsfactory','v9.51.0','.','gdsfactory','gdsfactory以参数化组件、端口、截面和工艺定义组织光子/芯片层次版图，提供放置、连接、光电布线和GDS文件读写；原生几何和继承对象来自KLayout/kfactory。'),
        ('kfactory','gdsfactory/kfactory','v3.0.4','src','kfactory','kfactory在KLayout数据库之上提供带类型/单位的单元、实例、端口、变换和工艺模型，是gdsfactory层次版图与布线的基础；当前版本遵循gdsfactory的兼容依赖。'),
        ('klayout','KLayout/klayout','v0.30.12','src/pymod/distutils_src','klayout','KLayout提供GDS/OASIS层次数据库、整数/浮点几何、区域布尔运算、设计规则检查和版图连接提取，支持无GUI的Python芯片版图处理。'),
        ('gdstk','heitzmann/gdstk','v1.0.1','.','gdstk','GDSTK提供基于C++的GDSII/OASIS读写、层次单元、路径与多边形布尔运算；本场景优先KLayout统一数据库，保留GDSTK候选但不重复暴露。'),
    ],331):
        api='https://api.github.com/repos/'+repo+'/releases/latest'
        release=json.loads(Path(web[api]['json_file']).read_text(encoding='utf-8'))
        latest=release
        if name=='kfactory':
            evidence={'latest_release':latest,'latest_commit':git(Path('seed_pypi_raw/l1_design_lab/kfactory'),'rev-parse','HEAD'),'selected_tag':tag,'selected_commit':git(Path('seed_pypi_raw/l1_design_lab/kfactory_3.0.4'),'rev-parse','HEAD'),'reason':'gdsfactory9.51.0依赖kfactory[ipy]>=3.0.4,<3.1.dev0；最新3.2.1不可组合。最新全量候选池独立保留。','uv_error':'Because gdsfactory==9.51.0 depends on kfactory[ipy]>=3.0.4,<3.1.dev0 and you require kfactory==3.2.1, your requirements are unsatisfiable.'}
            (BASE/'research/kfactory_compatibility.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            api='https://api.github.com/repos/gdsfactory/kfactory/releases/tags/v3.0.4'
            compat={r['url']:r for r in json.loads((BASE/'research/layout_web/index.json').read_text(encoding='utf-8'))}
            release=json.loads(Path(compat[api]['json_file']).read_text(encoding='utf-8'))
        assert release['tag_name']==tag and not release['prerelease']
        directory='klayout' if name=='klayout' else 'l1_design_lab/'+name
        if name=='kfactory': directory='l1_design_lab/kfactory_3.0.4'
        root=Path('seed_pypi_raw')/directory
        assert git(root,'remote','get-url','origin').removesuffix('.git')=='https://github.com/'+repo
        docs={'gdsfactory':'https://gdsfactory.github.io/gdsfactory/','kfactory':'https://gdsfactory.github.io/kfactory/','klayout':'https://www.klayout.de/doc-qt5/programming/python.html','gdstk':'https://heitzmann.github.io/gdstk/'}[name]
        spec=dict(name=name,index=index,directory=directory,source_root=source_root,modules=[module],repository='https://github.com/'+repo,tag=tag,commit=git(root,'rev-parse','HEAD'),documentation=docs,description=description,pypi=f'https://pypi.org/project/{name}/',pypi_version=discovery[name]['info']['version'],checked_on='2026-09-17',release_api=api,release_url=release['html_url'],release_published_at=release['published_at'],github_prerelease=False,notes=['最新官方稳定Release；继承API按真实定义类保留，任务选择时明确状态/单位边界。'])
        if name=='klayout':
            spec['adapter']='klayout'
            spec['notes'].append('复用已存在相同发布commit且无追踪修改的源码，只读重新核验；官方core.pyi原生适配，不改变共享源码。')
        if name=='gdstk': spec['adapter']='gdstk'
        if name=='kfactory': spec['notes']=['官方稳定3.0.4，gdsfactory9.51.0要求>=3.0.4,<3.1.dev0；最新3.2.1来源/commit保存在research/kfactory_compatibility.json，最新全量池也保留但不供场景引用。']
        payload=build_seed(spec,Path('seed_pypi_raw'))
        (RAW/f'{name}_{tag}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec)
        print(name,payload[0]['environment']['nums'],flush=True)
    (BASE/'layout_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
