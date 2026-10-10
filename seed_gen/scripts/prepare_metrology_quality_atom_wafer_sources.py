"""Record official stable Atomap/wafermap and read-only sharedSTDF source."""
import json
from pathlib import Path
from seed_gen.scripts.build_joint_scenario_seeds import read,require,file_sha
from seed_gen.scripts.extract_release_python_seeds import build_seed,git
BASE=Path('seed_gen/scenario_collection/l1_metrology_quality');RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_metrology_quality')
def main():
    web={r['url']:r for p in (BASE/'research').glob('*web/index.json') for r in read(p) if r.get('status')==200}
    specs=[];checks=[]
    for i,name,repo,tag,modules,docs,description in [
      (780,'atomap','https://gitlab.com/atomap/atomap','0.4.2',['atomap'],'https://atomap.org/','Atomap从原子分辨HAADF-STEM图像寻找和精化原子列位置，构造子晶格/原子面并测量间距与位移；阈值和二维投影假设需独立检查。'),
      (781,'wafermap','https://github.com/cap1tan/wafermap','v0.3.2',['wafermap'],'https://github.com/cap1tan/wafermap','wafermap按晶圆半径、notch、die网格、点和图像绘制半导体晶圆地图并保存HTML；坐标、bin语义和重测归并由上游契约维护。')]:
        directory='l1_metrology_quality/'+name;root=Path('seed_pypi_raw')/directory
        require(git(root,'remote','get-url','origin').removesuffix('.git')==repo,'repo mismatch');require(not git(root,'status','--porcelain'),'dirty source')
        require(git(root,'rev-parse','HEAD')==git(root,'rev-parse',tag+'^{commit}'),'tag mismatch')
        api='https://api.github.com/repos/cap1tan/wafermap/releases/latest' if name=='wafermap' else 'https://gitlab.com/api/v4/projects/atomap%2Fatomap/repository/tags?per_page=10'
        raw=read(web[api]['json_file'])
        if name=='wafermap':require(raw['tag_name']==tag and not raw['prerelease'],'release mismatch')
        else:require(raw[0]['name']==tag and raw[0]['commit']['id']==git(root,'rev-parse','HEAD'),'tag evidence mismatch')
        specs.append(dict(name=name,index=i,directory=directory,source_root='.',modules=modules,repository=repo,tag=tag,commit=git(root,'rev-parse','HEAD'),documentation=docs,description=description,pypi=f'https://pypi.org/project/{name}/',pypi_version=tag.removeprefix('v'),checked_on='2026-09-17',release_api=api,release_url=repo+('/releases/tag/' if name=='wafermap' else '/-/tags/')+tag,selection_rule='Latest official stableRelease' if name=='wafermap' else 'NoGitLabReleases; latestofficialstabletag0.4.2 agreesPyPI0.4.2',notes=[] if name=='wafermap' else ['GitLabRelease列表为空，tag提交标题Release0.4.2；在线文档0.4.3.dev1领先固定源码。']))
    specs[0].update(release_published_at='2025-01-05T15:59:54+08:00',github_prerelease=False)
    specs[1].update(release_published_at='2025-12-08T16:37:02Z',github_prerelease=False)
    specs.append(next(s for s in read(Path('seed_gen/scenario_collection/l1_test_fab/ate_sources.json')) if s['name']=='Semi-ATE-STDF'))
    for spec in specs:
        payload=build_seed(spec,Path('seed_pypi_raw'));dest=RAW/f'{spec["name"]}_{spec["tag"]}.json';dest.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        checks.append(dict(package=spec['name'],raw_path=dest.as_posix(),raw_sha256=file_sha(dest),nums=payload[0]['environment']['nums']))
        print(spec['name'],payload[0]['environment']['nums'],flush=True)
    (BASE/'atom_wafer_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'research/atom_wafer_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':main()
