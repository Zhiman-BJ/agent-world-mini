"""Pinned official logic/schema package releases and alternatives."""
import json
from pathlib import Path
import subprocess
import requests
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW
from seed_gen.scripts.extract_release_python_seeds import build_seed,git


def main():
    discovery={r['query_name']:r for r in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
    specs=[]
    entries=[
        ('hdl21','dan-fritchman/Hdl21','v6.0.0','hdl21','.','hdl21','https://github.com/dan-fritchman/Hdl21/blob/v6.0.0/readme.md','HDL21以Python对象与参数生成器描述晶体管级原理图、信号、实例和层次连接，支持展开为VLSIR电路模型并通过VLSIRtools生成SPICE网表。','官方无GitHubRelease；最新非开发Git标签6.0.0，与tag内pyproject版本一致。PyPI最新7.0.0但官方标签只有7.0.0.dev，当前选择可证实发布源码6.0.0并固定vlsir/vlsirtools6.0.0，未把main冒充7.0.0发布源码。'),
        ('vlsirtools','Vlsir/Vlsir','v6.0.0','vlsir','VlsirTools','vlsirtools','https://github.com/Vlsir/Vlsir/blob/v6.0.0/VlsirTools/readme.md','VLSIRtools对VLSIR电路和仿真Schema提供网表输出、仿真输入组织与结果解析，连接硬件描述和SPICE类外部求解器；网表生成不等于求解执行。','官方无GitHubRelease；最新稳定Git标签6.0.0，与VlsirTools/setup.py一致；PyPI7.0.0另记，当前与HDL21的6.0.0硬依赖组合。'),
        ('skidl','devbisme/skidl','2.3.0','skidl','.','skidl','https://devbisme.github.io/skidl/','SKiDL使用Python描述元件、引脚、网络、总线和层次原理图，执行电气规则检查并导出网表；当前晶体管级场景与HDL21互为替代，不重复暴露。','最新官方非预发布Release2.3.0。'),
        ('amaranth','amaranth-lang/amaranth','v0.5.10','amaranth','.','amaranth','https://amaranth-lang.org/docs/amaranth/v0.5.10/','Amaranth用Python描述信号、组合/时序逻辑、接口和FIFO等标准组件，提供真实事件仿真、断言与波形输出，适合可重复的计数器/数据通路验证。','最新官方非预发布Release0.5.10。'),
        ('pymtl3','pymtl/pymtl3','v3.1','pymtl3','.','pymtl3','https://pymtl3.readthedocs.io/','PyMTL3支持Python硬件组件、位向量、接口、RTL仿真及Verilog翻译，提供多层抽象的建模与测试；本场景作为Amaranth的重叠替代候选。','采用最新官方非预发布GitHubRelease v3.1；PyPI最新3.1.17差异保留，未混入其源码。'),
        ('pyrtl','UCSBarchlab/PyRTL','1.0.3','pyrtl','.','pyrtl','https://pyrtl.readthedocs.io/','PyRTL提供Python寄存器传输级线网、仿真、综合、分析和Verilog输出，适合小型数字逻辑验证；当前场景不与Amaranth重复暴露同类操作。','无GitHubRelease；官方稳定tag1.0.3与PyPI1.0.3对应。'),
        ('myhdl','myhdl/myhdl','0.11','myhdl','.','myhdl','https://docs.myhdl.org/','MyHDL通过Python生成器、信号和仿真调度描述数字硬件，并可转换为Verilog/VHDL；作为当前Amaranth方案的替代线索保留。','官方无GitHubRelease；最高稳定版本tag0.11，PyPI维护版0.11.52另记，tag与分发版本不同，未将默认分支作为维护版源码。'),
        ('pyverilog','PyHDI/Pyverilog','1.3.0','pyverilog','.','pyverilog','https://github.com/PyHDI/Pyverilog/tree/1.3.0','PyVerilog提供Verilog词法/语法AST、数据流与控制流分析及代码生成，支持结构检查、依赖追踪和可验证的源码变换；预处理文件流程依赖Icarus。','最新官方非预发布Release1.3.0。'),
        ('volare','efabless/volare','0.20.6','volare','.','volare','https://github.com/efabless/volare/tree/0.20.6','Volare管理open_pdks布局的PDK版本、安装缓存和激活链接，支持下载/构建与版本匹配；本地版本管理验证不代表实际工艺库或流片签核。','latest GitHubRelease是sky130等PDK数据release，非Python包release；采用官方Python版本tag0.20.6并与PyPI核对。仓库当前重定向chipfoundry/volare，保留原官方来源链。'),
    ]
    for index,(name,repo,tag,directory,source_root,module,docs,description,note) in enumerate(entries,341):
        root=Path('seed_pypi_raw/l1_design_lab')/directory
        if name=='skidl': source_root='src'
        assert git(root,'remote','get-url','origin').removesuffix('.git')=='https://github.com/'+repo
        assert git(root,'rev-parse',tag+'^{commit}')==git(root,'rev-parse','HEAD')
        version_record=discovery[name]
        tags=subprocess.run(['git','ls-remote','--tags','https://github.com/'+repo+'.git'],check=True,text=True,encoding='utf-8',capture_output=True).stdout
        evidence={'checked_on':'2026-09-17','name':name,'selected_tag':tag,'commit':git(root,'rev-parse','HEAD'),'official_tags':tags.splitlines(),'pypi_version':version_record['info']['version'],'selection_reason':note}
        (BASE/f'research/{name}_version_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        spec=dict(name=name,index=index,directory='l1_design_lab/'+directory,source_root=source_root,modules=[module],repository='https://github.com/'+repo,tag=tag,commit=evidence['commit'],documentation=docs,description=description,pypi=f'https://pypi.org/project/{name}/',pypi_version=version_record['info']['version'],checked_on='2026-09-17',release_api='https://api.github.com/repos/'+repo+'/releases/latest',release_url='https://github.com/'+repo+'/tree/'+tag,release_published_at='',github_prerelease=False,notes=[note,'固定标签候选仅为Python源API；属性按当前提取口径保存，场景会剔除无关内部/冗余方法。'])
        if name=='vlsirtools':
            spec['notes'].append('protobuf生成的VLSIR消息是外部基础Schema数据对象；当前全量索引为vlsirtools包公开源类/函数，序列化Schema字段不伪造为可调用方法。')
        if name=='hdl21':
            spec['include_call_protocol']=True
            spec['notes'].append('保留源代码明确定义的__call__协议以闭合ExternalModule/Generator参数调用；装饰器动态生成的实例调用只记入场景基础设施，不伪造源方法。')
        if name=='myhdl':
            spec['modules']=['.'.join(p.relative_to(root).with_suffix('').parts) for p in sorted((root/'myhdl').rglob('*.py')) if not {'test','tests','__pycache__'}.intersection(p.relative_to(root).parts)]
            spec['notes'].append('逐文件枚举完整运行库，排除包内test/tests：含故意无效Python2反引号语法的负例，不是库公开API；未改源码或忽略运行库语法错误。')
        payload=build_seed(spec,Path('seed_pypi_raw'))
        (RAW/f'{name}_{tag}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec)
        (BASE/'logic_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(name,payload[0]['environment']['nums'],flush=True)


if __name__=='__main__':
    main()
