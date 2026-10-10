"""Final equipment/control/logistics scenes, reviewed against official releases."""
from seed_gen.scripts.scenario_collection_support import Collection,recipe
from seed_gen.scripts.prepare_test_fab_production_profiles import BASE,RAW,sel,entity,simpy_symbols,life_symbols
from seed_gen.scripts.prepare_test_fab_ate_profiles import tables

FIXTURE=(BASE/'verify_control_integration.py').as_posix()
CODEC='secsgem.secs.functions.base.SecsStreamFunction.'

def secs_codecs(names):
    rows=[sel('secsgem','secsgem.secs.functions.base','SecsStreamFunction','__init__ append encode decode set get get_format stream function','codec','所有具体SxFy从此继承构造/编解码；不在子类伪造重复方法。')]
    for stream,function in names:
        rows.append(sel('secsgem',f'secsgem.secs.functions.s{stream:02}f{function:02}',f'SecsS{stream:02}F{function:02}','','codec','具体流/功能的数据格式构造继承SecsStreamFunction。'))
    return rows

def protocol_symbols():
    rows=secs_codecs([(1,1),(1,2),(1,13),(1,14)])
    for module,name,methods in [('hsms.settings','HsmsSettings','__init__ connect_mode address port is_active'),
      ('hsms.header','HsmsHeader','__init__ p_type s_type encode decode'),('hsms.message','HsmsBlock',''),
      ('hsms.message','HsmsMessage','header data complete'),('common.message','Block','__init__ header data encode decode'),
      ('common.message','Message','__init__ from_block blocks'),('hsms.stream_function_header','HsmsStreamFunctionHeader','__init__'),
      ('hsms.select_req_header','HsmsSelectReqHeader','__init__'),('hsms.select_rsp_header','HsmsSelectRspHeader','__init__'),
      ('hsms.linktest_req_header','HsmsLinktestReqHeader','__init__'),('hsms.linktest_rsp_header','HsmsLinktestRspHeader','__init__'),
      ('secs.variables.base_number','BaseNumber','__init__ supports_value set get encode decode'),
      ('secs.variables.base_text','BaseText','__init__ supports_value set get encode decode'),('secs.variables.u4','U4',''),('secs.variables.string','String',''),
      ('gem.handler','GemHandler','__init__ communication_state enable disable waitfor_communicating')]:
        rows.append(sel('secsgem','secsgem.'+module,name,methods,'transport','协议配置、消息/字节生命周期与观测；原生HSMS类继承common.Message/Block的实现已显式选取。'))
    return rows

def control_symbols():
    rows=[]
    for module,names in [('xferfcn','tf tfdata'),('statesp','ss ssdata'),('timeresp','step_response forced_response initial_response impulse_response step_info'),('dtime','sample_system'),('bdalg','feedback series parallel'),('lti','poles zeros dcgain damp frequency_response'),('statefbk','ctrb obsv place lqr dlqr'),('margins','margin stability_margins')]:
        rows.extend(sel('control','control.'+module,n,None,'dynamics','状态空间/传递函数、响应、稳定性和反馈主链；不重复MATLAB别名，不引入绘图GUI。') for n in names.split())
    rows += [sel('control','control.statesp','StateSpace','__init__ poles zeros feedback append sample dcgain dynamics output','model'),
             sel('control','control.xferfcn','TransferFunction','__init__ num_list den_list poles zeros feedback sample dcgain','model'),
             sel('control','control.timeresp','TimeResponseData','time outputs states inputs to_pandas','observe')]
    return rows

def mpc_symbols(estimator=False):
    rows=[sel('do-mpc','do_mpc.model._model','Model','__init__ x u z p tvp y aux set_variable set_expression set_meas set_rhs set_alg setup get_linear_system_matrices','model'),
          sel('do-mpc','do_mpc.model._iteratedvariables','IteratedVariables','x0 u0 z0 t0','state','控制器/模拟器/估计器继承的初值和时钟属性。'),
          sel('do-mpc','do_mpc.optimizer','Optimizer','bounds scaling reset_history set_nl_cons get_tvp_template set_tvp_fun','optimize','公开约束/缩放/参数及重置；不暴露内部NLP数组来凑数量。'),
          sel('do-mpc','do_mpc.simulator','Simulator','__init__ settings scaling reset_history setup set_param get_tvp_template set_tvp_fun get_p_template set_p_fun set_initial_guess make_step','simulate'),
          sel('do-mpc','do_mpc.data','Data','__init__ set_meta update export','observe')]
    if estimator:
        rows += [sel('do-mpc','do_mpc.estimator._mhe','MHE','__init__ p_est0 set_param set_objective set_default_objective get_p_template set_p_fun get_y_template set_y_fun set_initial_guess setup make_step','estimate'),
                 sel('do-mpc','do_mpc.estimator._estimatorsettings','MHESettings','check_for_mandatory_settings supress_ipopt_output','configure')]
    else:
        rows += [sel('do-mpc','do_mpc.controller._mpc','MPC','__init__ settings terminal_bounds set_param set_objective set_rterm get_p_template set_p_fun set_uncertainty_values setup set_initial_guess make_step','control'),
                 sel('do-mpc','do_mpc.controller._controllersettings','MPCSettings','check_for_mandatory_settings supress_ipopt_output','configure'),
                 sel('do-mpc','do_mpc.data','MPCData','prediction','observe')]
    return rows

