"""Pinned stable optics packages and explicitly unreleased research candidates."""
from pathlib import Path
import json,shutil
from seed_gen.scripts.extract_release_python_seeds import build_seed,git
from seed_gen.scripts.build_joint_scenario_seeds import file_sha

BASE=Path('seed_gen/scenario_collection/lithography')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/lithography')

def main():
    RAW.mkdir(parents=True,exist_ok=True)
    specs=[];checks=[]
    for filename,name,lane in [('l1_design_lab/layout_sources.json','gdstk','l1_design_lab'),('l1_test_fab/statistics_sources.json','scipy','l1_test_fab')]:
        spec=next(x for x in json.loads((BASE.parent/filename).read_text(encoding='utf-8')) if x['name']==name)
        origin=RAW.parent/lane/f'{name}_{spec["tag"]}.json';dest=RAW/origin.name
        shutil.copy2(origin,dest);specs.append(spec)
        checks.append(dict(package=name,original_manifest=(BASE.parent/filename).as_posix(),original_raw=origin.as_posix(),raw_sha256=file_sha(dest),source_kind='official_stable_release'))
    alt=json.loads((BASE/'research/alternative_web/index.json').read_text(encoding='utf-8'))
    def data(url):return json.loads(Path(next(r for r in alt if r['url']==url)['json_file']).read_text(encoding='utf-8'))
    pypi=data('https://pypi.org/pypi/prysm/json');tags=data('https://api.github.com/repos/brandondube/prysm/tags?per_page=20')
    assert pypi['info']['version']=='0.21.1'
    commit=next(t['commit']['sha'] for t in tags if t['name']=='v0.21.1')
    spec=dict(name='prysm',index=1851,directory='lithography/prysm',modules=['prysm'],repository='https://github.com/brandondube/prysm',documentation='https://prysm.readthedocs.io/en/stable/',pypi='https://pypi.org/project/prysm/',pypi_version='0.21.1',tag='v0.21.1',commit=commit,release_published_at=pypi['releases']['0.21.1'][0]['upload_time_iso_8601'],checked_on='2026-09-17',github_prerelease=False,release_url='https://github.com/brandondube/prysm/tree/v0.21.1',release_api='https://api.github.com/repos/brandondube/prysm/tags?per_page=20',source_kind='official_stable_tag_and_distribution',selection_rule='PyPI latest stable 0.21.1 agrees with official v0.21.1 tag and docs/source/releases/v0.21.1.rst. GitHub latest Release page is older v0.21; the patch release is explicitly documented, not inferred from HEAD.',description='prysm提供数值傅里叶光学、波前传播、瞳孔/像差、点扩散与图像传递等接口；可作为小型标量相干光刻环境的光学核心，不自带完整工业光刻胶和OPC工艺模型。',notes=['已发布0.21.1标签、分发、发布说明一致；当前场景限定局部周期光学模型，非工业Hopkins/resist全栈。'])
    specs.append(spec)
    diffrows=json.loads((BASE/'research/diff_alternative_web/index.json').read_text(encoding='utf-8'))
    def diffdata(url):return json.loads(Path(next(r for r in diffrows if r['url']==url)['json_file']).read_text(encoding='utf-8'))
    torpy=diffdata('https://pypi.org/pypi/torchoptics/json')
    torrel=diffdata('https://api.github.com/repos/MatthewFilipovich/torchoptics/releases/latest')
    assert torpy['info']['version']=='1.0.2' and torrel['tag_name']=='v1.0.2' and torrel['prerelease'] is False
    root=Path('seed_pypi_raw/lithography/torchoptics');commit=git(root,'rev-parse','HEAD')
    assert commit=='34fe9c40f874e53db225e703c5580befe8c523b5'
    specs.append(dict(name='torchoptics',index=1856,directory='lithography/torchoptics',source_root='src',modules=['torchoptics'],repository='https://github.com/MatthewFilipovich/torchoptics',documentation='https://torchoptics.readthedocs.io/en/stable/',pypi='https://pypi.org/project/torchoptics/',pypi_version='1.0.2',tag='v1.0.2',commit=commit,release_published_at=torrel['published_at'],checked_on='2026-09-17',github_prerelease=False,release_url=torrel['html_url'],release_api='https://api.github.com/repos/MatthewFilipovich/torchoptics/releases/latest',source_kind='official_stable_release',selection_rule='Latest official non-prerelease GitHub release v1.0.2 agrees with PyPI1.0.2 and exact tag.',description='TorchOptics提供基于PyTorch的可微标量/矢量光场、光学元件、传播和系统优化接口；可在CPU小网格上核验梯度及逆向幅度掩膜设计，不等于完整标定的工业光刻工艺仿真。',notes=['作为无正式发布TorchLitho2的明确替代，验证固定CPU Fresnel过程窗口及自动微分，不声称GPU全片性能。']))
    candidates=[('lithosim','VLSIDA/lithosim','lithosim',['lithosim'],'非常基础的光刻成像、GDS/bitmap输入、SOCS与像素OPC研究代码；未发布，退火候选存在不可退出的随机选择循环。'),
                ('OpenILT','OpenOPC/OpenILT','OpenILT',['pycommon','pylitho','pyilt','opc','utils'],'OpenILT研究平台将光刻仿真、初始化、逆向优化与评估解耦，附带ICCAD13核；当前无发布版。'),
                ('TorchLitho-2.0','OpenOPC/TorchLitho-2.0','TorchLitho-2.0',['pylitho'],'TorchLitho 2.0使用PyTorch实现Abbe/Hopkins和自定义梯度；setup声明2.0.0但无对应发布tag或PyPI分发，保留为固定研究快照。'),
                ('TorchLitho','TorchOPC/TorchLitho','TorchLitho',['src'],'TorchLitho旧研究框架覆盖光学成像、可微模型和训练配置；无正式发布，不能与TorchLitho2共同作为同一已验证运行包。')]
    identity=json.loads((BASE/'research/identity_web/index.json').read_text(encoding='utf-8'))
    for i,(name,repo,directory,modules,description) in enumerate(candidates,1852):
        repository='https://github.com/'+repo;root=Path('seed_pypi_raw/lithography')/directory
        latest=next(r for r in identity if r['url']==f'https://api.github.com/repos/{repo}/releases/latest')
        tagsrow=next(r for r in identity if r['url']==f'https://api.github.com/repos/{repo}/tags?per_page=100')
        assert latest['status']==404 and json.loads(Path(tagsrow['json_file']).read_text(encoding='utf-8'))==[]
        commit=git(root,'rev-parse','HEAD')
        specs.append(dict(name=name,index=i,directory='lithography/'+directory,modules=modules,repository=repository,documentation=repository+'#readme',pypi='',pypi_version='',tag='snapshot-'+commit[:12],ref=commit,commit=commit,release_published_at='',checked_on='2026-09-17',github_prerelease=None,release_url='',release_api=latest['url'],source_kind='unreleased_commit_snapshot',selection_rule='No GitHub Release and zero tags; no verified PyPI distribution. Exact research commit only; NOT a release and NOT accepted by the stable-release gate.',description=description,include_call_protocol=True,notes=['UNRELEASED RESEARCH SNAPSHOT: source_kind=unreleased_commit_snapshot; tag字段仅文件索引标签，真实ref为完整commit；没有发布版本/发布日期。','未修改上游源码。静态全量候选不代表运行通过；未发布依赖用于正式场景时必须明确release例外/阻塞。']))
    for spec in specs:
        if spec['name'] in ('gdstk','scipy'):continue
        payload=build_seed(spec,Path('seed_pypi_raw'));dest=RAW/f'{spec["name"]}_{spec["tag"]}.json'
        dest.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        checks.append(dict(package=spec['name'],source_kind=spec['source_kind'],commit=spec['commit'],raw_path=dest.as_posix(),raw_sha256=file_sha(dest),nums=payload[0]['environment']['nums']))
    (BASE/'lithography_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'research/source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print([(r['package'],r.get('nums')) for r in checks],flush=True)

if __name__=='__main__':main()
