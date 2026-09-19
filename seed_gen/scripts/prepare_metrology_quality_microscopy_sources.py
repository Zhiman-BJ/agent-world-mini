"""Clone/re-extract reviewed latest stable microscopy release sources in owned paths."""
import argparse
import json
import subprocess
from pathlib import Path
from seed_gen.scripts.build_joint_scenario_seeds import read,require,file_sha
from seed_gen.scripts.extract_release_python_seeds import build_seed,git

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality'); RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_metrology_quality')
DEFINITIONS=[
 ('RosettaSciIO','hyperspy/rosettasciio','v0.14.0','.', ['rsciio'],'https://hyperspy.org/rosettasciio/', 'RosettaSciIO提供多种显微镜、谱学和科学图像文件格式的读写，将信号数据、轴和实验元数据表示为可桥接HyperSpy的结构；各格式支持与元数据损失需单独检查。'),
 ('HyperSpy','hyperspy/hyperspy','v2.4.0','.', ['hyperspy'],'https://hyperspy.org/hyperspy-doc/current/', 'HyperSpy以多维信号、导航/信号轴和实验元数据组织显微/谱像数据，提供裁剪、配准、滤波、分解与模型分析，并通过扩展包加入领域功能。'),
 ('LiberTEM','LiberTEM/LiberTEM','v0.16.0','src', ['libertem'],'https://libertem.github.io/LiberTEM/', 'LiberTEM提供多维STEM探测器数据分块读取、任务执行和虚拟探测/质心等UDF分析，支持本地与分布式后端；固定小样例不能代替TB吞吐量验证。'),
 ('abTEM','abTEM/abTEM','v1.0.10','.', ['abtem'],'https://abtem.github.io/doc/', 'abTEM从原子结构和势构建透射电子显微镜多重散射、探针及探测器模拟，支持生成衍射/扫描图像并进行物理参数控制；模拟近似和数值网格需验证。'),
 ('kikuchipy','pyxem/kikuchipy','v0.13.1','src', ['kikuchipy'],'https://kikuchipy.org/en/stable/', 'kikuchipy基于HyperSpy处理EBSD花样、探测器几何、背景校正、字典匹配和晶体取向结果，提供数据/几何/索引的可追溯流程。'),
 ('pyxem','pyxem/pyxem','v0.21.0','.', ['pyxem'],'https://pyxem.readthedocs.io/en/stable/', 'pyxem在HyperSpy信号上提供电子衍射、4D数据、虚拟成像、衍射矢量、峰及取向/应变分析；导航轴与倒易像素尺度决定结果物理意义。'),
 ('py4DSTEM','py4dstem/py4DSTEM','v0.14.8','.', ['py4DSTEM'],'https://py4dstem.readthedocs.io/en/latest/', 'py4DSTEM围绕扫描位置和二维衍射像素组成的DataCube提供4D-STEM校准、虚拟探测、布拉格盘和应变/相位分析；本轮固定正式发布版并显式记录PyPI版本差异。'),
 ('eXSpy','hyperspy/exspy','v0.3.2','.', ['exspy'],'https://hyperspy.org/exspy/', 'eXSpy扩展HyperSpy的EDS/EELS信号、X射线谱线、背景、吸收边和定量分析模型，支持由谱像到组分图；物理标定与截面/厚度假设不能忽略。'),
 ('pyFAI','silx-kit/pyFAI','v2026.09','src', ['pyFAI'],'http://www.silx.org/doc/pyFAI/latest/', 'pyFAI从二维探测器图像及几何标定进行方位积分，输出一维/二维散射强度、单位轴和掩膜校正，支持XRD衍射数据处理与误差模型。'),
 ('xrayutilities','dkriegner/xrayutilities','v1.8.0','lib', ['xrayutilities'],'https://xrayutilities.sourceforge.io/', 'xrayutilities提供X射线衍射几何、倒易空间变换、材料晶格和薄膜XRR/XRD模拟/拟合，支持由角度测量到结构参数的可解释链。'),
 ('diffpy.structure','diffpy/diffpy.structure','3.5.0','src', ['diffpy.structure'],'https://www.diffpy.org/diffpy.structure/', 'diffpy.structure提供原子、晶格、晶体结构和结构文件读写，用于衍射参考和结构数据的规范表示；计算几何不等于完成散射谱反演。'),
 ('scikit-image','scikit-image/scikit-image','v0.26.0','src', ['skimage'],'https://scikit-image.org/docs/stable/', 'scikit-image提供科学图像滤波、分割、配准、区域测量和形态学工具，作为显微信号环境的必要图像能力补充，需维护像素尺度与坐标约定。'),
 ('orix','pyxem/orix','v0.15.0','.', ['orix'],'https://orix.readthedocs.io/en/stable/', 'orix提供晶体取向、对称性、相与CrystalMap表示和读写，作为kikuchipy字典取向及索引结果容器，避免把取向元数据藏入未搜集依赖。'),
]

