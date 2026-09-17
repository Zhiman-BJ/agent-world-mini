"""Pin official Kwant/tkwant stable tags including Cython reference APIs."""
import json
from pathlib import Path
from seed_gen.scripts.extract_release_python_seeds import build_seed, git
from seed_gen.scripts.build_joint_scenario_seeds import read, require

BASE=Path('seed_gen/scenario_collection')


def main():
    web={r['url']:r for r in read(BASE/'research/materials_transport_release_web/index.json')}
    definitions=[('kwant','v1.5.0','de315f171270d62ee2412c4084260c912cc4d58f','1.5.0','https://kwant-project.org/doc/1/',
                  'Kwant提供量子紧束缚散射系统的构建、周期导线与开放边界，计算散射矩阵、透射、态密度与局域电流，可用于纳米器件静态量子输运。'),
                 ('tkwant','v1.1.1','b2040b88d56b90288f1b6617e7f2070ec1db7ef5','1.1.0','https://tkwant.kwant-project.org/doc/stable/',
                  'tkwant基于Kwant系统传播含时单粒子和多体散射态，支持脉冲势、开放导线边界与时间相关密度/电流，用于瞬态量子输运。')]
    specs=[]
    for index,(name,tag,commit,pypi_version,docs,description) in enumerate(definitions,151):
        root=Path('seed_pypi_raw')/name
        repository='https://gitlab.kwant-project.org/kwant/'+name
        tag_url=repository+'/-/tags/'+tag
        require(web[tag_url]['status']==200,'Official tag evidence missing')
        notes=['Cython Python-visible def/cpdef/property与公开类已全量适配；__call__作为求解/算子实际协议保留。']
        if name=='tkwant': notes.append('最新正式标签v1.1.1内TKWANT_VERSION仍1.1.0，PyPI亦1.1.0；运行需固定该tag的完整commit，不用conda1.1.0rc2代替。')
        spec=dict(name=name,index=index,directory=name,source_root='.',modules=[name],adapter=name,include_call_protocol=True,
                  repository=repository,documentation=docs,pypi=f'https://pypi.org/project/{name}/',pypi_version=pypi_version,
                  tag=tag,commit=commit,release_published_at=git(root,'show','-s','--format=%cI','HEAD'),
                  checked_on='2026-09-17',github_prerelease=None,notes=notes,description=description,
                  release_api=tag_url,release_url=tag_url,
                  selection_rule='Latest stable official GitLab tag reviewed against remote tags and release docs. Timestamp is tagged commit date, not an invented GitHub release date.')
        require(git(root,'remote','get-url','origin').removesuffix('.git')==repository,'Remote mismatch')
        payload=build_seed(spec,Path('seed_pypi_raw'))
        target=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection')/f'{name}_{tag}.json'
        target.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec)
        print(name,payload[0]['environment']['nums'],flush=True)
    (BASE/'materials_transport_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__': main()