def main():
 c=Collection(base=BASE,raw=RAW)
 def src(url,text):return c.source(url,text,['equipment','lot','model','run','result'],['configure','run','observe','reset'],['repair_validate'])
 gh='https://github.com/bparzella/secsgem/blob/v0.3.0/'
 protocol=[src(gh+'docs/hsms/messages.md','HSMS消息由header/block构成，system id关联请求响应，数据类型0和select/linktest控制类型分开，长度为独立可检验字节契约。'),
           src(gh+'docs/secs/functions.md','SecsStreamFunction具体SxFy类提供字段set/get及encode/decode，SECS载荷可放入HSMS消息；文档旧module名称以发布源码校正。'),
           src(gh+'tests/test_hsms_packets.py','官方固定字节测试明确长度、session_id、system和stype；本轮另写socketpair和独立struct oracle验证所选路径。')]
 equipment=src(gh+'docs/firststeps/gemequipment.md','GEM设备模型定义SVID/ECID、collection event、alarm和remote command回调；示例部分旧构造签名以发布源码为准。')
 compliance=src(gh+'docs/gem/compliance.md','官方明确工艺处理状态、配方管理、spooling等未实现，alarm/report等缺持久化；START/STOP应用逻辑需使用者实现，不能宣称完整GEM合规。')
 host=src(gh+'secsgem/gem/hosthandler.py','主机按report定义/事件链接/启用构造S2消息；远程命令S2F41与S2F42、recipe列表/传输均有协议入口，但设备侧配方语义不自动具备。')
 ua_base='https://github.com/FreeOpcUa/opcua-asyncio/blob/v2.0.1/examples/'
 ua=[src(ua_base+'server-minimal.py','初始化服务端、namespace、对象和可写变量，演示方法/参数类型；本轮改只绑定127.0.0.1。'),
     src(ua_base+'client-minimal.py','客户端按namespace和browse path读写node，设备变量身份不能只用临时整数猜测。'),
     src(ua_base+'client-subscription.py','v2异步subscription迭代DataChangeEvent并可auto_reconnect；只列为参考能力，本轮固定任务验证读写/错误节点，不冒称订阅重连覆盖。')]
 ctl='https://python-control.readthedocs.io/en/0.10.2/generated/control.'
 dynamic=[src(ctl+'step_response.html','给定线性系统、时间网格和初值计算响应，TimeResponseData保存时钟、输入、状态和输出；数组形状及连续/离散时间不可混淆。'),
          src(ctl+'forced_response.html','明确输入随时间与初值，连续/离散模型输出遵守同一时间序列形状契约；可用解析指数/递推独立验收。'),
          src(ctl+'sample_system.html','默认ZOH从连续模型到离散系统，Ts须正且有统一单位；单极点采样可与exp(-Ts/tau)核对。')]
 feedbacksrc=src(ctl+'feedback.html','feedback默认sign=-1负反馈，sign=1为正反馈；输入输出维度需匹配，稳定性不能仅看单次仿真是否结束。')
 dm='https://github.com/do-mpc/do-mpc/blob/v5.1.2/examples/'
 mpcsrc=[src(dm+'CSTR/template_model.py','CSTR以浓度/温度为状态、流量/冷却为输入，set_variable/set_rhs/setup建立带明确单位的动态模型；本轮用可解析标量缩小验证。'),
         src(dm+'CSTR/template_mpc.py','配置时域、目标、输入变化惩罚、状态/输入边界和不确定参数，setup后逐步求解；参数缩放与可行性为环境实体。'),
         src(dm+'CSTR/template_simulator.py','同一model构造Simulator、参数回调与积分设置，make_step闭环回放；模拟器输入要求二维列向量。')]
 mhesrc=[src(dm+'rotating_oscillating_masses_mhe_mpc/template_mhe.py','MHE配置n_horizon、测量窗口、arrival/measurement权重和参数边界，set_default_objective后setup/make_step；错误测量单位可导致状态偏移。'),
         src(dm+'rotating_oscillating_masses_mhe_mpc/template_model.py','通过set_meas给定状态/输入观测关系，部分参数可待估；需明确哪些状态可观测，本轮常量标量为解析基线。'),mpcsrc[2]]
 sim=[src('https://simpy.readthedocs.io/en/stable/examples/machine_shop.html','机加工示例用Process、timeout与资源表示设备加工/故障维修；事件语义可迁移到lot和维护，自建工艺时长不是库自带Fab模型。'),
      src('https://simpy.readthedocs.io/en/stable/topical_guides/resources.html','Resource容量和request/release、Store有限缓冲共同表示设备与物流约束；调容量会改变排队。'),
      src('https://simpy.readthedocs.io/en/stable/topical_guides/monitoring.html','统计可观察env时间、资源占用和队列并保存事件；数值守恒和手算时间表比动画截图更适合作验收。')]
 opt=[src('https://developers.google.com/optimization/scheduling/job_shop','job shop由区间、工序先后和机器no-overlap构建，最小makespan输出开始/结束时间。'),
      src('https://developers.google.com/optimization/cp/cp_solver','CP-SAT约束是整数，必须区分OPTIMAL/FEASIBLE/INFEASIBLE/MODEL_INVALID/UNKNOWN；无可行解时不能读取伪排程。'),
      src('https://developers.google.com/optimization/scheduling/employee_scheduling','人员排班采用每班恰一人/每日最多一班及覆盖约束；体现一般资源布尔约束，不局限专用job-shop模型。')]
 change=src('https://centre-borelli.github.io/ruptures-docs/user-guide/detection/pelt/','PELT惩罚与min_size/jump影响离线分段；变点索引需映射同单位事件时间再和alarm相关。')
 drift=src('https://riverml.xyz/latest/api/drift/PageHinkley/','在线Page-Hinkley更新后查询drift_detected；阈值与冷启动决定触发，独立任务必须重置检测器。')
 lifetime=src('https://reliability.readthedocs.io/en/latest/Creating%20and%20plotting%20distributions.html','寿命分布可计算SF/分位数等，alpha与时间必须同单位；分布是给定/拟合假设，不直接提供真实设备RUL真值。')
 merge=src('https://pandas.pydata.org/docs/user_guide/merging.html','通过主键和validate基数约束链接equipment/lot/recipe数据；重复设备映射应拒绝。')
 entities=[entity('equipment','id protocol address namespace variable_map recipe_map state revision','登记→配置→连接/离线构造→读写/事件→断开；更改身份映射后旧结果失效。'),
           entity('workload','id lot route arrival process_time time_unit resource_id revision','登记人工批次/路由→校验约束和单位→运行；不自动解释为真实工厂。'),
           entity('model','id dynamics state input bounds weights baseline revision','建立变量/方程/目标→setup→逐步运行/观测；改方程或约束重新setup，跨任务新建对象。'),
           entity('run','id config_revision input_revision initial_state clock events result status','固定初始状态→运行→独立oracle验收→保存；旧控制器/检测器历史不跨任务共享。')]
 boundaries=['全部任务为本地/人工固定场景，无真实设备/PLC/MES、产线绩效或标准合规结论。','所列公开参考API不等于全部实跑工具；后续Agent环境需实现ID存储、JSON封装、权限与任务隔离。',
             '包内继承/导出别名、状态属性按实际源码选择；NumPy数组/基本算术、struct字节、JSON/时钟和已知单路线为显式基础设施。']
 def emit(sid,desc,pkgs,symbols,sources,tasks,bridges=(),extra=(),exception=''):
    if not exception and sid in ('06.01.02','06.01.04','06.04.01','06.04.02'):
        exception='当前47–49个操作已覆盖配置/构造、运行、结果观察和重建的完整固定主链；不为达到50加入重复协议层、GUI绘图或无关算法，数量按场景边界允许少量下浮。'
    c.write(dict(scenario_id=sid,description=desc,packages=pkgs,symbols=symbols,sources=sources,entities=entities,
                 capabilities=list(dict.fromkeys(s['capability'] for s in symbols)),bridges=list(bridges),boundaries=boundaries+list(extra),count_exception=exception,
                 runtime_infrastructure=[{'reference':'runtime.fixed_local_transport','reason':'socketpair字节回环及127.0.0.1动态端口OPC UA；异步context负责启动/关闭，禁止把本地测试推广为设备连接。'},
                                         {'reference':'runtime.engineering_state_and_oracle','reason':'配方字典/在线政策、canonical复合键、SimPy组件与单路线、单位换算以及独立解析/穷举验收均显式写在脚本中。'},
                                         {'reference':'runtime.solver_backends_and_array_shapes','reason':'do-mpc公开接口调用CasADi/IPOPT，OR-Tools公开接口调用原生CP-SAT；数组列向量和模型算术非额外隐藏控制器。'}],
                 tasks=tasks,runtime_report=(BASE/f'runtime/control/{sid}.json').as_posix(),runtime_scope='每条固定正常/错误/修复任务运行两次；离散值精确相同，浮点按7位比较且各项独立容差验收。'))
 def rec(i,d,initial,refs,checks):return recipe(i,d,initial,[('构造/运行/诊断/修复/独立验收',refs)],checks,FIXTURE)
 coderefs=[CODEC+'__init__',CODEC+'encode',CODEC+'decode',CODEC+'get']
 secpk=[('secsgem','primary','SECS/HSMS/GEM公开格式和设备状态入口；不引入未经固定源码的driver。')]
 secbounds=['secsgem-driver无GitHubRelease且tags为空；PyPI1.0.0未得到对应稳定源码标识，未进入稳定API候选池。pysemisecs PyPI404只作失败线索。','官方GEM compliance列出配方/处理状态等未实现；本轮不声称SEMI整体合规。']
 prot=protocol_symbols()
 emit('06.01.01','SECS-II/HSMS消息环境：构造会话/流功能和关联system id，将编码字节经本地socketpair传输并解码，诊断错响应及截断长度；保留连接生命周期参考入口但不冒称完整GEM协商。',secpk,prot,protocol,
      [rec('repair_hsms_correlation','给定S1F1请求的session7/system123，识别响应误用124；恢复相同system后解码核对响应关联和W位，并用独立network struct检查头字节。','本地字节socketpair，不连外部设备。',['secsgem.hsms.stream_function_header.HsmsStreamFunctionHeader.__init__','secsgem.common.message.Message.__init__','secsgem.common.message.Block.encode','secsgem.common.message.Block.decode','secsgem.common.message.Message.from_block'],['session7/system123，10字节头与独立struct一致。']),
       rec('reject_incomplete_hsms','截断消息末字节导致声明长度与实长不符，先拒绝不完整帧再恢复完整字节，验收长度与头字段一致。','4字节大端长度+10字节HSMS头。',['secsgem.common.message.Block.encode','secsgem.common.message.Block.decode'],['长度不符拒绝，修复长度相等；完整性检查属于显式工程层。'])],extra=secbounds+['该任务仅codec/socketpair；未运行HSMS select/reconnect/timeout等全协议场景。'])
 events=secs_codecs([(5,1),(5,2),(6,11),(6,12),(2,33),(2,35),(2,37)])
 events += [sel('secsgem','secsgem.hsms.settings','HsmsSettings','__init__ address port connect_mode','transport'),
            sel('secsgem','secsgem.gem.equipmenthandler','GemEquipmentHandler','__init__','state'),
            sel('secsgem','secsgem.gem.handler','GemHandler','__init__ communication_state enable disable waitfor_communicating','state'),
            sel('secsgem','secsgem.gem.hosthandler','GemHostHandler','__init__ clear_collection_events subscribe_collection_event enable_alarm disable_alarm list_alarms list_enabled_alarms go_online go_offline','event'),
            sel('secsgem','secsgem.gem.alarm','Alarm','__init__','alarm'),sel('secsgem','secsgem.gem.alarm_capability','AlarmCapability','alarms set_alarm clear_alarm','alarm'),
            sel('secsgem','secsgem.gem.collection_event_capability','CollectionEventCapability','collection_events registered_reports registered_collection_events trigger_collection_events get_ceid_name','event')]
 for module,name,methods in [('collection_event','CollectionEvent','__init__'),('collection_event_report','CollectionEventReport','__init__'),('collection_event_link','CollectionEventLink','__init__'),('data_value','DataValue','__init__ dvid name value_type use_callback id_type'),('status_variable','StatusVariable','__init__'),('status_data_collection_capability','StatusDataCollectionCapability','status_variables on_sv_value_request')]:events.append(sel('secsgem','secsgem.gem.'+module,name,methods,'event'))
 emit('06.01.02','GEM事件/报警：建立CEID/RPTID/VID映射，按发布API编解码报告并管理离线报警状态，修复变量顺序及未知报警身份；事件启用与报警set/clear分别保存。',secpk,events,[equipment,host,compliance],
      [rec('repair_report_variable_order','解码CEID50/RPT100含W01与123的报告，诊断VID定义颠倒把wafer当pressure；恢复变量顺序后验收字段身份。','报告V=[W01,123]。',coderefs,['waferW01/pressure123；原错pressure为文本。']),
       rec('repair_alarm_identity','创建设备报警25，验证未知99应拒绝；设定25、重复设定、清除，检查false/true/true/false，再编解码ALCD置位消息。','传输disabled的真实GemEquipmentHandler。',coderefs+['secsgem.gem.equipmenthandler.GemEquipmentHandler.__init__','secsgem.gem.alarm.Alarm.__init__','secsgem.gem.alarm_capability.AlarmCapability.set_alarm','secsgem.gem.alarm_capability.AlarmCapability.clear_alarm'],['未知ValueError，状态幂等，ALID25且ALCD包含128。'])],extra=secbounds+['报警传输关闭、未进行主机协商；状态生命周期与消息编解码分别实跑，不宣称事件已经交付远端。'])
 remote=prot+[sel('secsgem','secsgem.gem.hosthandler','GemHostHandler','__init__ send_remote_command go_online go_offline get_process_program_list delete_process_programs','command'),
              sel('secsgem','secsgem.gem.remote_command','RemoteCommand','__init__','command')]
 for s,f in [(2,41),(2,42),(7,3),(7,5),(7,6),(7,19),(7,20)]:remote.append(sel('secsgem',f'secsgem.secs.functions.s{s:02}f{f:02}',f'SecsS{s:02}F{f:02}','','codec'))
 emit('06.01.03','远程命令与配方引用：通过SECS S2F41/F42表达START参数与应答，将PPID映射到显式本地配方/在线状态，诊断未知配方和不满足前置状态；真实recipe存储执行需要环境工程层实现。',secpk,remote,[host,equipment,compliance],
      [rec('repair_recipe_reference','构造START携PPID=MISSING，解码后本地设备政策拒绝且保持idle；改为ETCH_A，验收HCACK0和running状态，配方为100W/10s。','显式配方字典含ETCH_A。',coderefs,['未知HCACK1/idle；修复HCACK0/running/ETCH_A。']),
       rec('repair_remote_precondition','用有效配方请求但online=false时应拒绝；修复在线前置后重发并解码应答，验收状态只在接受后推进。','同一START载荷，不执行实际设备命令。',coderefs,['离线拒绝/idle；在线接受。'])],extra=secbounds+['配方字典和状态政策由fixture实现，secsgem仅执行格式编解码；不以此声称原生完整recipe管理。'])
 opc=[sel('asyncua','asyncua.server.server','Server','__init__ init set_endpoint set_security_policy set_server_name start stop get_node get_namespace_array register_namespace get_namespace_index','server'),
      sel('asyncua','asyncua.client.client','Client','__init__ connect disconnect get_node get_objects_node get_namespace_array get_namespace_index create_subscription read_values write_values','client'),
      sel('asyncua','asyncua.common.node','Node','__init__ read_browse_name read_display_name read_data_type read_value read_data_value write_value set_writable set_read_only get_children get_child get_parent get_path add_object add_variable add_property call_method','node'),
      sel('asyncua','asyncua.common.subscription','Subscription','__init__ next_event delete subscribe_data_change subscribe_events unsubscribe modify_monitored_item set_monitoring_mode set_publishing_mode','subscription')]
 emit('06.01.04','OPC UA设备变量：构造本地命名空间/设备节点与可写量测，真实客户端读写并验证类型和节点身份，释放连接；订阅为参考扩展能力，固定验收只覆盖变量访问。',[('asyncua','primary','异步UA服务端/客户端/Node同一协议实现，避免再混同步层。')],opc,ua,
      [rec('repair_opcua_value_type','启动仅localhost的临时OPC UA服务，读取Pressure_kPa=1；尝试字符串写入被拒绝后改Double2.5，服务器和客户端读回一致。','临时端口NoSecurity固定fixture。',['asyncua.server.server.Server.__init__','asyncua.server.server.Server.init','asyncua.server.server.Server.set_endpoint','asyncua.common.node.Node.add_variable','asyncua.common.node.Node.set_writable','asyncua.client.client.Client.__init__','asyncua.common.node.Node.read_value','asyncua.common.node.Node.write_value'],['错类型拒绝且值仍1；修复读回2.5。']),
       rec('repair_opcua_node_identity','读取不存在node应返回UA错误，随后从Objects按namespace/ETCH01/Pressure_kPa浏览得到正确节点；验收值2.5并关闭客户端和服务端。','同一UA会话和变量。',['asyncua.client.client.Client.get_node','asyncua.common.node.Node.get_child','asyncua.common.node.Node.read_value','asyncua.client.client.Client.disconnect','asyncua.server.server.Server.stop'],['错误node拒绝；browse成功。'])],extra=['NoSecurity仅本地回环，不验证证书/生产权限；未覆盖订阅重连和实际PLC。'])
 emit('06.01.05','设备/lot规范对象：将发布SECS报告映射为equipment/lot/wafer/time事件表，保留同wafer跨机台事件，再按受基数约束的设备键连接配方；实体业务规则为显式环境层。',[('secsgem','primary','真实S6F11报告编解码。'),('pandas','complement','规范主键、时序和many_to_one设备映射。')],secs_codecs([(6,11)])+tables(),[equipment,protocol[1],merge],
      [rec('repair_canonical_event_key','解码三条设备报告，诊断仅按wafer去重把W1跨E1/E2事件合并；以lot/wafer/equipment/time复合键恢复三事件并按时间排序。','E1W1t1/E2W1t2/E1W2t3。',coderefs+['pandas.core.frame.DataFrame.__init__','pandas.core.frame.DataFrame.drop_duplicates','pandas.core.frame.DataFrame.sort_values'],['错误2，修复3且时序E1/E2/E1。']),
       rec('repair_equipment_mapping','连接设备到配方映射时注入重复E1，many_to_one应拒绝；修复映射后验收三事件的A/B/A配方身份。','两设备各唯一recipe映射。',['pandas.core.frame.DataFrame.merge','pandas.core.reshape.concat.concat'],['重复MergeError；正确3行。'])],bridges=[{'from':'S6F11 decoded RPT.V','to':'pandas canonical event','contract':'RPTID规定V列序，equipment/wafer/time+上下文lot构成完整事件身份；设备映射many_to_one，时间单位固定。'}])
 controlpk=[('control','primary','连续/离散动态、反馈与响应保持同一系统语义。')]
 emit('06.04.01','工艺动态模型：用传递函数/状态空间描述固定一阶响应，设置单位、初始状态和采样周期，比较连续与离散输出并定位时常单位错误；不从人工参数声称已辨识真实机台。',controlpk,control_symbols(),dynamic,
      [rec('repair_time_constant_units','创建1/(2s+1)的一阶模型，发现时常误录0.002导致响应过快；改2秒，按固定网格计算阶跃并与1-exp(-t/2)逐点核对。','0–10s共101点。',['control.xferfcn.tf','control.timeresp.step_response'],['t1响应1-exp(-.5)，全网格容差1e-10。']),
       rec('verify_discrete_model','用Ts1s ZOH采样同一模型，查看状态矩阵极点并执行六步常输入，验收极点exp(-.5)和1-exp(-k/2)序列。','初值0，输入1。',['control.dtime.sample_system','control.statesp.ssdata','control.timeresp.forced_response'],['独立离散解析序列一致。'])])
 emit('06.04.02','反馈与run-to-run修正：检查控制回路反馈符号/闭环极点，并用显式离散批间更新律追踪目标；修复正反馈或过大增益导致的发散，验收解析响应。',controlpk,control_symbols(),[feedbacksrc,dynamic[0],dynamic[1]],
      [rec('repair_feedback_sign','对2/(s+1)反馈，发现sign=+1带来正极点1；改负反馈得到极点-3并验收2/3*(1-exp(-3t))响应。','同一植物与增益2。',['control.xferfcn.tf','control.bdalg.feedback','control.lti.poles','control.timeresp.step_response'],['错误+1，正确-3；解析响应一致。']),
       rec('repair_run_to_run_gain','以静态y=u和u_next=u+g*(1-y)构造离散闭环，诊断g2.5发散；改g0.5重跑七步，验收1-.5^k并趋近1。','初始状态0，批次间隔1。',['control.statesp.ss','control.timeresp.forced_response'],['末值1-.5^6，坏增益误差>1。'])],extra=['R2R工艺更新律由明确工程模型指定，control负责系统响应；未验证真实工艺漂移/量测延迟。'])
 mprefs=['do_mpc.model._model.Model.__init__','do_mpc.model._model.Model.set_variable','do_mpc.model._model.Model.set_rhs','do_mpc.model._model.Model.setup','do_mpc.controller._mpc.MPC.__init__','do_mpc.controller._mpc.MPC.set_objective','do_mpc.controller._mpc.MPC.set_rterm','do_mpc.optimizer.Optimizer.bounds','do_mpc.controller._mpc.MPC.setup','do_mpc.controller._mpc.MPC.make_step']
 emit('06.04.03','约束MPC：在固定x_next=.8x+u模型中设置预测时域、目标和输入边界，真实求解并由同模型Simulator回放，检查动作限幅和状态重置；工艺模型为人工标量。',[('do-mpc','primary','Model/MPC/Simulator共用变量与CasADi/IPOPT后端。')],mpc_symbols(),mpcsrc,
      [rec('repair_mpc_actuator_bound','诊断输入上限0.05让首步动作受限，恢复±0.5并求解一步预测MPC；根据解析无约束1/1.01验收约束最优u0=.5。','x0=0，目标1，rterm.01，n_horizon1。',mprefs,['坏u.05，修复u.5，误差<1e-6。']),
       rec('replay_and_reset_mpc','用列向量动作逐步驱动Simulator，独立核对每步x_next=.8x+u、边界和八步后接近1；新建控制器/模拟器重新从0开始并获得同首步。','八步闭环，跨任务不复用状态。',mprefs+['do_mpc.simulator.Simulator.__init__','do_mpc.simulator.Simulator.set_param','do_mpc.simulator.Simulator.setup','do_mpc.simulator.Simulator.make_step','do_mpc.model._iteratedvariables.IteratedVariables.x0'],['动作≤.500001，末状态距1<.01，重建u0一致。'])],extra=['CasADi3.8.1/IPOPT实跑；Simulator拒绝(1,)输入，需(1,1)列向量，错误已修复。'])
 mhrefs=['do_mpc.model._model.Model.__init__','do_mpc.model._model.Model.set_variable','do_mpc.model._model.Model.set_rhs','do_mpc.model._model.Model.set_meas','do_mpc.model._model.Model.setup','do_mpc.estimator._mhe.MHE.__init__','do_mpc.estimator._mhe.MHE.set_default_objective','do_mpc.estimator._mhe.MHE.setup','do_mpc.estimator._mhe.MHE.make_step']
 emit('06.04.04','移动窗状态估计：明确动态/观测方程、测量窗口和arrival/measurement权重，运行do-mpc MHE；用常量可观测标量诊断单位和过强错误先验，保存估计轨迹。',[('do-mpc','primary','MHE与Model/观测/窗口共用公开模型体系。')],mpc_symbols(True),mhesrc,
      [rec('repair_mhe_measurement_units','对真实常量状态2的观测误传2000，估计也偏到2000；统一单位重建MHE后输入四次2，验收每步在2附近。','x_next=x，y=x，horizon3，弱arrival权重1e-8。',mhrefs,['修复估计距2<1e-5。']),
       rec('repair_mhe_arrival_weight','起点先验误为4且arrival权重1000时估计被钉在3.99附近；按可信测量将先验权重调至1e-8重建，验收第一步回到2。','无噪声固定观测2，测量权重1。',mhrefs,['坏first>3.9；修复2±1e-5，与最小二乘常量oracle一致。'])],extra=['只验证可解析常量标量，未证明复杂非线性机台可观测性或参数辨识质量。'])
 fallback=['FactorySimPy官方无Release，全部tag为alpha/beta，PyPI最新0.1.0b3；保留research/control_web的404、tags与PyPI证据，未进入正式API候选池。','采用稳定SimPy并由fixture显式构建source/buffer/processor/路线；不是声称SimPy自带完整Fab或AMHS模型。']
 simrefs=['simpy.core.Environment.__init__','simpy.core.Environment.run','simpy.events.Process.__init__','simpy.events.Timeout.__init__','simpy.resources.resource.Resource.__init__','simpy.resources.resource.Request','simpy.resources.resource.Release.__init__']
 # A class with no own methods is referenced as a construction, not invented __init__.
 simrefs.remove('simpy.resources.resource.Request')
 emit('06.05.02','制造组件离散事件模型：以稳定SimPy显式组装lot源、有限缓冲、机器和完工收集，验证容量/队列及输入输出守恒；FactorySimPy仅预发布而不选作稳定基础。',[('simpy','primary','事件和资源提供稳定公开基础，自建组件语义明确。')],simpy_symbols(True),sim,
      [rec('repair_machine_capacity','三个同时到达lot每个加工2分钟，发现机器误设容量2造成2/2/4完工；恢复单机后验收2/4/6且每lot恰一次。','有限Store缓冲+Resource处理。',simrefs,['正确完成时刻2/4/6。']),
       rec('repair_buffer_capacity','构建buffer容量0应报错，改1后回放固定三批，验收released3/completed3/lost0并记录所有排队完成。','无丢弃策略的工程组件。',simrefs+['simpy.resources.store.Store.__init__'],['零容量ValueError；守恒3。'])],extra=fallback)
 cp=[sel('ortools','ortools.sat.python.cp_model','CpModel','__init__ new_int_var new_bool_var new_constant add add_linear_constraint add_all_different add_allowed_assignments add_forbidden_assignments add_implication add_bool_or add_at_least_one add_at_most_one add_exactly_one add_bool_and add_min_equality add_max_equality add_abs_equality new_interval_var new_fixed_size_interval_var new_optional_interval_var new_optional_fixed_size_interval_var add_no_overlap add_cumulative clone minimize maximize has_objective clear_objective add_hint clear_hints add_assumption add_assumptions clear_assumptions model_stats validate export_to_file','schedule'),
     sel('ortools','ortools.sat.python.cp_model','CpSolver','__init__ solve stop_search value values boolean_value objective_value best_objective_bound num_conflicts num_branches deterministic_time status_name response_stats sufficient_assumptions_for_infeasibility','solve')]
 cprefs=['ortools.sat.python.cp_model.CpModel.__init__','ortools.sat.python.cp_model.CpModel.new_int_var','ortools.sat.python.cp_model.CpModel.add','ortools.sat.python.cp_model.CpModel.new_interval_var','ortools.sat.python.cp_model.CpModel.new_fixed_size_interval_var','ortools.sat.python.cp_model.CpModel.add_no_overlap','ortools.sat.python.cp_model.CpModel.minimize','ortools.sat.python.cp_model.CpSolver.__init__','ortools.sat.python.cp_model.CpSolver.solve','ortools.sat.python.cp_model.CpSolver.value']
 emit('06.05.06','通用约束调度：用OR-Tools CP-SAT表示整数时间/布尔资源、区间、维护日历和先后关系，显式检查最优或不可行状态；保留通用约束而不重复引入Pyomo求解路线。',[('ortools','primary','CP-SAT在本地有完整可运行整数约束求解链。'),('pyomo','excluded','代数建模可替代，但本场景不需再维护外部求解器/第二套模型。')],cp,opt,
      [rec('repair_general_schedule_calendar','两任务3/2分钟有先后且共享单机，发现漏PM[3,5]得假makespan5；补no-overlap维护区间后验收0–3/5–7，并用有限起点穷举证明最优7。','固定两任务。',cprefs,['坏5；修复7，枚举最优同为7。']),
       rec('repair_infeasible_domain','对x范围0–1却约束x≥2，求解必须返回INFEASIBLE且不读取值；扩正确域0–2重新求解，验收OPTIMAL和x2。','单整数变量，用于状态处理契约。',cprefs,['错误不可行，修复x2。'])],extra=['OR-Tools9.15.6755独立控制环境，避免与Job Shop Lib所需<9.13冲突；原生求解器作为正式wheel依赖。'])
 emit('06.05.07','AMHS运输资源与节拍：以明确单路线运输时间、车辆容量和下游单机建立事件回放，诊断车辆并发误配或秒/分钟错用，验收到达/加工时间表；不隐含复杂寻路。',[('simpy','primary','共享车辆/机器资源与运输/加工事件同一时钟。')],simpy_symbols(True),[sim[1],sim[2],sim[0]],
      [rec('repair_transport_capacity','两批同时请求2分钟运输，发现车辆容量2允许同时到达；改单车容量1并接2分钟单机，验收到达2/4、完工4/6。','固定A/B、单路线。',simrefs,['运输互斥；A0–2到达，B2–4到达。']),
       rec('repair_transport_time_units','把120秒误用120分钟使首批到达120，统一分钟为2后重跑，验收运输与加工表及lot身份。','同一两批路线。',simrefs,['修复到达2/4、完工4/6。'])],extra=fallback+['只有预声明两分钟路线，无动态图寻路、搬运碰撞或真实AMHS接口验证。'])
 alarm_sy=secs_codecs([(5,1)])+[sel('ruptures','ruptures.detection.pelt','Pelt','__init__ fit predict fit_predict','segment')]+tables()
 emit('06.06.01','报警诊断与轨迹关联：解码ALID/ALCD set-clear并映射时间，使用PELT定位压力均值阶跃，修复报警时钟和置位语义；保留关联证据，不自动判定物理根因。',[('secsgem','primary','实际报警格式编解码。'),('ruptures','complement','独立压力轨迹离线分段。'),('pandas','complement','设备/时间规范事件与结果表。')],alarm_sy,[equipment,change,protocol[1]],
      [rec('repair_alarm_trace_clock','对40个0后40个5的压力轨迹执行PELT，发现alarm秒误乘60导致时间不对齐；恢复t40后匹配真实阶跃边界。','固定80点，ALID25。',coderefs+['ruptures.detection.pelt.Pelt.__init__','ruptures.detection.pelt.Pelt.fit','ruptures.detection.pelt.Pelt.predict'],['分段40/80，修复alarm40距变点≤1。']),
       rec('separate_alarm_set_clear','分别解码ALCD129与1，识别128置位标志并用显式t40/t50形成活动区间；验收持续10且clear不误算第二次新报警。','同ALID25两事件。',coderefs,['set=true/clear=false，区间40–50。'])],bridges=[{'from':'SECS alarm message plus explicit timestamp','to':'ruptures trace breakpoints','contract':'设备同id、时钟同原点和单位；ALCD128区分set/clear，索引末端不是额外变点。'}],extra=['只验证时间关联；alarm消息不携带本样例所有业务时钟，时间上下文明确由工程层补入。'])
 pred=[sel('river','river.drift.page_hinkley','PageHinkley','__init__ update','drift')]+life_symbols(('Weibull',))+simpy_symbols(True)
 emit('06.06.05','预测性维护触发回放：River检测固定漂移，按显式策略把触发时刻送入SimPy维护事件；reliability检查给定寿命分布和同单位时钟。以人工故障政策验证接口，不声称真实RUL预测或收益。',[('river','primary','在线漂移事件触发。'),('simpy','complement','故障/维护时间与停机日志回放。'),('reliability','complement','明确寿命模型SF及时间单位。')],pred,[drift,sim[0],lifetime],
      [rec('repair_predictive_trigger','回放100点0后80点5，发现过高PageHinkley阈值漏触发，模型按t150故障停20；改阈值10后100–105报警并按已声明政策计划停2，验收日志时序。','政策假设及时PM避免该预设故障，仅人工反事实。',['river.drift.page_hinkley.PageHinkley.__init__','river.drift.page_hinkley.PageHinkley.update']+simrefs,['坏停机20，修复2，触发100–105。']),
       rec('repair_lifetime_clock_units','给定Weibull alpha200小时/beta2，发现把150小时写成9000令SF为0；统一小时后验收SF=exp(-(150/200)^2)。','给定分布而非真实寿命拟合。',['reliability.Distributions.Weibull_Distribution.__init__','reliability.Distributions.Weibull_Distribution.SF'],['SF与独立指数公式一致。'])],bridges=[{'from':'River first drift index','to':'SimPy maintenance event','contract':'每点一小时的固定人工时钟，首次触发转PM时间，跨任务新建检测器和环境。'},{'from':'SimPy age/time policy','to':'reliability Weibull SF','contract':'alpha和age同为小时；策略参数与分布假设独立记录，不把SF直接当故障时间预测。'}],extra=['PM避免t150故障是fixture规定的政策，不是从数据实证得到；无真实寿命或产线收益结论。'])

if __name__=='__main__':main()
