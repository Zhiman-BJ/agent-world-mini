"""Scene-level reviewed selections for production scheduling and maintenance."""
from pathlib import Path
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe

BASE=Path('seed_gen/scenario_collection/l1_test_fab')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_test_fab')
FIXTURE=(BASE/'verify_production.py').as_posix()

def entity(name,attributes,lifecycle):
    return {'entity':name,'attributes':attributes.split(),'lifecycle':lifecycle}

def sel(package,module,name,methods,capability='model',reason=''):
    return symbol(package,module,name,methods,capability,reason or '保留当前场景构造、配置、运行和检查主链所需公开入口，裁去无关后端和内部实现。',
                  '由所选模型工厂或继承构造取得；dataclass及动态别名不伪造源方法。')

def simpy_symbols(full=False):
    definitions=[('core','Environment','__init__ now active_process peek step run'),
                 ('events','Event','__init__ triggered processed ok value succeed fail'),
                 ('events','Timeout','__init__'),('events','Process','__init__ target name is_alive interrupt'),
                 ('events','AllOf','__init__'),('events','AnyOf','__init__'),
                 ('exceptions','Interrupt','__init__ cause'),
                 ('resources.resource','Resource','__init__ count'),('resources.resource','Request',''),('resources.resource','Release','__init__')]
    if full:
        definitions += [('events','ConditionValue','__init__ keys values items todict'),('events','Condition','__init__ all_events any_events'),
                        ('resources.base','BaseResource','__init__ capacity'),('resources.base','Put','__init__ cancel'),('resources.base','Get','__init__ cancel'),
                        ('resources.container','Container','__init__ level'),('resources.container','ContainerPut','__init__'),('resources.container','ContainerGet','__init__'),
                        ('resources.resource','PriorityResource','__init__'),('resources.resource','PriorityRequest','__init__'),('resources.resource','PreemptiveResource',''),
                        ('resources.resource','Preempted','__init__'),('resources.store','Store','__init__'),('resources.store','StorePut','__init__'),('resources.store','StoreGet',''),
                        ('resources.store','FilterStore',''),('resources.store','FilterStoreGet','__init__')]
    return [sel('simpy','simpy.'+module,name,methods,'simulation','lot进程、设备/缓冲资源和事件生命周期；env.process/timeout为源码BoundClass到Process/Timeout的公开别名。') for module,name,methods in definitions]

def pyjob_symbols():
    return [sel('pyjobshop','pyjobshop.Model','Model','__init__ jobs resources tasks modes constraints objective add_job add_machine add_renewable add_consumable add_task add_mode add_start_before_start add_end_before_start add_end_before_end add_start_at_start add_end_at_end add_identical_resources add_different_resources add_consecutive add_same_sequence add_setup_time add_mode_dependency set_objective summary from_data data solve','schedule'),
            sel('pyjobshop','pyjobshop.ProblemData','ProblemData','replace num_jobs num_resources num_machines num_tasks num_modes num_constraints machine_idcs task2modes resource2modes task2resources to_json from_json','persist'),
            sel('pyjobshop','pyjobshop.Solution','Solution','__init__ tasks jobs objective makespan tardy_jobs total_flow_time total_tardiness max_tardiness total_setup_time','observe'),
            sel('pyjobshop','pyjobshop.Solution','ScheduledTask','duration processing','observe'),
            sel('pyjobshop','pyjobshop.Solution','ScheduledJob','duration flow_time is_tardy tardiness','observe'),
            sel('pyjobshop','pyjobshop.Result','Result','','observe')]

def life_symbols(families=('Exponential','Weibull','Lognormal','Gamma')):
    rows=[]
    for family in families:
        rows.append(sel('reliability','reliability.Distributions',family+'_Distribution','__init__ plot PDF CDF SF HF CHF quantile inverse_SF mean_residual_life stats random_samples','lifetime'))
        rows.append(sel('reliability','reliability.Fitters','Fit_'+family+('_1P' if family=='Exponential' else '_2P'),'__init__','fit','保留高层参数估计入口，裁去LL/logf等内部似然实现。'))
    return rows

