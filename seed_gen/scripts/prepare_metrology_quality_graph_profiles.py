"""Quality evidence scenes: explicit table/graph/statistics responsibilities."""
from pathlib import Path
from seed_gen.scripts.scenario_collection_support import Collection,symbol,recipe

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_metrology_quality')
def cl(p,m,n,methods,cap,why,construction=None):return symbol(p,m,n,methods,cap,why,construction)
def fn(p,m,names,cap,why):return [symbol(p,m,n,None,cap,why) for n in names.split()]

def tables():
    return [cl('pandas','pandas.core.frame','DataFrame','__init__ from_records to_dict to_numpy shape assign rename set_index reset_index drop drop_duplicates duplicated sort_values sort_index query groupby merge join pivot_table isna dropna count mean std','table','规范lot/wafer/事件表、主键去重、受控关联与命名统计。'),
        cl('pandas','pandas.core.generic','NDFrame','copy to_csv to_json equals astype fillna','persist','表格继承的快照/保存与类型修复，保留原始输入和版本。','由DataFrame/Series实例继承，基类不独立构造。'),
        cl('pandas','pandas.core.groupby.groupby','GroupBy','mean count size sum std var','aggregate','由明确批次/产品键生成的分组统计，避免整体均值掩盖混合。','由DataFrame.groupby工厂获得，直接构造会破坏键语义。'),
        cl('pandas','pandas.core.series','Series','__init__ to_dict to_frame unstack map unique sort_values','table','列级标签、编码和分组结果转换；保留series身份与索引。')]+fn('pandas','pandas.io.parsers.readers','read_csv','io','读取带来源和列约定的记录。')

def graphs():
    return [cl('networkx','networkx.classes.digraph','DiGraph','__init__ add_node add_nodes_from remove_node remove_nodes_from add_edge add_edges_from remove_edge remove_edges_from predecessors successors in_edges out_edges in_degree out_degree clear','graph','有向制造/证据关系，边类型与来源放属性；删除局部错误边保留原始账本。'),
        cl('networkx','networkx.classes.graph','Graph','nodes has_node has_edge get_edge_data number_of_nodes number_of_edges copy subgraph edge_subgraph','graph','DiGraph继承的节点/子图/快照能力，不再暴露第二种图构造。','由DiGraph对象继承，Graph不单独作为另一套canonical state。')]

def graph_functions():
    return fn('networkx','networkx.algorithms.dag','is_directed_acyclic_graph topological_sort topological_generations ancestors descendants','graph_query','查有向顺序、上游/下游证据，环和缺链不能静默通过。')+fn('networkx','networkx.algorithms.shortest_paths.generic','has_path shortest_path','graph_query','追踪具体证据或工艺路径。')+fn('networkx','networkx.readwrite.json_graph.node_link','node_link_data node_link_graph','persist','JSON图保存恢复，保留有向边、节点ID和证据属性。')

def stats(names):
    return fn('scipy','scipy.stats._stats_py',names,'statistics','公开scipy.stats别名对应此源码定义；保留样本/备择/方差假设，不把p值当因果证明。')

