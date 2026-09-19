"""Official released cocotb/UVM/protocol candidates."""
import json
from pathlib import Path
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW
from seed_gen.scripts.extract_release_python_seeds import build_seed,git


def main():
    web=json.loads((BASE/'research/design_release_web/index.json').read_text(encoding='utf-8'))
    discovery={p['query_name']:p for p in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
    specs=[]
    for index,(name,repo,tag,directory,source_root,module,description) in enumerate([
        ('cocotb','cocotb/cocotb','v2.1.0','cocotb','src','cocotb','cocotb通过Python协程驱动真实HDL仿真器，提供时钟、触发器、信号访问、并发测试和断言，可构建可重放的硬件验证任务。'),
        ('pyuvm','pyuvm/pyuvm','5.0.0','pyuvm','src','pyuvm','pyuvm在cocotb基础上提供UVM式组件、sequence/driver/monitor、配置数据库、TLM连接和scoreboard，用于可复用事务级验证环境。'),
        ('cocotbext-axi','alexforencich/cocotbext-axi','v0.1.28','cocotbext-axi','.','cocotbext.axi','cocotbext-axi提供AXI、AXI-Lite、AXI-Stream及APB的主从、存储器和监视模型，支持突发、背压及事务结果检查。'),
        ('cocotb-bus','cocotb/cocotb-bus','v0.3.0','cocotb-bus','src','cocotb_bus','cocotb-bus提供cocotb信号总线、驱动、监视器和scoreboard基础类，是协议扩展的基础设施；避免与专用AXI事务入口重复。'),
    ],351):
        root=Path('seed_pypi_raw/l1_design_lab')/directory
        assert git(root,'remote','get-url','origin')=='https://github.com/'+repo+'.git'
        commit=git(root,'rev-parse','HEAD')
        assert commit==git(root,'rev-parse',tag+'^{commit}')
        release_record=next(r for r in web if r['url']=='https://api.github.com/repos/'+repo+'/releases/latest')
        release=json.loads(Path(release_record['json_file']).read_text(encoding='utf-8'))
        assert release['tag_name']==tag and not release['prerelease']
        spec=dict(name=name,index=index,directory='l1_design_lab/'+directory,source_root=source_root,modules=[module],repository='https://github.com/'+repo,tag=tag,commit=commit,documentation='https://github.com/'+repo+'/tree/'+tag,description=description,pypi='https://pypi.org/project/'+name+'/',pypi_version=discovery[name]['info']['version'],checked_on='2026-09-17',release_api=release_record['url'],release_url=release['html_url'],release_published_at=release['published_at'],github_prerelease=False,include_call_protocol=True,notes=['官方latest非预发布Release，原始源码/完整候选固定tag；场景联合筛选并裁剪具体类方法。','Python源层全量提取；cocotb仿真器原生GPI扩展为基础设施，由真实Icarus任务验证其连接，不把C++内部导出伪造为Python源方法。'])
        if name=='cocotb':
            spec['modules'].append('cocotb_tools')
        payload=build_seed(spec,Path('seed_pypi_raw'))
        (RAW/f'{name}_{tag}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec)
        (BASE/'verification_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(name,payload[0]['environment']['nums'],flush=True)


if __name__=='__main__':
    main()
