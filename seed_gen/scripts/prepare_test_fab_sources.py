"""Reviewed stable-release source pools for the production/maintenance pilot."""
import json
from pathlib import Path
import subprocess
import requests
from seed_gen.scripts.collect_scenario_web_evidence import fetch
from seed_gen.scripts.extract_release_python_seeds import build_seed, git
from seed_gen.scripts.build_joint_scenario_seeds import file_sha

BASE=Path('seed_gen/scenario_collection/l1_test_fab')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_test_fab')
DEFINITIONS=[
 ('simpy','https://gitlab.com/team-simpy/simpy','4.1.2','src',['simpy'],'https://simpy.readthedocs.io/en/stable/',
  'SimPy以生成器进程、事件和共享资源构建离散事件仿真，可表示生产批次、设备队列、停机和运输等待；工艺时间与规则由使用者提供，不自带真实晶圆厂模型。'),
 ('salabim','https://github.com/salabim/salabim','26.0.1','.', ['salabim'],'https://www.salabim.org/manual/',
  'salabim提供离散事件仿真组件、队列、资源、状态和统计监视器，也支持动画；本轮采用无图形固定模型验证，不连接实际制造系统。'),
 ('pyjobshop','https://github.com/PyJobShop/PyJobShop','v0.0.9','.', ['pyjobshop'],'https://pyjobshop.org/stable/',
  'PyJobShop用作业、任务、机器、模式、时序和资源约束描述生产调度问题，调用CP-SAT等求解器生成排程，支持停机、设置时间和多目标。'),
 ('job-shop-lib','https://github.com/Pabloo22/job_shop_lib','v1.7.0','.', ['job_shop_lib'],'https://job-shop-lib.readthedocs.io/en/stable/',
  'Job Shop Lib提供作业车间实例、排程、派工规则、图表示和求解接口，可用于比较FIFO、SPT等规则并检验资源和工序约束。'),
 ('reliability','https://github.com/MatthewReid854/reliability','v0.9.0','.', ['reliability'],'https://reliability.readthedocs.io/en/latest/',
  'reliability提供寿命分布、含删失数据的参数拟合、可靠性与维修分析和置信区间工具；参数估计必须保留故障/删失时间及观察窗口。'),
 ('surpyval','https://github.com/derrynknife/SurPyval','v0.18.0','.', ['surpyval'],'https://surpyval.readthedocs.io/en/latest/',
  'SurPyval提供参数与非参数生存分析及多种删失/截断数据建模，用于寿命和可靠性估计；本轮保留其完整API候选，完整和右删失样例优先采用reliability。'),
]

def main():
 RAW.mkdir(parents=True,exist_ok=True)
 webdir=BASE/'research/production_source_web'; webdir.mkdir(parents=True,exist_ok=True)
 discovery={r['query_name']:r for r in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
 specs=[]; evidence=[]; checks=[]
 for i,(name,repo,tag,sroot,modules,docs,description) in enumerate(DEFINITIONS,501):
  api=repo.replace('https://github.com/','https://api.github.com/repos/')+'/releases/latest' if 'github.com' in repo else 'https://gitlab.com/api/v4/projects/team-simpy%2Fsimpy/repository/tags/4.1.2'
  rec=fetch(api,webdir); evidence.append(rec)
  if rec['status']!=200: raise RuntimeError((api,rec))
  release=json.loads(Path(rec['json_file']).read_text(encoding='utf-8'))
  if 'github.com' in repo:
   assert release['tag_name']==tag and not release['prerelease'] and not release['draft']
  else: assert release['name']==tag
  directory='l1_test_fab/'+name; root=Path('seed_pypi_raw')/directory
  if not root.exists(): subprocess.run(['git','clone','--depth','1','--branch',tag,repo+'.git',str(root)],check=True)
  commit=git(root,'rev-parse','HEAD')
  assert commit==git(root,'rev-parse',tag+'^{commit}')
  assert git(root,'remote','get-url','origin').removesuffix('.git')==repo
  assert not git(root,'status','--porcelain','--untracked-files=no')
  notes=[]
  if name=='salabim': notes.append('最新正式GitHub Release为26.0.1，PyPI为26.0.8；采用Release并从固定提交安装。')
  if name=='surpyval': notes.append('最新正式GitHub Release为v0.18.0，PyPI为0.19.0；采用正式Release。')
  spec=dict(name=name,index=i,directory=directory,source_root=sroot,modules=modules,repository=repo,tag=tag,commit=commit,documentation=docs,description=description,pypi='https://pypi.org/project/'+name+'/',pypi_version=discovery[name]['info']['version'],checked_on='2026-09-17',release_api=api,release_url=release.get('html_url',repo+'/-/tags/'+tag),release_published_at=release.get('published_at',release.get('commit',{}).get('committed_date')),github_prerelease=False,selection_rule='Latest official stable release; SimPy uses official latest stable GitLab tag matching PyPI.',notes=notes)
  payload=build_seed(spec,Path('seed_pypi_raw')); path=RAW/f'{name}_{tag}.json'; path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  specs.append(spec); checks.append(dict(name=name,commit=commit,raw_path=path.as_posix(),raw_sha256=file_sha(path),nums=payload[0]['environment']['nums'],release_evidence=rec,tracked_status=git(root,'status','--porcelain','--untracked-files=no'),submodules=git(root,'submodule','status').splitlines()))
  print(name,tag,payload[0]['environment']['nums'],flush=True)
 (BASE/'production_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (BASE/'research/production_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (webdir/'index.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
