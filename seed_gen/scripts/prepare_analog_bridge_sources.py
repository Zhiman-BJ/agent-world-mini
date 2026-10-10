"""Collect fixed stable sources and explicit unreleased-name exclusions."""
import json
from pathlib import Path
from seed_gen.scripts.build_joint_scenario_seeds import read, require, file_sha
from seed_gen.scripts.extract_release_python_seeds import build_seed, git

BASE=Path('seed_gen/scenario_collection/analog_bridge')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/analog_bridge')

def main():
    web={x['url']:x for x in read(BASE/'research/analog_bridge_web/index.json')}
    api='https://api.github.com/repos/unihd-cag/skillbridge/releases/latest'
    release=read(web[api]['json_file'])
    require(release['tag_name']=='releases/1.8.0' and not release['prerelease'],'Unexpected release')
    package=read(web['https://pypi.org/pypi/skillbridge/json']['json_file'])['info']
    require(package['version']=='1.8.0' and package['project_urls']['Repository']=='https://github.com/unihd-cag/skillbridge','Wrong distribution')
    root=Path('seed_pypi_raw/analog_bridge/skillbridge')
    require(git(root,'rev-parse','HEAD')=='ca6105eec77a587db39d61c6e26f60be46065e48','Wrong release commit')
    specs=[dict(name='skillbridge',index=9801,directory='analog_bridge/skillbridge',source_root='.',modules=['skillbridge'],
      repository='https://github.com/unihd-cag/skillbridge',tag='releases/1.8.0',commit=git(root,'rev-parse','HEAD'),
      documentation='https://unihd-cag.github.io/skillbridge/',
      description='Skillbridge将Python对象、参数和调用转换为Cadence Virtuoso的SKILL命令，通过远程会话读取/修改设计对象；实际原理图和版图执行依赖商业后端，官方测试通道仅用于离线协议验证。',
      pypi='https://pypi.org/project/skillbridge/',pypi_version='1.8.0',checked_on='2026-09-17',release_api=api,
      release_url=release['html_url'],release_published_at=release['published_at'],github_prerelease=False,
      include_call_protocol=True,notes=['完整候选包含源码公开接口与显式__call__；ws.db/rod动态函数名不伪造成逐个源定义方法。','正式tag含斜杠，原始路径保留skillbridge_releases/1.8.0.json。'])]
    for manifest,names in [('logic_sources.json',['hdl21','vlsirtools']),('layout_sources.json',['klayout'])]:
        for spec in read(Path('seed_gen/scenario_collection/l1_design_lab')/manifest):
            if spec['name'] in names:
                specs.append(spec)
    checks=[]
    for spec in specs:
        payload=build_seed(spec,Path('seed_pypi_raw'))
        dest=RAW/f'{spec["name"]}_{spec["tag"]}.json';dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        checks.append(dict(package=spec['name'],raw_path=dest.as_posix(),raw_sha256=file_sha(dest),nums=payload[0]['environment']['nums']))
        print(spec['name'],payload[0]['environment']['nums'],flush=True)
    (BASE/'analog_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    exclusions=[]
    for repo,name in [('fredrief/pade','PADE'),('bluecheetah/bag','BAG3'),('ucb-art/BAG_framework','BAG')]:
        queries=[]
        for endpoint in ['releases','tags']:
            url=f'https://api.github.com/repos/{repo}/{endpoint}';e=web[url]
            require(e['status']==200,'Unavailable exclusion evidence')
            values=read(e['json_file']);require(values==[],'New release/tag requires reconsideration')
            queries.append(dict(url=url,status=200,count=0,json_file=e['json_file'],sha256=file_sha(e['json_file'])))
        exclusions.append(dict(name=name,repository='https://github.com/'+repo,decision='excluded_no_official_release_or_tag',queries=queries,
          reason='未找到可核验发布源码，保留实际场景作为需求线索，不将默认分支克隆冒充发布版或构造假全量索引。'))
    unrelated=read(web['https://pypi.org/pypi/pade/json']['json_file'])['info']
    exclusions.append(dict(name='pade on PyPI',decision='excluded_unrelated_distribution',summary=unrelated['summary'],project_urls=unrelated['project_urls']))
    (BASE/'research/source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'research/unavailable_candidates.json').write_text(json.dumps(exclusions,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
