"""Read-only reuse of ASE/AbiPy sources and pinned GPAW full source indexing."""
import json
from pathlib import Path
import shutil
from seed_gen.scripts.collect_scenario_web_evidence import fetch
from seed_gen.scripts.extract_release_python_seeds import build_seed,git
from seed_gen.scripts.build_joint_scenario_seeds import file_sha

BASE=Path('seed_gen/scenario_collection/materials_solver')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/materials_solver')

def main():
    BASE.mkdir(parents=True,exist_ok=True);RAW.mkdir(parents=True,exist_ok=True);web=BASE/'research/source_web';web.mkdir(parents=True,exist_ok=True)
    specs=[];evidence=[];checks=[]
    for filename,name in [('materials_structure_sources.json','ase'),('materials_quantum_sources.json','abipy')]:
        spec=next(x for x in json.loads((BASE.parent/filename).read_text(encoding='utf-8')) if x['name']==name);specs.append(spec)
        source=RAW.parent/f'{name}_{spec["tag"]}.json';dest=RAW/source.name;shutil.copy2(source,dest)
        checks.append(dict(name=name,commit=spec['commit'],original_manifest=(BASE.parent/filename).as_posix(),original_raw=source.as_posix(),raw_sha256=file_sha(dest),reuse='Read-only source/full-index reuse; copied identical local artifact'))
    py=fetch('https://pypi.org/pypi/gpaw/json',web);tag=fetch('https://gitlab.com/api/v4/projects/gpaw%2Fgpaw/repository/tags/26.7.0',web);evidence += [py,tag]
    assert py['status']==200 and tag['status']==200
    pypi=json.loads(Path(py['json_file']).read_text(encoding='utf-8'));release=json.loads(Path(tag['json_file']).read_text(encoding='utf-8'))
    assert pypi['info']['version']=='26.7.0' and release['name']=='26.7.0'
    root=Path('seed_pypi_raw/gpaw');commit=git(root,'rev-parse','HEAD');assert commit=='9c6f4ccd94355b3e8c1c418b4b605d7ce7552e30' and release['commit']['id']==commit
    assert git(root,'remote','get-url','origin')=='https://gitlab.com/gpaw/gpaw.git'
    spec=dict(name='gpaw',index=181,directory='gpaw',source_root='.',modules=['gpaw'],repository='https://gitlab.com/gpaw/gpaw',tag='26.7.0',commit=commit,
              documentation='https://gpaw.readthedocs.io/',pypi='https://pypi.org/project/gpaw/',pypi_version='26.7.0',checked_on='2026-09-17',release_api=tag['url'],release_url='https://gitlab.com/gpaw/gpaw/-/tags/26.7.0',release_published_at=pypi['urls'][0]['upload_time_iso_8601'],github_prerelease=None,selection_rule='Latest stable PyPI release agrees with exact official GitLab tag/commit; publication time is PyPI upload.',description='GPAW基于投影缀加波方法实现密度泛函计算，提供平面波、实空间网格及局域轨道模式，通过ASE计算器接口接收原子结构和返回能量、力与电子态；常规后端使用编译扩展和数值库，26.7.0另有功能受限的官方纯Python后端，均需匹配PAW数据。',notes=['完整静态Python源码候选，不把原生扩展/动态继承宣称已完整反射；运行后端及PAW数据另行核验。'])
    payload=build_seed(spec,Path('seed_pypi_raw'));dest=RAW/'gpaw_26.7.0.json';dest.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');specs.append(spec)
    checks.append(dict(name='gpaw',commit=commit,raw_path=dest.as_posix(),raw_sha256=file_sha(dest),nums=payload[0]['environment']['nums'],release_evidence=tag))
    (BASE/'solver_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');(BASE/'research/source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');(web/'index.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print([(s['name'],s['tag']) for s in specs],flush=True)

if __name__=='__main__':main()
