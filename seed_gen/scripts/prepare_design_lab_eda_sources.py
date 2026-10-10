"""Pinned released build, layout and flow candidate pools."""
import json
from pathlib import Path
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW
from seed_gen.scripts.extract_release_python_seeds import build_seed,git


def main():
    web=json.loads((BASE/'research/design_release_web/index.json').read_text(encoding='utf-8'))
    discovery={p['query_name']:p for p in json.loads((BASE/'research/package_discovery.json').read_text(encoding='utf-8'))}
    specs=[]
    for index,(name,repo,tag,directory,module,pypi,description,note) in enumerate([
        ('siliconcompiler','siliconcompiler/siliconcompiler','v0.38.8','siliconcompiler','siliconcompiler','siliconcompiler','SiliconCompiler以Design、Project及Flowgraph管理源文件、工艺、任务依赖、执行状态和质量指标，连接RTL到GDS的外部EDA工具链。','最新官方稳定Release0.38.8；完整RTL到GDS需外部综合/布局布线/工艺库，配置和局部步骤不可冒充整条流。'),
        ('librelane','librelane/librelane','3.0.14','librelane','librelane','librelane','LibreLane提供可配置ASIC RTL到GDS实现流程、步骤和工艺环境，编排Yosys/OpenROAD等外部工具；当前作为SiliconCompiler重叠候选。','最新官方稳定Release3.0.14；不将选择单一主流程理解为全后端已装。'),
        ('fusesoc','olofk/fusesoc','2.4.7','fusesoc','fusesoc','fusesoc','FuseSoC管理CAPI2 IP core、文件集、目标与版本依赖，将可复用IP组合为EDAM供后端构建执行。','最新官方稳定Release2.4.7。'),
        ('edalize','olofk/edalize','v0.6.7','edalize','edalize','edalize','Edalize接收EDAM源文件/参数/目标配置，通过统一flow及tool接口生成并执行仿真、综合和实现工具命令。','最新官方Release0.6.7，PyPI0.6.8差异保留；采用已发布Git源码0.6.7，新Sim flow API优先，旧tool API已弃用。'),
        ('laygo2','niftylab/laygo2','stable-230804','laygo2','laygo2','laygo2','laygo2基于模板、抽象栅格、实例/引脚及布线构建参数化定制IC版图，可导出多种EDA数据格式。','最新官方GitHubRelease stable-230804，其setup.py实际版本0.5.5；PyPI0.6.9差异记录，运行依照发布源码版本0.5.5。'),
        ('ALIGN-analoglayout','ALIGN-analoglayout/ALIGN-public','v1.0','ALIGN-release','align','','ALIGN由晶体管级SPICE网表识别电路结构、产生布局约束和器件原语，并调用布局布线后台生成模拟版图。','最新官方GitHubRelease v1.0，源码__version__0.9.8；同名PyPI align是无关语言学包，链接留空。Windows测试gold文件含<>非法路径，使用WSL Git生成仅align/docs/examples/pdks/bin的稀疏工作树，全部align运行库保留且git tracked clean；未修改源文件。'),
    ],361):
        root=Path('seed_pypi_raw/l1_design_lab')/directory
        assert git(root,'remote','get-url','origin')=='https://github.com/'+repo+'.git'
        commit=git(root,'rev-parse','HEAD')
        assert commit==git(root,'rev-parse',tag+'^{commit}')
        rr=next(r for r in web if r['url']=='https://api.github.com/repos/'+repo+'/releases/latest')
        release=json.loads(Path(rr['json_file']).read_text(encoding='utf-8'))
        assert release['tag_name']==tag and not release['prerelease']
        spec=dict(name=name,index=index,directory='l1_design_lab/'+directory,source_root='.',modules=[module],repository='https://github.com/'+repo,tag=tag,commit=commit,documentation='https://github.com/'+repo+'/tree/'+tag,description=description,pypi='https://pypi.org/project/'+pypi+'/' if pypi else '',pypi_version=discovery.get(pypi,{}).get('info',{}).get('version',''),checked_on='2026-09-17',release_api=rr['url'],release_url=release['html_url'],release_published_at=release['published_at'],github_prerelease=False,include_call_protocol=True,notes=[note,'全量候选只由固定源声明提取；运行时/商业后端验证限制单独保留，不推断所有候选都已执行。'])
        payload=build_seed(spec,Path('seed_pypi_raw'))
        (RAW/f'{name}_{tag}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec)
        (BASE/'eda_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(name,payload[0]['environment']['nums'],flush=True)


if __name__=='__main__':
    main()
