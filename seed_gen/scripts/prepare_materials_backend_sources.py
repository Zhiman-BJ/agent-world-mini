"""Pin database and provenance/backend candidates to inspected official releases."""
import json
from pathlib import Path

from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require
from seed_gen.scripts.extract_release_python_seeds import build_seed, git

BASE = Path('seed_gen/scenario_collection')


def main():
    web = {r['url']:r for folder in ('materials_workflow_backend_web','materials_backend_extra_web') for r in read(BASE/'research'/folder/'index.json')}
    discovery = {r['query_name']:r for r in read(BASE/'research/materials_package_discovery.json')}
    definitions = [
        ('mp-api','materialsproject/api','v0.46.5','.',['mp_api'],'https://docs.materialsproject.org/downloading-data/using-the-api',
         'Materials Project官方Python客户端，提供材料检索、结构/能带等属性读取及查询参数验证；真实数据库访问依赖API密钥和网络，固定本地响应只能验证客户端契约。'),
        ('aiida-core','aiidateam/aiida-core','v2.9.2','src',['aiida'],'https://aiida.readthedocs.io/projects/aiida-core/en/stable/',
         'AiiDA以可追溯的数据节点、计算节点和工作流管理计算科学任务，提供本地/远程进程执行、来源图查询、归档和恢复；外部求解器、守护进程与HPC基础设施需分别配置。'),
        ('pyiron-base','pyiron/pyiron_base','pyiron_base-0.15.19','.',['pyiron_base'],'https://pyiron-base.readthedocs.io/',
         'pyiron-base提供项目、作业、参数容器、存储和调度抽象，可组织可恢复的本地或HPC科学计算；具体物理后台由扩展包提供。'),
        ('pyiron-workflow','pyiron/pyiron_workflow','pyiron_workflow-0.20.0','src',['pyiron_workflow'],'https://pyiron-workflow.readthedocs.io/',
         'pyiron-workflow通过节点、输入输出通道与组合工作流描述Python计算依赖，支持执行、缓存及状态序列化；与完整材料求解器的适配需单独验证。'),
        ('pyiron-atomistics','pyiron/pyiron_atomistics','pyiron_atomistics-0.8.12','.',['pyiron_atomistics'],'https://pyiron.readthedocs.io/',
         'pyiron-atomistics将原子结构、计算作业和多种材料模拟后台统一到项目接口，可配置ASE、VASP、LAMMPS等计算及结构/输出分析；外部可执行程序和许可证不随静态API索引提供。'),
        ('pyiron-workflow-atomistics','pyiron/pyiron_workflow_atomistics','pyiron_workflow_atomistics-0.2.1','.',['pyiron_workflow_atomistics'],'https://pyiron_workflow_atomistics.readthedocs.io/',
         'pyiron-workflow-atomistics提供原子计算工作流节点、通用Engine和ASE等后台适配，围绕结构、计算器、输入/输出和优化组合可执行流程；发布版0.2.1硬锁定pyiron-workflow0.19.0，不能与当前0.20.0直接混装。'),
    ]
    specs,checks=[],[]
    for index,(name,repo,tag,source_root,modules,docs,description) in enumerate(definitions,111):
        api='https://api.github.com/repos/'+repo+'/releases/latest'
        evidence=web[api]
        release=read(evidence['json_file'])
        require(evidence['status']==200 and release['tag_name']==tag and not release['prerelease'] and not release['draft'],'Release review required')
        root=Path('seed_pypi_raw')/name
        commit=git(root,'rev-parse','HEAD')
        repository='https://github.com/'+repo
        require(git(root,'remote','get-url','origin').removesuffix('.git')==repository,'Repository mismatch')
        require(git(root,'rev-parse',tag+'^{commit}')==commit,'Tag mismatch')
        spec=dict(name=name,index=index,directory=name,modules=modules,source_root=source_root,tag=tag,commit=commit,
                  repository=repository,documentation=docs,pypi=f'https://pypi.org/project/{name}/',
                  pypi_version=discovery[name]['info']['version'],checked_on='2026-09-17',github_prerelease=False,
                  release_published_at=release['published_at'],release_api=api,release_url=release['html_url'],
                  description=description,notes=['仅表示来源和完整静态候选已核验，未据此声明任务或外部后台验证通过。'])
        payload=build_seed(spec,Path('seed_pypi_raw'))
        raw=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection')/f'{name}_{tag}.json'
        raw.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        specs.append(spec)
        checks.append({'package':name,'commit':commit,'release':evidence,'raw_path':raw.as_posix(),
                       'raw_sha256':file_sha(raw),'nums':payload[0]['environment']['nums'],
                       'tracked_status':git(root,'status','--porcelain','--untracked-files=no')})
        print(name,tag,payload[0]['environment']['nums'],flush=True)
    (BASE/'materials_backend_sources.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'research/materials_backend_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
