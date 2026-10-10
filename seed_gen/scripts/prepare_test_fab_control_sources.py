"""Freeze official equipment/control/optimization releases and full source pools."""
import json
from pathlib import Path
import subprocess
from seed_gen.scripts.collect_scenario_web_evidence import fetch
from seed_gen.scripts.extract_release_python_seeds import build_seed,git
from seed_gen.scripts.build_joint_scenario_seeds import file_sha
from seed_gen.scripts.prepare_test_fab_sources import BASE,RAW

DEFS=[('secsgem','bparzella/secsgem','v0.3.0','.',['secsgem'],'https://secsgem.readthedocs.io/en/latest/',
       'secsgem实现SECS-II数据项、消息、HSMS连接及GEM主机/设备协议，可用于固定本地消息或loopback交互；真实机台接入及SEMI一致性需另行验证。'),
      ('asyncua','FreeOpcUa/opcua-asyncio','v2.0.1','.',['asyncua'],'https://opcua-asyncio.readthedocs.io/en/latest/',
       'asyncua提供异步OPC UA服务端、客户端、节点/属性和订阅，可构造本地设备变量模型并验证读取、写入及类型边界；不冒称实际PLC连通。'),
      ('control','python-control/python-control','0.10.2','.',['control'],'https://python-control.readthedocs.io/en/latest/',
       'python-control提供线性系统、传递函数、状态空间、反馈和时域响应，可表达工艺动态与离散run-to-run控制；工艺参数与扰动由固定模型给定。'),
      ('do-mpc','do-mpc/do-mpc','v5.1.2','.',['do_mpc'],'https://www.do-mpc.com/en/latest/',
       'do-mpc结合CasADi构建动态模型、约束MPC、模拟器和移动窗估计，支持明确状态/输入/参数的控制与观测生命周期。'),
      ('ortools','google/or-tools','v9.15','.',['ortools'],'https://developers.google.com/optimization',
       'OR-Tools的CP-SAT Python接口提供变量、线性/布尔/区间约束和整数优化，可统一表示资源日历、作业及排程；原生求解器通过公开包装器调用。'),
      ('pyomo','Pyomo/pyomo','6.10.1','.',['pyomo'],'https://pyomo.readthedocs.io/en/stable/',
       'Pyomo提供代数优化模型、变量、目标和约束以及求解器接口，是通用生产优化另一建模路线；选择时比较求解器安装与任务范围，避免重复暴露。')]

def main():
    webdir=BASE/'research/control_source_web';webdir.mkdir(parents=True,exist_ok=True);RAW.mkdir(parents=True,exist_ok=True)
    specs=[];checks=[];evidence=[]
    for idx,(name,slug,tag,sroot,modules,docs,desc) in enumerate(DEFS,571):
        repo='https://github.com/'+slug;api='https://api.github.com/repos/'+slug+'/releases/latest';rec=fetch(api,webdir);evidence.append(rec)
        assert rec['status']==200;release=json.loads(Path(rec['json_file']).read_text(encoding='utf-8'));assert release['tag_name']==tag and not release['prerelease']
        pyrec=fetch('https://pypi.org/pypi/'+name+'/json',webdir);evidence.append(pyrec);assert pyrec['status']==200;pydata=json.loads(Path(pyrec['json_file']).read_text(encoding='utf-8'))
        directory='l1_test_fab/'+name;root=Path('seed_pypi_raw')/directory
        if not root.exists():subprocess.run(['git','clone','--depth','1','--branch',tag,repo+'.git',str(root)],check=True)
        commit=git(root,'rev-parse','HEAD');assert commit==git(root,'rev-parse',tag+'^{commit}') and not git(root,'status','--porcelain','--untracked-files=no')
        assert git(root,'remote','get-url','origin').removesuffix('.git')==repo
        spec=dict(name=name,index=idx,directory=directory,source_root=sroot,modules=modules,repository=repo,tag=tag,commit=commit,documentation=docs,description=desc,pypi='https://pypi.org/project/'+name+'/',pypi_version=pydata['info']['version'],checked_on='2026-09-17',release_api=api,release_url=release['html_url'],release_published_at=release['published_at'],github_prerelease=False,selection_rule='Latest official stable GitHub Release',notes=[])
        if name=='ortools':spec['notes']=['完整解析发布仓库ortools下Python源码；原生C++及构建期导出不冒称已完整反射。']
        payload=build_seed(spec,Path('seed_pypi_raw'));path=RAW/f'{name}_{tag}.json';path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec);checks.append(dict(name=name,commit=commit,raw_path=path.as_posix(),raw_sha256=file_sha(path),nums=payload[0]['environment']['nums'],release_evidence=rec,tracked_status=git(root,'status','--porcelain','--untracked-files=no'),submodules=git(root,'submodule','status').splitlines()))
        (BASE/'control_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');(BASE/'research/control_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');(webdir/'index.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(name,tag,payload[0]['environment']['nums'],flush=True)

if __name__=='__main__':main()