def main():
    c=Collection(base=BASE,raw=RAW)
    P='https://pandas.pydata.org/docs/';N='https://networkx.org/documentation/stable/';R='https://centre-borelli.github.io/ruptures-docs/'
    source_text={
      P+'user_guide/merging.html':'连接教程区分exact/ordered/asof join，展示按键关联表；键约束决定事件谱系是否可信。',
      P+'user_guide/timeseries.html':'展示解析时间、时区localize/convert与重采样，统一绝对时间后才可按顺序关联来源。',
      P+'user_guide/groupby.html':'按键拆分、聚合和过滤完整示例，说明组均值/数量/方差及transform与aggregate差异。',
      P+'getting_started/intro_tutorials/09_timeseries.html':'实际OpenAQ多站测量数据示例按时间、站点、单位组织表并转UTC时间；迁移的是事件/观测表示，不冒充半导体实例。',
      N+'auto_examples/graph/plot_dag_layout.html':'真实代码从DiGraph到topological_generations分层，支持将事件关系按依赖拓扑审阅。',
      N+'reference/readwrite/json_graph.html':'node-link图JSON保留节点/边表示，支持保存证据图并复验引用。',
      N+'reference/algorithms/generated/networkx.algorithms.dag.ancestors.html':'示例定义能到达观测节点的所有上游节点，适合限制具有谱系证据的候选原因。',
      'https://www.itl.nist.gov/div898/handbook/prc/section3/prc31.htm':'NIST工艺比较说明双过程均值假设、样本方差和独立样本检验，提供比较工艺质量的数学基础。',
      'https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ttest_ind.html':'独立样本检验默认等方差，equal_var=False明确采用Welch；需保留输入数据与假设。',
      'https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.fisher_exact.html':'2x2列联表返回OR及固定边际下双侧精确概率，定义并非某候选是根因的概率。',
      R+'user-guide/detection/dynp/':'动态规划求固定变点数的全局分段代价最小值；jump只允许网格候选点，min_size限制段长。',
      R+'user-guide/detection/pelt/':'PELT使用惩罚控制未知变点数，给出分段信号完整fit/predict例子和网格参数作用。',
      R+'user-guide/costs/costl2/':'L2分段代价明确为相对段均值的平方差和，可构造独立枚举oracle。',
      'https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm':'NIST制造过程CUSUM案例说明累计偏移能检出小均值变化；本环境选择离线分段，未把CUSUM API混为同一模型。',
    }
    def sources(urls):return [c.source(u,source_text[u],['source_record','entity_key','quality_measurement','evidence_graph'],['join','aggregate','query','test','persist'],['固定制造观测的校验、诊断与证据修复']) for u in urls]
    def task(sid,tid,desc,initial,steps,checks):return recipe(tid,desc,initial,steps,checks,'verify_quality.py:'+sid)
    designs=[dict(scenario_id='08.02.01',description='重建制造/测试/维修事件的可追溯时间线：以记录ID、wafer ID、原时区和来源维护表格，统一UTC并连接为有向依赖图；缺键、反向时间或循环必须显式定位。固定样例验证跨时区排序与图保存恢复，领域事件权限仍需后续封装。',
      packages=[('pandas','primary','管理带来源的事件表、时间/键规范化和关联。'),('networkx','complement','维护显式依赖图和无环/路径检查，时间排序不代替依赖关系。')],
      symbols=tables()+graphs()+graph_functions()+fn('pandas','pandas.core.tools.datetimes','to_datetime','time','按原偏移解析UTC，保留原始时间字符串。')+fn('pandas','pandas.core.reshape.merge','merge_asof','time','在明确容差和分组键内关联相邻事件，禁止无界最近时间猜测。'),
      sources=sources([P+'getting_started/intro_tutorials/09_timeseries.html',P+'user_guide/timeseries.html',P+'user_guide/merging.html',N+'auto_examples/graph/plot_dag_layout.html']),
      tasks=[task('08.02.01','reconstruct_event_timeline','将两份来源的W1 load/etch/test事件统一UTC，按0/5/20分钟重建有向时间线并输出可保存证据图；核验总时长1200秒和固定事件顺序。','三条制造记录含UTC和+08:00，event_id保持唯一。',[
        ('载入并解析排序',['pandas.core.frame.DataFrame.__init__','pandas.core.tools.datetimes.to_datetime','pandas.core.frame.DataFrame.sort_values','pandas.core.frame.DataFrame.to_dict']),('构建并验收DAG',['networkx.classes.digraph.DiGraph.__init__','networkx.classes.digraph.DiGraph.add_nodes_from','networkx.classes.digraph.DiGraph.add_edges_from','networkx.algorithms.dag.is_directed_acyclic_graph','networkx.algorithms.dag.topological_sort'])],['顺序load/etch/test','时间跨度1200秒','固定两条边']),
        task('08.02.01','repair_timezone_and_cycle','修复etch错标UTC导致的晚8小时事件，并删除test→load错误回边；重新解析和验收无环时间线，JSON往返保留固定边与来源。','错误副本09:05Z实际为09:05+08:00；原始source保留。',[
        ('按来源修复UTC解析',['pandas.core.tools.datetimes.to_datetime','pandas.core.frame.DataFrame.sort_values']),('检测并修复环，保存恢复',['networkx.classes.digraph.DiGraph.add_edge','networkx.algorithms.dag.is_directed_acyclic_graph','networkx.classes.digraph.DiGraph.remove_edge','networkx.readwrite.json_graph.node_link.node_link_data','networkx.readwrite.json_graph.node_link.node_link_graph'])],['错误顺序和有环必须失败','修复后恢复两条边和无环'])]),
      dict(scenario_id='08.02.02',description='按产品、工艺和时间等预先定义条件比较批次质量：保留原始wafer测量、分组样本量、标准化权重与检验假设，识别产品混合引发的总体均值误导。固定制造样例给出组内+2百分点但总体-12.4的反转，统计关联不自动视为工艺改进因果。',
      packages=[('pandas','primary','维护批次/产品匹配、分组汇总和可追溯输出。'),('scipy','complement','仅提供所需比较统计与不确定性，表格处理不重复暴露。')],
      symbols=tables()+stats('ttest_ind fisher_exact spearmanr')+fn('scipy','scipy.stats._mannwhitneyu','mannwhitneyu','statistics','秩检验作为分布假设不同的比较方式。')+fn('scipy','scipy.stats._resampling','bootstrap permutation_test','statistics','在明确采样单位和固定随机状态下形成重抽样比较。')+fn('pandas','pandas.core.reshape.pivot','crosstab','aggregate','通过具名类别产生失败/通过列联表。'),
      sources=sources(['https://www.itl.nist.gov/div898/handbook/prc/section3/prc31.htm',P+'user_guide/groupby.html',P+'user_guide/merging.html','https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ttest_ind.html']),
      tasks=[task('08.02.02','compare_matched_lots','按easy/hard产品分别比较A/B两批制造良率并计算hard组Welch统计量，核验每产品B-A=2百分点和独立样本方差公式。','40个制造wafer记录，四组均值98/80/100/82，组数量18/2/2/18。',[
        ('按批次和产品聚合',['pandas.core.frame.DataFrame.__init__','pandas.core.frame.DataFrame.groupby','pandas.core.groupby.groupby.GroupBy.mean','pandas.core.series.Series.unstack']),('提取匹配样本进行比较',['pandas.core.frame.DataFrame.query','scipy.stats._stats_py.ttest_ind'])],['组内差均2','Welch统计量-3.8873012632 atol1e-12']),
        task('08.02.02','repair_product_mix_bias','拒绝用不匹配产品比例的整体差-12.4百分点判断批次退化；按预定等产品权重重新汇总，恢复+2百分点并报告分组样本量。','错误估计直接平均各批所有wafer；目标为equal product weights。',[
        ('诊断总体与分层均值差异',['pandas.core.frame.DataFrame.groupby','pandas.core.groupby.groupby.GroupBy.mean']),('按产品输出差值',['pandas.core.series.Series.unstack'])],['整体差-12.4不等于目标','预定加权差+2'])]),
      dict(scenario_id='08.02.03',description='对工艺变更附近有序质量测量做离线变点检测与前后统计比较：维护采样索引、分段代价、候选分辨率及变化点来源，检测位置与变更日志可关联但不自动等同因果。固定L2分段以独立枚举SSE验收，支持网格分辨率修复。',
      packages=[('ruptures','primary','负责高层离线分段及对应代价模型，固定变点数Dynp为主，未知数PELT为同框架补充。'),('pandas','complement','维护有序测量及元数据，不参与第二套变点求解。'),('scipy','complement','用于前后独立样本比较，不能替代变点定位。')],
      symbols=tables()+[cl('ruptures','ruptures.detection.dynp','Dynp','__init__ fit predict','detect','固定变点数主实现，显式min_size/jump。'),cl('ruptures','ruptures.detection.pelt','Pelt','__init__ fit predict','detect','变点数未知时采用惩罚模型，避免盲目固定数目。'),cl('ruptures','ruptures.costs.costl2','CostL2','__init__ fit error','diagnose','逐段L2代价诊断与候选边界比较。'),cl('ruptures','ruptures.base','BaseCost','sum_of_costs','diagnose','由具体代价类继承的分段总成本计算。','CostL2实例继承，不直接实例化抽象协议。')]+stats('ttest_ind'),
      sources=sources([R+'user-guide/detection/dynp/',R+'user-guide/detection/pelt/',R+'user-guide/costs/costl2/','https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm']),
      tasks=[task('08.02.03','detect_process_changepoint','对80点制造工艺轨迹以L2代价检测一个变化点，验收[37,80]和均值升高3；按独立所有候选SSE枚举确认37为最小。','固定37点低均值/43点高均值，min_size5，jump1。',[
        ('配置并拟合分段',['ruptures.detection.dynp.Dynp.__init__','ruptures.detection.dynp.Dynp.fit']),('输出断点',['ruptures.detection.dynp.Dynp.predict'])],['唯一独立最优37','最后80为结束边界而非额外变化点']),
        task('08.02.03','repair_detection_resolution','发现jump20将真正37点变化限制到40，改为jump1重算；恢复37并用同一前后Welch公式核验变化统计。','错误候选网格20，目标位置容差0且独立SSE已固定。',[
        ('修复候选网格重拟合',['ruptures.detection.dynp.Dynp.__init__','ruptures.detection.dynp.Dynp.fit','ruptures.detection.dynp.Dynp.predict']),('前后比较',['scipy.stats._stats_py.ttest_ind'])],['错误40须失败','恢复37','Welch-66.8931050独立验算'])]),
      dict(scenario_id='08.02.04',description='将制造谱系与质量测量合成候选原因排序环境：只纳入观测的真实上游设备/腔室，比较失败比例与精确列联统计并保留通过反例。固定制造账本验证chamberA关联和错误join修复，明确输出为有证据的候选而不是已证实根因。',
      packages=[('networkx','primary','限定因果候选的制造谱系路径和证据引用。'),('pandas','complement','将按wafer主键关联的通过/失败记录形成具名列联表。'),('scipy','complement','执行必要精确关联检验，保留统计与因果边界。')],
      symbols=graphs()+graph_functions()+tables()+stats('fisher_exact')+fn('pandas','pandas.core.reshape.pivot','crosstab','aggregate','把设备/失败标志映射列联表，保留边际与通过反例。'),
      sources=sources([N+'reference/algorithms/generated/networkx.algorithms.dag.ancestors.html',P+'user_guide/merging.html','https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.fisher_exact.html']),
      tasks=[task('08.02.04','rank_supported_root_cause_candidates','沿制造图筛选失败test的上游腔室，对A8/10与B2/10失败计数计算OR和精确双侧p值；把A排为候选、保留A两条通过反证、排除无关联C。','20制造wafer及process/test边，未执行外部维修或状态变更。',[
        ('构造谱系并限制上游候选',['networkx.classes.digraph.DiGraph.__init__','networkx.classes.digraph.DiGraph.add_edge','networkx.classes.digraph.DiGraph.add_node','networkx.algorithms.dag.ancestors']),('生成列联和关联统计',['pandas.core.frame.DataFrame.__init__','pandas.core.reshape.pivot.crosstab','scipy.stats._stats_py.fisher_exact','pandas.core.frame.DataFrame.query'])],['OR16','p=.023014137565221155独立组合数枚举','A通过反证2','C排除']),
        task('08.02.04','repair_genealogy_join','识别错误轮换join使两腔室各5次失败、OR1，按原wafer身份恢复真实谱系；复核A8/B2并保留原错表证据。','错误表用行号奇偶分配chamber，原始记录保存真实wafer→chamber。',[
        ('保留副本并重核按身份关联',['pandas.core.generic.NDFrame.copy','pandas.core.reshape.pivot.crosstab','scipy.stats._stats_py.fisher_exact'])],['错误OR1','恢复原始失败计数8/2'])]),
      dict(scenario_id='08.02.05',description='把质量问题、原因假设、纠正措施、验证和后续效果记录组织成可审阅CAPA证据图；每节点绑定来源、版本及适用状态，支持缺链诊断和JSON导出。仅建立本地证据完整性任务，不伪造实验、确认根因或写入外部QMS关闭状态。',
      packages=[('networkx','primary','管理有向证据引用、缺链/环检查及JSON快照。'),('pandas','complement','导出带字段与来源的证据清单，避免图属性仅隐藏在内存。')],
      symbols=graphs()+graph_functions()+tables(),
      sources=sources([N+'auto_examples/graph/plot_dag_layout.html',N+'reference/readwrite/json_graph.html',P+'user_guide/merging.html']),
      tasks=[task('08.02.05','assemble_capa_evidence','整理五类制造证据issue/cause/action/verification/followup，检查有向无环完整链和原始来源，输出清单并JSON往返；保留cause.confirmed=False。','预存本地制造fixture：已实施措施、通过验证、30天通过followup、尚未确认原因。',[
        ('建立带来源证据节点和引用',['networkx.classes.digraph.DiGraph.__init__','networkx.classes.digraph.DiGraph.add_node','networkx.classes.digraph.DiGraph.add_edges_from']),('核对路径并导出清单与图',['networkx.algorithms.dag.is_directed_acyclic_graph','networkx.algorithms.shortest_paths.generic.has_path','networkx.classes.graph.Graph.nodes','pandas.core.frame.DataFrame.__init__','networkx.readwrite.json_graph.node_link.node_link_data','networkx.readwrite.json_graph.node_link.node_link_graph'])],['五类节点来源均存在','issue可达followup且无环','未确认原因字段不被自动提升']),
        task('08.02.05','repair_missing_effectiveness_evidence','拒绝缺少followup的CAPA证据集，从已存在的固定审核记录补回30天效果节点及其引用，再验收完整性；不得把新增空节点当作通过证据。','副本丢失followup，原始审核fixture仍存在且标记passed。',[
        ('定位缺失并从原审核记录补回',['networkx.classes.graph.Graph.copy','networkx.classes.digraph.DiGraph.remove_node','networkx.classes.digraph.DiGraph.add_node','networkx.classes.digraph.DiGraph.add_edge']),('复核完整链和状态',['networkx.algorithms.shortest_paths.generic.has_path','networkx.algorithms.dag.is_directed_acyclic_graph'])],['缺失followup必须失败','已存来源/通过状态/30天窗口和链全部恢复'])])]
    for d in designs:
        if d['scenario_id'] in ['08.02.01','08.02.04','08.02.05']:
            d['sources'].append(c.source('https://www.ibm.com/think/topics/root-cause-analysis',
                'IBM质量管理应用说明列出定义问题、收集证据、候选排查/排序、纠正措施和防复发，并说明事件分析要重建发生顺序；资料不能证明某统计关联是根因。',
                ['issue','evidence','candidate_cause','corrective_action'],['reconstruct','rank','retain_counterevidence'],['原因候选排序与纠正措施证据链，保留未证实状态']))
        if d['scenario_id'] in ['08.02.01','08.02.05']:
            d['sources'].append(c.source('https://openlineage.io/docs/spec/object-model/',
                '官方运行谱系模型区分Job、Run、Dataset以及实际START/COMPLETE事件、来源版本和质量断言；设计元数据不能冒充已执行事件。',
                ['run_id','source_dataset','event','quality_assertion'],['link_source','validate_run_state','persist'],['证据实体具有稳定身份和来源，避免把计划任务当成真实验证']))
        d['capabilities']=list(dict.fromkeys(t['capability'] for t in d['symbols']))
        d['entities']=[{'name':'source_record','identity':'record_id','attributes':['source_path','source_hash','original_fields','unit','timezone','revision'],'lifecycle':'载入→核对来源→规范化副本；原文保留供纠错/重置，修改后派生产物失效。'},
          {'name':'manufacturing_entity','identity':'lot/wafer/equipment_id','attributes':['product','process','timestamp_utc','measurements','pass_fail','source_refs'],'lifecycle':'按唯一键关联→检测缺失/重复→分析；禁止用行号默认代替实体身份。'},
          {'name':'analysis_config','identity':'analysis_id','attributes':['strata/weights','time_window','test_assumptions','candidate_resolution','frozen_thresholds'],'lifecycle':'配置→计算→独立验收→报告；配置或数据改变需重算统计/排序。'},
          {'name':'evidence_result','identity':'result_id','attributes':['source_revisions','graph_edges','metrics','counterevidence','hypothesis_status','acceptance_checks'],'lifecycle':'生成→核对来源/路径/数值→保存恢复；原因假设不因统计显著自动确认，CAPA不自动关闭。'}]
        d['bridges']=[{'from':'canonical pandas keyed rows','to':'NetworkX node/edge attributes or scipy/ruptures numerical input as selected','contract':'用稳定ID关联，保留原顺序/时区/单位及观测分组；统计数组导出绑定row IDs，图结果保存来源引用。'}]
        d['runtime_infrastructure']=[{'reference':'python.numpy_json_datetime','kind':'infrastructure','reason':'数组、显式固定权重和单位转换、独立SSE/组合数/方差oracle及JSON序列化，不隐藏另一套统计求解器。'},
          {'reference':'runtime.pandas_indexing','kind':'infrastructure','reason':'DataFrame/Series []、loc、columns/values等已有对象访问语法；不伪造为新的顶层函数或领域动作。'}]
        d['boundaries']=['所有运行数据是制造fixture；未接入真实MES/QMS，未证明因果或真实质量改善。','仅来源Python静态池及指定任务实跑，SciPy原生函数和pandas动态方法不是全覆盖；选定stats函数来源可重算且公开别名已运行。','未实现Agent reset/权限/状态注册或跨任务隔离；后续封装要冻结目标、保留原始证据并阻止伪造来源。','教程示例中跨领域观测/通用图方法仅用于可追溯状态设计，不声称它们是半导体专用QMS软件。']
        d['runtime_report']=(BASE/f'runtime/quality/{d["scenario_id"]}.json').as_posix();d['runtime_scope']='固定正常、错误、修复与独立结构/统计oracle，仅指定任务。'
        c.write(d)

if __name__=='__main__':main()