def main():
    c=Collection(base=BASE,raw=RAW)
    def source(url,evidence):return c.source(url,evidence,['lot','equipment','observation'],['construct','configure','solve','observe'],['fixed_fixture_repair'])
    sim_sources=[source('https://simpy.readthedocs.io/en/stable/examples/machine_shop.html','官方机器车间例用Process.interrupt和PreemptiveResource表示设备失效、维修优先级和剩余加工时间。'),
                 source('https://simpy.readthedocs.io/en/stable/topical_guides/resources.html','资源分Resource/Container/Store；request/release、容量、等待和取消事件可表示设备、物料与缓冲。'),
                 source('https://simpy.readthedocs.io/en/stable/topical_guides/monitoring.html','官方指导在状态变化时记录事件列表并测量资源占用/队列；支持明确区分等待、加工和完工时刻。')]
    pj_sources=[source('https://pyjobshop.org/stable/examples/simple_example.html','Model添加job/task/machine/mode，再求解并读取ScheduledTask资源、起止时刻及目标。'),
                source('https://pyjobshop.org/stable/examples/breaks.html','官方例将计划维护编码为资源breaks；默认任务不可穿越停机，allow_breaks才允许中断。'),
                sim_sources[0]]
    life_sources=[source('https://reliability.readthedocs.io/en/latest/Fitting%20a%20specific%20distribution%20to%20data.html','Fitters接收failures/right_censored、返回分布和参数；指出样本量/重删失限制。'),
                  source('https://reliability.readthedocs.io/en/latest/What%20is%20censored%20data.html','右删失表示观察终止时尚未失效；该包仅支持完整/右删失，左/区间删失需其他实现。'),
                  source('https://reliability.readthedocs.io/en/latest/Working%20with%20fitted%20distributions.html','拟合分布提供生存概率与寿命分位值，区分time/reliability置信界输入，避免将估计当确定寿命。')]
    entities=[entity('lot','id route_id release_time due_time quantity unit state','载入→排队→加工→完成；更改路线/时间需新建运行，旧结果失效。'),
              entity('equipment','id capacity calendar recipe_id queue status','定义容量与停机→申请/占用/释放→记录故障/维修，任务重置创建新实例。'),
              entity('run','id config_revision time_unit event_log schedule metrics','配置→求解/仿真→检查资源/顺序/目标→JSON保存；不把旧仿真对象回拨为reset。')]
    infra=[{'reference':'runtime.fixture_engineering_layer','reason':'Python生成器/列表/JSON存储记录lot身份；itertools穷举和独立算术是验收器，不替代包调度或DES求解。'},
           {'reference':'runtime.simpy_bound_aliases','reason':'env.process/timeout和resource.request/release分别为所选Process/Timeout/Request/Release的BoundClass别名；原始索引保留实际定义，不伪造方法。'}]
    common_boundaries=['本地人工固定制造数据，不连接真实MES、机台、PLC、许可证系统；未证明实际Fab吞吐/良率。',
                       '固定样例重复两次；所选接口集合大于实跑覆盖，不能称全部API运行通过。',
                       'JSON结果持久化和ID映射为待封装工程层；种子不是已实现的Agent工具服务器。',
                       '共同环境用OR-Tools9.12.4544满足job-shop-lib<9.13和PyJobShop>=9.12；硬依赖不重复暴露。']
    def emit(sid,description,packages,symbols,sources,tasks,bridges=(),extra_entities=(),boundaries=()):
        c.write(dict(scenario_id=sid,description=description,packages=packages,symbols=symbols,sources=sources,
                     entities=entities+list(extra_entities),capabilities=list(dict.fromkeys(s['capability'] for s in symbols)),bridges=list(bridges),
                     runtime_infrastructure=infra,boundaries=common_boundaries+list(boundaries),tasks=tasks,
                     runtime_report=(BASE/f'runtime/production/{sid}.json').as_posix(),runtime_scope='真实发布包上的固定正常/错误/修复链；独立固定数值或穷举oracle，两次运行完全相同。'))
    def rec(id,desc,initial,steps,assertions):return recipe(id,desc,initial,steps,assertions,FIXTURE)
    simrefs=['simpy.core.Environment.__init__','simpy.events.Process.__init__','simpy.events.Timeout.__init__','simpy.resources.resource.Resource.__init__','simpy.resources.resource.Request','simpy.resources.resource.Release.__init__','simpy.core.Environment.run']
    emit('06.05.01','晶圆批次离散事件仿真：把lot、设备容量、等待队列和加工时间表示为可重置的事件模型；比较容量与派工顺序，检查完工、WIP和资源互斥。以SimPy为主，排除同角色salabim，固定小例只证明逻辑正确。',
         [('simpy','primary','主事件引擎，低依赖、官方资源/故障案例完整。'),('salabim','excluded','同为DES，当前无需动画/内建监视器，避免混合两套事件时钟和资源对象。')],
         simpy_symbols(True),sim_sources,[
          rec('repair_machine_capacity','给定三个同时释放的lot，加工时间4/1/2分钟，检测错误双容量机台导致并行加工；改为单容量重新运行，验收完成时刻4/5/7且资源不重叠。','同一机台、固定批次，错误capacity=2。',[('构造队列并执行',simrefs)],['错配置未达到固定完成向量；修复得到4/5/7。']),
          rec('compare_fifo_spt','在相同单机数据上比较FIFO和SPT，重建模型运行并导出完成记录；验证SPT平均流动时间11/3，低于FIFO16/3且达到六种排列的独立穷举最优。','三批加工4/1/2，全部t=0释放。',[('按策略构造并运行',simrefs)],['完成总时长均7；SPT完工和11，穷举最优11。'])])
    sal_symbols=[sel('salabim','salabim.salabim','Environment','__init__ yieldless peek now run step current_component get_time_unit to_minutes to_hours','simulation'),
                 sel('salabim','salabim.salabim','Component','__init__ setup activate hold passivate interrupt resume cancel request release failed ispassive isrequesting isscheduled isinterrupted enter leave creation_time scheduled_time remaining_duration','simulation'),
                 sel('salabim','salabim.salabim','Resource','__init__ ispreemptive requesters claimers set_capacity release monitor reset_monitors print_statistics','resource'),
                 sel('salabim','salabim.salabim','Queue','__init__ append pop head tail clear as_list set_capacity arrival_rate departure_rate print_statistics','resource'),
                 sel('salabim','salabim.salabim','Monitor','__init__ tally reset mean std minimum maximum percentile number_of_entries values x xt as_dataframe monitor','observe'),
                 sel('salabim','salabim.salabim','State','__init__ get set reset trigger waiters','resource')]
    sr=['salabim.salabim.Environment.__init__','salabim.salabim.Environment.run','salabim.salabim.Component.request','salabim.salabim.Component.hold','salabim.salabim.Component.release']
    emit('06.05.03','使用salabim构建无图形制造队列、资源和监视器；区分仿真时钟、等待样本和资源占用，在固定lot流中诊断容量与指标定义。与SimPy为替代关系，本场景单独选择salabim，不交换活跃进程。',
         [('salabim','primary','此L3明确探索替代DES及内建Monitor。'),('simpy','excluded','同角色事件引擎，避免同时维护两套资源状态。')],sal_symbols,
         [source('https://www.salabim.org/manual/Modeling.html','官方生成器Component示例用hold/passivate/activate和FIFO队列建模；该页面标23.1.0，签名按26.0.1源码核对。'),source('https://www.salabim.org/manual/Resource.html','Resource支持容量、request/release、requesters/claimers与占用监视器。'),source('https://www.salabim.org/manual/Monitor.html','Monitor区分level/non-level；等待等一次性样本用tally，reset清空统计，mean包含零值。')],
         [rec('repair_salabim_capacity','为三个lot建立salabim组件和设备资源，发现容量2与单机基准不符；改为1并重新仿真，断言FIFO完成4/5/7。','加工4/1/2，使用yieldless=False。',[('创建资源并运行组件',sr)],['修复完成向量4/5/7且无重叠。']),
          rec('repair_wait_metric','从组件到达与获得设备的时刻统计等待，诊断把完成时刻误标为等待的问题；用Monitor.tally记录0/4/5，验证3个样本的平均等待为3分钟。','固定FIFO单机运行。',[('运行并采集等待',sr+['salabim.salabim.Monitor.tally','salabim.salabim.Monitor.mean','salabim.salabim.Monitor.number_of_entries'])],['误用完成均值16/3不合格，等待均值3。'])],boundaries=['不启用GUI/动画/实时控制；文档各页版本不一致，按发布26.0.1静态索引和实际运行校核。'])
    dispatch_symbols=[sel('job-shop-lib','job_shop_lib._operation','Operation','__init__ machine_id is_initialized','dispatch'),
                      sel('job-shop-lib','job_shop_lib._job_shop_instance','JobShopInstance','__init__ from_matrices to_dict num_jobs num_machines num_operations is_flexible has_release_dates has_deadlines has_due_dates operations_by_machine job_durations machine_loads total_duration','dispatch'),
                      sel('job-shop-lib','job_shop_lib._schedule','Schedule','__init__ schedule to_dict from_dict from_job_sequences job_sequences reset makespan is_complete check_schedule copy critical_path','observe'),
                      sel('job-shop-lib','job_shop_lib._scheduled_operation','ScheduledOperation','__init__ machine_id job_id position_in_job end_time','observe'),
                      sel('job-shop-lib','job_shop_lib.dispatching._dispatcher','Dispatcher','__init__ reset dispatch is_operation_ready start_time current_time available_operations available_machines available_jobs remaining_duration completed_operations ongoing_operations','dispatch'),
                      sel('job-shop-lib','job_shop_lib.dispatching.rules._dispatching_rule_solver','DispatchingRuleSolver','__init__ solve step','dispatch')]+simpy_symbols()
    dr=['job_shop_lib._operation.Operation.__init__','job_shop_lib._job_shop_instance.JobShopInstance.__init__','job_shop_lib.dispatching.rules._dispatching_rule_solver.DispatchingRuleSolver.__init__','job_shop_lib.dispatching.rules._dispatching_rule_solver.DispatchingRuleSolver.solve','job_shop_lib._schedule.Schedule.schedule','job_shop_lib._scheduled_operation.ScheduledOperation.end_time']
    emit('06.05.04','制造派工规则环境：以Job Shop Lib构造工序、机台与派工器，比较FIFO/SPT并诊断规则和时间单位；SimPy仅补充已生成排程的事件回放，检查身份和时间映射。',
         [('job-shop-lib','primary','规则驱动排程和显式Schedule对象。'),('simpy','complement','只回放已产生的排程，不再实现规则选择器。')],dispatch_symbols,
         [source('https://job-shop-lib.readthedocs.io/en/stable/api/job_shop_lib.dispatching.rules.html','DispatchingRuleSolver选择规则；SPT按下一工序长度，FCFS按工序位置，不能无条件视为真实到达时间排序。'),sim_sources[1],sim_sources[2]],
         [rec('choose_dispatch_rule','构造三批单机工序，分别运行FCFS和SPT，核对工序完整性和总完成时刻；验收SPT顺序1/2/0、完成和11优于FCFS16，并与六排列穷举一致。','三批仅各一工序，全部t=0，长度4/1/2。',[('构造并比较派工',dr)],['SPT完成和11，FCFS16；此fixture工序位置平局由输入次序确定。']),
          rec('repair_schedule_time_unit','将Job Shop Lib的排程转为SimPy批次回放，发现分钟被误乘60；修复时间映射后检查job身份、完成1/3/7与排程一致。','SPT排程及声明分钟单位。',[('读取排程',dr),('事件回放并核对',simrefs)],['错误回放60/180/420失败；修复1/3/7。'])],bridges=[{'from':'Job Shop Lib Schedule/ScheduledOperation','to':'SimPy Process/Timeout','contract':'显式复制job_id、start_time、end_time，分钟不缩放；本例回放单机无重叠表，不交换库内部状态。'}])
    pjr=['pyjobshop.Model.Model.__init__','pyjobshop.Model.Model.add_job','pyjobshop.Model.Model.add_machine','pyjobshop.Model.Model.add_task','pyjobshop.Model.Model.add_mode','pyjobshop.Model.Model.add_end_before_start','pyjobshop.Model.Model.solve','pyjobshop.Solution.Solution.tasks']
    emit('06.05.05','带停机、工序依赖、资源和批次释放时间的约束排程；PyJobShop负责工程建模并通过OR-Tools求解，独立检查排程可行性和小规模最优值，不把求解器正常退出视为验收。',
         [('pyjobshop','primary','提供资源日历、加工模式和时序约束的高层Model。'),('job-shop-lib','excluded','规则派工不能替代当前显式停机/释放约束最优化，避免额外排程状态。')],pyjob_symbols(),pj_sources,
         [rec('repair_resource_break','为3分钟和2分钟的顺序工序建模，发现漏设3–5分钟设备停机；补回breaks重新求解，断言工序0–3、5–7，无停机重叠且makespan=7。','单机两个工序，错误模型无停机。',[('建模和求解',pjr)],['错误makespan5未满足日历；正确最优7。']),
          rec('repair_lot_release','诊断批次应在t=2释放却从t=0加工的排程，设置job.release_date=2后重新求解；检查顺序、资源互斥、首工序开始2和最终完成7。','处理时间3+2，释放日期2。',[('重建job并求解',pjr)],['旧起点0失败；修复起点2，最优7。'])])
    emit('06.06.02','预防性维护排程：将设备PM窗口和生产工序统一到PyJobShop日历，在有限资源约束下生成可行排程，并通过SimPy回放验证执行时间与维护窗口一致。这里只验证给定PM窗口，不优化真实维护成本。',
         [('pyjobshop','primary','停机日历及工序排程。'),('simpy','complement','把静态优化排程转为离散事件执行证据。')],pyjob_symbols()+simpy_symbols(),pj_sources,
         [rec('repair_pm_calendar','检查生产排程是否进入设备3–5分钟PM窗口，补回遗漏维护日历并重排；验证无工序跨越PM且makespan=7。','3+2分钟工序；已知PM=[3,5]。',[('配置维护与重排',pjr)],['无PM模型与窗口冲突；修复0–3、5–7。']),
          rec('replay_pm_schedule','读取优化排程的任务身份与起止时间，在SimPy单容量设备中逐项回放并导出日志；验收观测为(0,0,3)/(1,5,7)，生产段与PM窗口互斥。','修复后ScheduledTask。',[('读取优化任务',pjr),('回放并观察设备',simrefs)],['回放身份、开始、结束逐项一致；维护空档保留。'])],bridges=[{'from':'pyjobshop.Solution.ScheduledTask','to':'SimPy Resource + Process','contract':'任务索引稳定、整数分钟、资源ID映射唯一；复制start/end与处理量，PM空档不可压缩，任何上游日历变更须重求解和重回放。'}])
    lifetime_entities=[entity('part_history','part_id equipment_id observation_time censor_flag time_unit','加载→区分故障/删失→单位校正→拟合；修改一条记录使参数/寿命预测失效。'),entity('lifetime_model','id family parameters ci data_revision','拟合→诊断→预测生存/分位值→保存参数；不能当作确定的真实剩余寿命。')]
    lr=['reliability.Fitters.Fit_Exponential_1P.__init__','reliability.Distributions.Exponential_Distribution.SF']
    emit('06.06.03','泵/阀等部件寿命分析：保留失效与右删失观察，拟合候选分布并计算生存概率、分位寿命和剩余寿命；本例用解析可验的指数/Weibull模型诊断删失标签和时间单位。',
         [('reliability','primary','当前数据为完整和右删失，官方Fitters及分布接口闭环。'),('surpyval','excluded','能覆盖左/区间删失，但当前没有该需求；避免重复生存拟合/分布状态。')],life_symbols(),life_sources,
         [rec('repair_censoring','给定10/20/30小时失效及40小时仍存活的部件，将误当第四次故障的记录改为右删失后重新拟合指数分布；验收失效率3/100、20小时生存率exp(-0.6)。','四个独立部件，最后一个censor_flag=1。',[('拟合并查询生存',lr)],['错误失效率0.04，修复0.03；独立总暴露100小时。']),
          rec('repair_lifetime_unit','在alpha=100小时、beta=2的Weibull模型下诊断把50小时写成3000分钟但未转换的预测，统一小时后重新查询；验收生存exp(-0.25)和B10=100sqrt(-ln0.9)。','固定分布和查询年龄，单位契约hour。',[('构造分布和查询', ['reliability.Distributions.Weibull_Distribution.__init__','reliability.Distributions.Weibull_Distribution.SF','reliability.Distributions.Weibull_Distribution.quantile'])],['错误生存近0失败；正确exp(-0.25)，分位值独立公式。'])],extra_entities=lifetime_entities,boundaries=['未支持左/区间删失；不把四个人工点的数值验证当作寿命模型统计可信证明。'])
    mt_symbols=simpy_symbols()+life_symbols(('Exponential','Weibull'))+[sel('reliability','reliability.Repairable_systems',n,'__init__','fit') for n in ('ROCOF','MCF_nonparametric','MCF_parametric')]
    emit('06.06.04','从设备故障/恢复事件重建实际运行与维修区间，计算MTBF、MTTR和观察窗可用率，并在明确指数更新假设下拟合运行时间分布；SimPy产生可验事件，reliability负责寿命统计。',
         [('simpy','primary','固定故障恢复事件生成与时间轴。'),('reliability','complement','指数/Weibull更新分布、故障趋势和累积故障分析；不重复DES。')],mt_symbols,[sim_sources[0],sim_sources[2],life_sources[0]],
         [rec('repair_mtbf_denominator','回放三段8/12/10小时运行及2/1/3小时维修记录，定位把总日历时间36除以故障数的MTBF错误；改为运行时间30/3并验收MTBF10、MTTR2、可用率5/6。','固定三个故障恢复周期，观察结束36。',[('执行故障与恢复',simrefs)],['错误MTBF12失败；修复10，维修均值2，可用率5/6。']),
          rec('fit_renewal_uptime','将事件日志提取的8/12/10小时运行区间交给指数拟合，检查单位和故障数；验收失效率0.1、已运行5小时的平均剩余寿命10小时，并明确这是指数模型条件值。','三个完整运行区间。',[('生成与提取周期',simrefs),('拟合和剩余寿命查询', ['reliability.Fitters.Fit_Exponential_1P.__init__','reliability.Distributions.Exponential_Distribution.__init__','reliability.Distributions.Exponential_Distribution.mean_residual_life'])],['MLE=3/30；指数无记忆性给出MRL=10。'])],bridges=[{'from':'SimPy failure/repair event log','to':'reliability Fit_Exponential_1P','contract':'相邻恢复→故障差为uptime；故障→恢复差为repair；只将完整uptime传failures，单位小时，观察尾部若未失效必须另外标右删失。'}],extra_entities=lifetime_entities,boundaries=['此观察窗含完整三周期，不含计划停机、等待备件或尾部删失；分母定义必须在真实数据适配时显式化。'])

if __name__=='__main__':main()
