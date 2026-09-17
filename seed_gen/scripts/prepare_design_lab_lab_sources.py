"""Collect the reviewed stable releases for laboratory scenarios."""
import json
from pathlib import Path
import subprocess
from seed_gen.scripts.prepare_design_lab_sources import BASE, RAW
from seed_gen.scripts.extract_release_python_seeds import build_seed, git


def main():
    discovery = {r['query_name']:r for r in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
    web = {r['url']:r for r in json.loads((BASE/'research/lab_web/index.json').read_text(encoding='utf-8'))}
    definitions = [
        ('qcodes','microsoft/Qcodes','v0.59.0','qcodes','https://microsoft.github.io/Qcodes/','QCoDeS是实验仪器控制和数据采集框架，以仪器、参数、验证器、测量运行和数据集组织可重复实验，包含半导体参数分析仪等驱动；真实硬件和虚拟DUT需明确区分。'),
        ('pymeasure','pymeasure/pymeasure','v0.16.0','pymeasure','https://pymeasure.readthedocs.io/','PyMeasure提供仪器驱动、适配器、实验Procedure与结果管理，支持自动化测量和虚拟仪器；与QCoDeS部分职责重叠。'),
        ('pyvisa','pyvisa/pyvisa','1.16.2','pyvisa','https://pyvisa.readthedocs.io/en/latest/','PyVISA提供VISA资源管理、消息及寄存器资源接口，统一USB/GPIB/串口/TCPIP等仪器通信，依赖指定的VISA后端。'),
        ('pyvisa-py','pyvisa/pyvisa-py','0.8.1','pyvisa_py','https://pyvisa-py.readthedocs.io/en/latest/','PyVISA-py实现纯Python VISA后端，通过TCPIP、串口或可选USB/GPIB依赖与仪器通信；应用层优先使用PyVISA资源接口。'),
        ('pyvisa-sim','pyvisa/pyvisa-sim','0.7.0','pyvisa_sim','https://pyvisa-sim.readthedocs.io/en/latest/','PyVISA-sim通过YAML对话、属性、终止符和错误行为模拟仪器消息，作为PyVISA后端支持无硬件驱动验证；它不自动提供器件物理模型。'),
        ('qcodes-contrib-drivers','QCoDeS/Qcodes_contrib_drivers','v0.25.0','qcodes_contrib_drivers','https://qcodes.github.io/Qcodes_contrib_drivers/','QCoDeS社区仪器驱动集，扩展SMU、示波器和其他实验设备接口；驱动命令需与型号、固件和实际通信后端匹配。'),
        ('pylablib','AlexShkarin/pyLabLib','v1.4.5','pylablib','https://pylablib.readthedocs.io/','pyLabLib提供实验设备控制、数据处理和采集基础设施，包含相机、示波器、运动控制和SCPI等设备驱动；需要适配真实或明确模拟的后端。'),
    ]
    specs=[]
    RAW.mkdir(parents=True,exist_ok=True)
    for index,(name,repo,tag,module,docs,description) in enumerate(definitions,311):
        api=f'https://api.github.com/repos/{repo}/releases/latest'
        release=json.loads(Path(web[api]['json_file']).read_text(encoding='utf-8'))
        assert release['tag_name']==tag and not release['prerelease'] and not release['draft']
        root=Path('seed_pypi_raw/l1_design_lab')/name
        if not root.exists():
            subprocess.run(['git','clone','--depth','1','--branch',tag,'https://github.com/'+repo+'.git',str(root)],check=True)
        assert git(root,'remote','get-url','origin').removesuffix('.git')=='https://github.com/'+repo
        roots=[d for d in ['.','src'] if (root/d/module/'__init__.py').exists()]
        assert len(roots)==1,(name,roots)
        spec=dict(name=name,index=index,directory='l1_design_lab/'+name,source_root=roots[0],modules=[module],
                  repository='https://github.com/'+repo,tag=tag,commit=git(root,'rev-parse','HEAD'),documentation=docs,
                  description=description,pypi=f'https://pypi.org/project/{name}/',pypi_version=discovery[name]['info']['version'],
                  checked_on='2026-09-17',release_api=api,release_url=release['html_url'],release_published_at=release['published_at'],
                  github_prerelease=False,notes=['网页latest可能领先源码，接口始终从固定tag提取。'])
        if name=='pyvisa-sim':
            spec['notes'].append('GitHub最新正式Release为0.7.0，PyPI0.7.1；按指南固定正式Release0.7.0。')
        if name=='qcodes':
            spec['include_call_protocol']=True
            spec['notes'].append('Parameter的get/set由构造包装产生，显式保留真实ParameterBase.__call__公开协议；不伪造源码get/set定义。')
        payload=build_seed(spec,Path('seed_pypi_raw'))
        (RAW/f'{name}_{tag}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec)
        (BASE/'lab_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(name,payload[0]['environment']['nums'],flush=True)


if __name__=='__main__':
    main()
