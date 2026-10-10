"""Full released sources for trace/SPC/test analysis; no per-package pruning."""
import json
from pathlib import Path
import subprocess
from seed_gen.scripts.collect_scenario_web_evidence import fetch
from seed_gen.scripts.extract_release_python_seeds import build_seed, git
from seed_gen.scripts.build_joint_scenario_seeds import file_sha
from seed_gen.scripts.prepare_test_fab_sources import BASE,RAW

DEFS=[
 ('tsfresh','blue-yonder/tsfresh','v0.21.2','.','tsfresh','https://tsfresh.readthedocs.io/en/latest/','tsfresh从带对象ID和时间戳的轨迹提取统计、频域及相关特征，并提供特征筛选和缺失处理工具；适合FDC波形到表格特征桥接。'),
 ('ruptures','deepcharles/ruptures','v1.1.10','src','ruptures','https://centre-borelli.github.io/ruptures-docs/','ruptures提供离线时间序列变点检测，支持不同代价函数、动态规划、PELT和窗口搜索；检测结果是分段边界，不自动等于工艺故障根因。'),
 ('sktime','sktime/sktime','v1.1.0','.','sktime','https://www.sktime.net/en/stable/','sktime提供统一的时间序列分类、预测、检测和变换接口；本轮按具体场景与专用特征/变点工具比较，避免重复状态与转换层。'),
 ('adtk','arundo/adtk','v0.6.2','src','adtk','https://adtk.readthedocs.io/en/stable/','ADTK提供基于规则、统计阈值和时间序列变换的异常检测器及管道，可表示工艺轨迹偏离；旧发布版的pandas依赖兼容需单独验证。'),
 ('river','online-ml/river','0.26.1','.','river','https://riverml.xyz/latest/','River提供逐条更新的在线统计、漂移检测、异常检测和学习模型；更新顺序、冷启动和reset是流式制造监测状态的关键。'),
 ('spc-lib','denccchick/spc-lib','1.0.0','src','spc_lib','https://denccchick.github.io/spc-lib/','spc-lib提供统计过程控制图、规则和能力分析，可用于固定基线下的过程异常与Cp/Cpk核查；发布版身份和包布局按官方源码确认。'),
 ('pyspc','carlosqsilva/pyspc','v0.4','.','pyspc','https://github.com/carlosqsilva/pyspc','pyspc提供控制图和规则绘制的统计过程控制工具，作为SPC候选与spc-lib比较；采用与PyPI0.4对应的官方标签。'),
 ('scikit-learn','scikit-learn/scikit-learn','1.9.1','.','sklearn','https://scikit-learn.org/stable/','scikit-learn提供预处理、聚类、异常检测、分类和评价接口；场景中仅暴露与固定晶圆/轨迹特征任务有关的估计器和数据划分，训练与验收数据必须分离。'),
 ('scipy','scipy/scipy','v1.18.1','.','scipy','https://docs.scipy.org/doc/scipy/','SciPy提供数值统计、信号与科学计算工具，作为测试限值和过程统计的必要补充；静态Python索引不冒充全部原生扩展导出。'),
 ('pandas','pandas-dev/pandas','v3.0.5','.','pandas','https://pandas.pydata.org/docs/','pandas提供带标签的表格、连接、分组、读写与时间序列处理，可承载lot/wafer/die/test及设备轨迹的规范数据层；需显式维护主键、单位和缺失约定。'),
]

def main():
    webdir=BASE/'research/statistics_source_web';webdir.mkdir(parents=True,exist_ok=True);RAW.mkdir(parents=True,exist_ok=True)
    discovery={r['query_name']:r for r in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
    specs=[];checks=[];evidence=[]
    for idx,(name,slug,tag,sroot,module,docs,desc) in enumerate(DEFS,521):
        repo='https://github.com/'+slug
        api='https://api.github.com/repos/'+slug+('/tags' if name=='pyspc' else '/releases/latest')
        rec=fetch(api,webdir);evidence.append(rec);assert rec['status']==200,(api,rec)
        release=json.loads(Path(rec['json_file']).read_text(encoding='utf-8'))
        if name=='pyspc':
            assert any(r['name']==tag for r in release);published=None
        else:
            assert release['tag_name']==tag and not release['prerelease'] and not release['draft'];published=release['published_at']
        directory='l1_test_fab/'+name;root=Path('seed_pypi_raw')/directory
        if not root.exists():subprocess.run(['git','clone','--depth','1','--branch',tag,repo+'.git',str(root)],check=True)
        commit=git(root,'rev-parse','HEAD');assert commit==git(root,'rev-parse',tag+'^{commit}')
        assert git(root,'remote','get-url','origin').removesuffix('.git')==repo
        assert not git(root,'status','--porcelain','--untracked-files=no')
        notes=[]
        if name=='pyspc':notes.append('官方无GitHub Release；标签v0.4与PyPI0.4及源码版本对应。')
        if name=='spc-lib':notes.append('官方最新GitHub Release1.0.0；PyPI1.1.2，按指南使用正式Release。')
        spec=dict(name=name,index=idx,directory=directory,source_root=sroot,modules=[module],repository=repo,tag=tag,commit=commit,documentation=docs,description=desc,pypi='https://pypi.org/project/'+name+'/',pypi_version=discovery[name]['info']['version'],checked_on='2026-09-17',release_api=api,release_url=repo+('/tree/' if name=='pyspc' else '/releases/tag/')+tag,release_published_at=published,github_prerelease=False,selection_rule='Latest official stable GitHub release; pyspc official v0.4 tag matches stable distribution.',notes=notes)
        if name=='spc-lib':
            spec['module_prefix']='src'
            spec['notes'].append('发布1.0.0内部绝对导入src.spc_lib，pip import spc_lib报ModuleNotFoundError。运行显式把未修改发布仓库根加入sys.path并使用src.spc_lib，索引模块同步记录真实可运行namespace；不是透明安装可用。')
        payload=build_seed(spec,Path('seed_pypi_raw'));path=RAW/f'{name}_{tag}.json';path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec);checks.append(dict(name=name,commit=commit,raw_path=path.as_posix(),raw_sha256=file_sha(path),nums=payload[0]['environment']['nums'],release_evidence=rec,tracked_status=git(root,'status','--porcelain','--untracked-files=no'),submodules=git(root,'submodule','status').splitlines()))
        # Save completed provenance after each package so an interrupted large clone is resumable.
        (BASE/'statistics_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (BASE/'research/statistics_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (webdir/'index.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(name,tag,payload[0]['environment']['nums'],flush=True)

if __name__=='__main__':main()
