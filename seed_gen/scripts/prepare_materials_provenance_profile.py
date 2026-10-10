"""AiiDA provenance selection across full AiiDA and pyiron candidate pools."""
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe
from seed_gen.scripts.prepare_materials_workflow_physics_profiles import entity, PREFIX


def main():
    c=Collection()
    def op(module,name,methods,capability,reason,construction=None):
        return symbol('aiida-core','aiida.'+module,name,methods,capability,reason,construction)
    def source(url,evidence,entities,tools,tasks):
        return c.source(url,evidence,entities.split(),tools.split(),tasks.split())
    c.write({
        'scenario_id':'01.03.03',
        'description':'材料计算溯源与工作流诊断环境：使用AiiDA的数据/计算/工作流节点及有类型的来源边，保存输入身份、查找结果生产者、追踪失败子步骤并以新输入重新执行，保留错误与修复历史。AiiDA负责唯一来源图，pyiron-base/workflow作为另一套替代生态排除。固定SQLite临时环境实际执行两级计算与来源查询；HPC提交、守护进程和外部DFT不属本次已验证运行范围。',
        'packages':[('aiida-core','primary','具备原生来源边、不可变计算输入、节点查询与工作流状态，本地无RabbitMQ/PostgreSQL可验证。'),
                    ('pyiron-base','excluded','项目/作业/存储与当前AiiDA状态和溯源职责重叠；不混入第二数据库。'),
                    ('pyiron-workflow','excluded','另一套Python节点DAG；当前无需跨生态迁移，不加入重复节点和执行状态。')],
        'symbols':[
            op('manage.configuration','load_profile',None,'storage','载入显式隔离profile，不触碰个人配置。'),
            op('manage.manager','get_manager',None,'storage','访问当前测试会话管理器。'),
            op('manage.manager','Manager','get_profile reset_profile get_profile_storage','storage','检查和释放当前会话；不暴露个人配置或远程守护进程控制。','get_manager工厂提供唯一管理器。'),
            op('storage.sqlite_temp.backend','SqliteTempBackend','create_profile close is_closed','storage','创建临时独立数据库并明确关闭生命周期；不暴露SQL批量修改。','load_profile依profile构造后端，Manager.get_profile_storage返回。'),
            op('engine.processes.functions','calcfunction',None,'execution','把可审查的科学数据变换变成有输入/输出来源记录的真实计算。'),
            op('engine.processes.functions','workfunction',None,'execution','组合计算并保存工作流调用关系；返回对象带run_get_node协议。'),
            op('orm.nodes.data.base','BaseType','__init__ value','data','数值节点共同构造与只读值，由Float/Int继承。'),
            op('orm.nodes.data.float','Float',[],'data','带单位约定的能量输入/结果节点具体类型。','继承BaseType.__init__，Float(-10)实际验证。'),
            op('orm.nodes.data.int','Int',[],'data','原子数等整数输入的具体类型。','继承BaseType.__init__，Int(4)实际验证。'),
            op('orm.nodes.data.data','Data','clone source creator','data','新输入修订与来源观察，不修改已存储计算数据。','Float/Int具体数据节点继承，clone创建未存储副本。'),
            op('orm.nodes.node','Node','base uuid node_type process_type label description ctime mtime store store_all','data','身份、标签、存储和基础访问；去掉手工改图与数据库底层操作。','由具体Data/Process节点获得，不直接生成无语义基类节点。'),
            op('orm.nodes.node','NodeBase','attributes extras links','data','节点状态/元数据/来源边的原生访问面。','Node.base返回。'),
            op('orm.nodes.attributes','NodeAttributes','all get get_many keys','data','只读科学属性，不向Agent开放篡改已有计算输入。','Node.base.attributes返回。'),
            op('orm.nodes.links','NodeLinks','get_incoming get_outgoing','lineage','按CREATE/INPUT/CALL等类型追踪，不手工伪造来源边。','Node.base.links返回。'),
            op('orm.utils.links','LinkManager','one all all_nodes all_link_labels','lineage','读取匹配边，明确单一生产者和输入标签。','NodeLinks.get_incoming/get_outgoing返回。'),
            op('orm.nodes.process.process','ProcessNode','process_label process_state process_status is_terminated is_excepted is_finished_ok is_failed exit_status exit_message exception called called_descendants caller','execution','诊断具体失败与父子调用关系；不直接改终态。','真实calcfunction/workfunction执行时自动创建。'),
            op('orm.nodes.process.calculation.calculation','CalculationNode','inputs outputs','lineage','从计算读取具名数据依赖与产物。','calcfunction创建CalcFunctionNode继承该接口。'),
            op('orm.nodes.process.calculation.calcfunction','CalcFunctionNode',[],'lineage','QueryBuilder类型过滤以定位真实计算生产者。','执行选定calcfunction返回；不用手工构造。'),
            op('orm.nodes.process.workflow.workfunction','WorkFunctionNode',[],'lineage','工作流调用树的具体节点类型。','执行选定workfunction返回；不用手工构造。'),
            op('orm.querybuilder','QueryBuilder','__init__ append add_filter add_projection order_by limit distinct first count all one dict as_dict from_dict','query','按节点类型/属性/来源边组合查询，支持小规模分页和可保存查询。'),
            op('orm.utils.loaders','load_node',None,'query','按uuid/pk恢复已存储节点身份。'),
            op('orm.groups','Group','__init__ store uuid label description nodes add_nodes remove_nodes count','audit','将正常/错误/修复节点归入审计组，删除组成员不删除节点历史。'),
        ],
        'sources':[
            source('https://aiida.readthedocs.io/projects/aiida-core/en/stable/howto/query.html','官方QueryBuilder教程按类型、过滤/投影和来源连接查询科学数据，结果可以来自父计算或下游节点。','node source_link query','QueryBuilder append all','找出数据的实际生产者'),
            source('https://aiida.readthedocs.io/projects/aiida-core/en/stable/howto/run_workflows.html','多步工作流教程通过calcfunction组合并记录WorkFunctionNode，run_get_node同时得到结果和工作流节点。','workflow calculation input output','calcfunction workfunction','执行后追踪两级计算及错误输入'),
            source('https://aiida.readthedocs.io/projects/aiida-core/en/stable/topics/storage.html','官方明确core.sqlite_temp为演示/测试的内存SQLite加sandbox，卸载后销毁；不同于生产PostgreSQL与持久SQLite。','profile storage lifetime','SqliteTempBackend.create_profile load_profile','隔离固定样例与重置'),
        ],
        'entities':[entity('scientific_input','uuid type value unit revision source','创建未存储输入→检查→存储冻结；修复创建新节点而非改历史。'),entity('calculation_node','uuid process_label state exception inputs outputs caller','执行→成功或异常→定位问题→以新输入重跑；错误节点继续保留。'),entity('workflow_node','uuid called_calculations input_ids result_id state','组合执行→读取来源边→审计；本地同步执行，不冒称HPC恢复。'),entity('audit_group','uuid members task_id expected_value units','收集正常/错误/修复记录→查询；reset创建新临时profile。')],
        'capabilities':['storage','data','execution','lineage','query','audit'],
        'bridges':[{'from':'AiiDA Data nodes','to':'calcfunction -> workfunction -> QueryBuilder','contract':'CREATE边标唯一数据生产者，INPUT_CALC与CALL_CALC区分数据依赖和执行关系；energy为eV、correction为eV/atom、count为正整数。'}],
        'runtime_infrastructure':[
            {'reference':'runtime.corrected_energy_workflow','kind':'derived_callable','derived_from':'aiida.engine.processes.functions.workfunction','reason':'被选workfunction装饰器返回的可调用及run_get_node协议；调用两次被选calcfunction，不伪造Python顶层API。'},
            {'reference':'runtime.energy_transform','kind':'derived_callable','derived_from':'aiida.engine.processes.functions.calcfunction','reason':'固定科学载荷为已给能量归一与加修正；计算器不执行DFT。'},
            {'reference':'runtime.provenance_oracle','reason':'手算-10/4+.25=-2.25，与图数据库无关；使用LinkType枚举、JSON记录UUID和单位，不手工插入来源边。'}],
        'boundaries':['本地SQLite临时profile、同步执行、来源查询已实跑；无RabbitMQ、远程队列、HPC账号、DFT求解或checkpoint继续执行。','恢复指根据异常节点的实际输入定位后以新输入重跑，并保留历史；不宣称远程进程原地续跑。','临时数据库随测试关闭销毁；JSON UUID为本次证据，不能当持久线上链接。','首次导入自动创建个人.aiida空子目录，清理动作被自动审批拒绝，保留不动；正式脚本在import前设置项目内AIIDA_PATH。'],
        'tasks':[
            recipe('trace_calculation_lineage','运行总能归一与显式修正的两级工作流，从最终能量反查实际生产者及输入节点，核验两条计算调用边、输入标签和-2.25eV/atom固定目标；将过程与结果归入审计组。','输入-10eV、4原子、修正.25eV/atom；全新临时profile。',[
                ('创建隔离会话',['aiida.storage.sqlite_temp.backend.SqliteTempBackend.create_profile','aiida.manage.configuration.load_profile']),
                ('创建输入并运行',['aiida.orm.nodes.data.base.BaseType.__init__','aiida.orm.nodes.node.Node.store','runtime.corrected_energy_workflow']),
                ('查询生产者和调用边',['aiida.orm.nodes.links.NodeLinks.get_incoming','aiida.orm.nodes.links.NodeLinks.get_outgoing','aiida.orm.utils.links.LinkManager.one','aiida.orm.utils.links.LinkManager.all','aiida.orm.utils.links.LinkManager.all_link_labels','aiida.orm.querybuilder.QueryBuilder.append','aiida.orm.querybuilder.QueryBuilder.all'])],
                ['结果=-2.25；creator为apply_correction；INPUT标签energy/correction；工作流有energy_per_atom和apply_correction两个子计算。'],PREFIX+'verify_materials_provenance.py'),
            recipe('repair_correction_units_with_provenance','诊断一个成功但数值错误的工作流和一个原子数为零的异常工作流：追踪输入，修复meV/eV及原子数后重跑；断言同一-2.25目标通过，旧错误值和excepted节点仍可查询，正常/错误/修复六节点归档于同组。','错误修正250被按eV/atom读取；另一个输入count=0导致真实子计算异常。',[
                ('运行并诊断错误',['runtime.corrected_energy_workflow','aiida.orm.querybuilder.QueryBuilder.append','aiida.orm.querybuilder.QueryBuilder.all','aiida.orm.nodes.process.process.ProcessNode.is_excepted']),
                ('定位输入并修复重跑',['aiida.orm.nodes.links.NodeLinks.get_incoming','aiida.orm.utils.links.LinkManager.one','runtime.corrected_energy_workflow']),
                ('保留历史和组归档',['aiida.orm.utils.loaders.load_node','aiida.orm.groups.Group.__init__','aiida.orm.groups.Group.store','aiida.orm.groups.Group.add_nodes','aiida.orm.groups.Group.nodes'])],
                ['错误247.5被独立目标拒绝；修复为-2.25；查询得到一个真实excepted子计算及count0；重跑成功后失败历史仍在；组6节点。'],PREFIX+'verify_materials_provenance.py')],
        'runtime_report':PREFIX+'runtime/materials_provenance/01.03.03.json',
        'runtime_scope':'真实AiiDA引擎、临时SQLite和来源图，显式能量载荷；正常、数值错误、真实异常、输入修复重跑，不执行HPC/DFT。',
    })


if __name__=='__main__':
    main()
