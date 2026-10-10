"""Official ATE/wafer-map release candidates."""
import json
from pathlib import Path
import subprocess
from seed_gen.scripts.collect_scenario_web_evidence import fetch
from seed_gen.scripts.extract_release_python_seeds import build_seed,git
from seed_gen.scripts.build_joint_scenario_seeds import file_sha
from seed_gen.scripts.prepare_test_fab_sources import BASE,RAW

DEFS=[('Semi-ATE-STDF','Semi-ATE/STDF','0.1.33','.',['Semi_ATE.STDF'],'https://github.com/Semi-ATE/STDF',
       'Semi-ATE-STDF提供半导体ATE的STDF记录读写、字段类型校验和ATDF转换，支持按发布记录格式解析晶圆/器件/测试信息；记录解析不自动解决重测、合批和良率业务语义。'),
      ('pystdf','cmars/pystdf','v1.4.0','.',['pystdf'],'https://github.com/cmars/pystdf',
       'PySTDF以事件流解析STDF并提供表格/文本输出及记录映射，是ATE结果解析的另一实现；与Semi-ATE-STDF比较主入口和错误处理后择一。'),
      ('wfmap','xlhaw/wfmap','1.0.3','.',['wfmap'],'https://github.com/xlhaw/wfmap',
       'wfmap把晶粒坐标和类别/数值映射为晶圆可视化，提供样式、图像与图例构造；缺测、pass/fail和坐标方向需由工程数据契约明确。')]

def main():
    webdir=BASE/'research/ate_source_web';webdir.mkdir(parents=True,exist_ok=True);RAW.mkdir(parents=True,exist_ok=True)
    leads={x['query_name']:x for x in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
    specs=[];checks=[];evidence=[]
    for idx,(name,slug,tag,sroot,modules,docs,desc) in enumerate(DEFS,551):
        repo='https://github.com/'+slug;api='https://api.github.com/repos/'+slug+'/releases/latest';rec=fetch(api,webdir);evidence.append(rec)
        assert rec['status']==200;release=json.loads(Path(rec['json_file']).read_text(encoding='utf-8'));assert release['tag_name']==tag and not release['prerelease']
        directory='l1_test_fab/'+name;root=Path('seed_pypi_raw')/directory
        if not root.exists():subprocess.run(['git','clone','--depth','1','--branch',tag,repo+'.git',str(root)],check=True)
        commit=git(root,'rev-parse','HEAD');assert commit==git(root,'rev-parse',tag+'^{commit}') and not git(root,'status','--porcelain','--untracked-files=no')
        assert git(root,'remote','get-url','origin').removesuffix('.git')==repo
        spec=dict(name=name,index=idx,directory=directory,source_root=sroot,modules=modules,repository=repo,tag=tag,commit=commit,documentation=docs,description=desc,pypi='https://pypi.org/project/'+name+'/',pypi_version=leads[name]['info']['version'],checked_on='2026-09-17',release_api=api,release_url=release['html_url'],release_published_at=release['published_at'],github_prerelease=False,selection_rule='Latest official stable GitHub Release',notes=[])
        if name=='Semi-ATE-STDF':spec['notes']=['最新正式Release0.1.33，PyPI同名分发0.1.28；采用源码正式Release，安装固定提交。']
        payload=build_seed(spec,Path('seed_pypi_raw'));path=RAW/f'{name}_{tag}.json';path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec);checks.append(dict(name=name,commit=commit,raw_path=path.as_posix(),raw_sha256=file_sha(path),nums=payload[0]['environment']['nums'],release_evidence=rec,tracked_status=git(root,'status','--porcelain','--untracked-files=no'),submodules=git(root,'submodule','status').splitlines()))
        (BASE/'ate_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');(BASE/'research/ate_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');(webdir/'index.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(name,tag,payload[0]['environment']['nums'],flush=True)

if __name__=='__main__':main()
