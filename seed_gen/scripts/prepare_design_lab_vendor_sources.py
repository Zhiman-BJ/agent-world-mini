"""Pin vendor releases without GitHub Releases using tags or verified sdists."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import requests
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW
from seed_gen.scripts.extract_release_python_seeds import build_seed,git


def main():
    discovery={r['query_name']:r for r in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
    specs=[]
    for index,(name,repo,tag,module,docs,description) in enumerate([
        ('RsInstrument','Rohde-Schwarz/RsInstrument',None,'RsInstrument','https://rsinstrument.readthedocs.io/','RsInstrument封装Rohde & Schwarz SCPI仪器的VISA或Socket通信，提供同步查询、状态错误处理、二进制数据、日志和模拟模式；需要设备型号与协议匹配。'),
        ('laboneq','zhinst/laboneq','26.7.0','laboneq','https://docs.zhinst.com/labone_q_user_manual/','LabOne Q提供脉冲实验描述、设备信号映射、校准、编译和仿真/执行工作流，面向量子与低温实验控制；真实执行需Zurich Instruments硬件和数据服务。'),
        ('zhinst-toolkit','zhinst/zhinst-toolkit','v1.4.0','zhinst','https://docs.zhinst.com/zhinst-toolkit/en/latest/','zhinst-toolkit提供Zurich Instruments仪器节点、波形、命令表和模块的高层Python接口，可补充LabOne Q设备控制；离线波形对象不等于真实硬件校准。'),
    ],321):
        root=Path('seed_pypi_raw/l1_design_lab')/name
        if not root.exists():
            command=['git','clone','--depth','1']+(['--branch',tag] if tag else [])+['https://github.com/'+repo+'.git',str(root)]
            subprocess.run(command,check=True)
        commit=git(root,'rev-parse','HEAD')
        roots=[p for p in ['.','src','src/python'] if (root/p/module).is_dir()]
        assert len(roots)==1,(name,roots)
        info=discovery[name]
        notes=['官方GitHub无Release；采用与PyPI稳定版本一致的官方tag，不取最新beta。']
        extra={}
        if name=='RsInstrument':
            release=requests.get('https://pypi.org/pypi/RsInstrument/json',timeout=30).json()
            assert release['info']['version']=='1.131.0'
            source=next(f for f in release['urls'] if f['packagetype']=='sdist')
            archive=requests.get(source['url'],timeout=60).content
            assert hashlib.sha256(archive).hexdigest()==source['digests']['sha256']
            comparisons=[]
            with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tf:
                for member in tf.getmembers():
                    parts=Path(member.name).parts
                    if len(parts)<3 or parts[1]!='RsInstrument' or not member.name.endswith('.py'):
                        continue
                    rel=Path(*parts[1:])
                    published=tf.extractfile(member).read()
                    checkout=(root/rel).read_bytes()
                    assert published.replace(b'\r\n',b'\n')==checkout.replace(b'\r\n',b'\n'),rel
                    comparisons.append(dict(path=rel.as_posix(),pypi_sha256=hashlib.sha256(published).hexdigest(),checkout_sha256=hashlib.sha256(checkout).hexdigest()))
            assert {r['path'] for r in comparisons}=={p.relative_to(root).as_posix() for p in (root/'RsInstrument').rglob('*.py')}
            evidence=dict(package=name,version='1.131.0',commit=commit,pypi_sdist_url=source['url'],sdist_sha256=source['digests']['sha256'],comparison='all package Python files equal after CRLF/LF normalization',files=comparisons)
            (BASE/'research/rsinstrument_release_equivalence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            tag='1.131.0'
            extra={'ref':commit}
            notes=['官方仓库无tag/Release且单次提交；不能用默认分支当版本。下载PyPI1.131.0正式sdist验证SHA256并逐个比较所有包内Python文件，与固定提交完全相同（仅规范化CRLF/LF），证据research/rsinstrument_release_equivalence.json。', 'tag字段记录PyPI正式分发版本1.131.0，release_ref为实际完整提交；未伪造Git标签。']
        spec=dict(name=name,index=index,directory='l1_design_lab/'+name,source_root=roots[0],modules=[module],repository='https://github.com/'+repo,
                  tag=tag,commit=commit,documentation=docs,description=description,pypi=f'https://pypi.org/project/{name}/',pypi_version=info['info']['version'],
                  checked_on='2026-09-17',release_api='https://api.github.com/repos/'+repo+'/releases/latest',
                  release_url=f'https://pypi.org/project/{name}/{info["info"]["version"]}/',release_published_at=min(f['upload_time_iso_8601'] for f in info['latest_files']),
                  github_prerelease=False,notes=notes,selection_rule='Stable official PyPI release matched to official source tag, or all package Python files matched to verified PyPI sdist.',**extra)
        if name=='zhinst-toolkit':
            spec['modules']=['zhinst.toolkit']
        if name=='laboneq':
            spec['notes'].extend(['Python候选池覆盖src/python/laboneq；src/rust中的私有编译器实现不作为独立Agent公开API，运行由Session.compile实际调用安装wheel中的Rust后端。', 'pulse_library中被register_pulse_functional装饰的函数源码为sampler，运行时为factory，AST签名不等同公开工厂签名；此场景不选这些动态入口，使用真实attrs生成的PulseFunctional/PulseSampled构造并记录字段。'])
        payload=build_seed(spec,Path('seed_pypi_raw'))
        (RAW/f'{name}_{tag}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec)
        (BASE/'vendor_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(name,commit,payload[0]['environment']['nums'],flush=True)


if __name__=='__main__':
    main()