def main():
    p=argparse.ArgumentParser();p.add_argument('--only',nargs='*');a=p.parse_args()
    web={r['url']:r for path in sorted((BASE/'research').glob('*web/index.json')) for r in read(path) if r.get('status')==200}
    discovery={r['query_name']:r for r in read(BASE/'research/package_discovery.json')}
    manifest=BASE/'microscopy_sources.json';specs=read(manifest) if manifest.exists() else []
    checks_path=BASE/'research/microscopy_source_checks.json';checks=read(checks_path) if checks_path.exists() else []
    for idx,(name,repo,tag,source_root,modules,docs,description) in enumerate(DEFINITIONS,750):
        if a.only and name not in a.only:continue
        api=f'https://api.github.com/repos/{repo}/releases/latest';release=read(web[api]['json_file'])
        require(release['tag_name']==tag and not release['prerelease'] and not release['draft'],'Unreviewed release')
        directory='l1_metrology_quality/'+name;root=Path('seed_pypi_raw')/directory
        if not root.exists():subprocess.run(['git','clone','--depth','1','--branch',tag,'https://github.com/'+repo+'.git',str(root)],check=True)
        require(git(root,'remote','get-url','origin').removesuffix('.git').lower()==('https://github.com/'+repo).lower(),'Repository mismatch')
        require(not git(root,'status','--porcelain'),'Source changed')
        require(all((root/source_root/m.replace('.', '/')).exists() for m in modules),'Review module layout')
        notes=[]
        if name=='py4DSTEM':notes=['最新GitHub正式Release v0.14.8；PyPI0.14.18/在线文档0.14.14，按指南固定正式Release，签名和运行使用0.14.8。']
        if name=='pyFAI':notes=['HTTPS文档证书链检查失败，未关闭验证；官方HTTP站200可读并保留原始URL。tag v2026.09对应PyPI2026.9.0。']
        if name=='LiberTEM':notes=['在线首页0.17.0.dev0领先正式版0.16.0，源码签名与运行固定0.16.0。']
        spec=dict(name=name,index=idx,directory=directory,source_root=source_root,modules=modules,repository='https://github.com/'+repo,
            tag=tag,commit=git(root,'rev-parse','HEAD'),documentation=docs,description=description,pypi=f'https://pypi.org/project/{name}/',
            pypi_version='0.15.0' if name=='orix' else discovery[name]['info']['version'],checked_on='2026-09-17',release_api=api,release_url=release['html_url'],
            release_published_at=release['published_at'],github_prerelease=False,notes=notes)
        payload=build_seed(spec,Path('seed_pypi_raw'));dest=RAW/f'{name}_{tag}.json';dest.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs=[s for s in specs if s['name']!=name]+[spec]
        checks=[s for s in checks if s['package']!=name]+[dict(package=name,release_evidence=web[api],commit=spec['commit'],raw_path=dest.as_posix(),raw_sha256=file_sha(dest),nums=payload[0]['environment']['nums'],tracked_status=git(root,'status','--porcelain'),submodules=git(root,'submodule','status').splitlines())]
        manifest.write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');checks_path.write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(name,tag,payload[0]['environment']['nums'],flush=True)
if __name__=='__main__':main()
