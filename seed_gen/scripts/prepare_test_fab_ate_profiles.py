"""Reviewed scene-oriented ATE/wafer/test selections and executed tasks."""
from seed_gen.scripts.scenario_collection_support import Collection, recipe
from seed_gen.scripts.prepare_test_fab_production_profiles import BASE,RAW,sel,entity
from seed_gen.scripts.prepare_test_fab_statistics_profiles import pandas_symbols

FIXTURE=(BASE/'verify_test_analysis.py').as_posix()

def tables():
    rows=pandas_symbols()
    rows[0]['methods'] += 'pivot pivot_table melt value_counts query drop count nunique'.split()
    rows[1]['methods'] += ['abs']
    rows[2]['methods'] += 'median first last quantile'.split()
    rows.extend([sel('pandas','pandas.core.series','Series','__init__ to_dict to_frame mean std median','table'),
                 sel('pandas','pandas.core.base','IndexOpsMixin','value_counts','table','Series继承的bin计数；不能把DataFrame.value_counts冒充Series定义。'),
                 sel('pandas','pandas.core.groupby.generic','SeriesGroupBy','transform','table','按site/lot中心化后保留原die行身份；由groupby产生。')])
    return rows

def stdf_symbols():
    rows=[sel('Semi-ATE-STDF','Semi_ATE.STDF.utils','records_from_file','__init__ parse_FAR','decode','逐记录流读取；迭代协议属于运行基础，不伪造独立工具。'),
          sel('Semi-ATE-STDF','Semi_ATE.STDF.utils','is_STDF',None,'decode'),
          sel('Semi-ATE-STDF','Semi_ATE.STDF.STDR','STDR','__init__ get_fields get_value set_value to_dict to_json to_atdf get_str_time_stamp reset hexify sys_endian sys_cpu','record','记录公共字段/类型与转换；通过具体记录类构造，保留继承定义。')]
    for name in ('FAR','MIR','MRR','WIR','WRR','PIR','PRR','PTR','TSR','HBR','SBR'):
        rows.append(sel('Semi-ATE-STDF','Semi_ATE.STDF.'+name,name,'__init__','record','保留文件、lot/wafer、器件、测试及bin记录构造，用于读取后字段核查；未声称每类都实跑。'))
    return rows

def stats(names):
    return [sel('scipy','scipy.stats._stats_py',n,None,'statistics','用于稳健中心/散布、组间比较或相关性；参数与数据前提显式保存。') for n in names.split()]

def main():
 c=Collection(base=BASE,raw=RAW)
 def src(url,text):return c.source(url,text,['lot','wafer','die','test','run'],['load','normalize','analyze','observe'],['repair_validate'])
 sd=[src('https://github.com/Semi-ATE/STDF/blob/0.1.33/README.md','官方示例按records_from_file迭代STDF记录，字段可转dict并转换ATDF；不自动定义重测良率口径。'),
     src('https://github.com/Semi-ATE/STDF/blob/0.1.33/tests/test_PRR.py','PRR器件记录包含坐标、part id、hard/soft bin等；该上游测试标记skip，只作字段参考，自己的固定字节样例另行实跑。'),
     src('https://github.com/Semi-ATE/STDF/blob/0.1.33/tests/test_PTR.py','PTR示例按TEST_NUM/HEAD_NUM/SITE_NUM组织参数测试，并设置结果、单位和上下限；参考字段模型不冒称已覆盖全部STDF可选记录。')]
 merge=src('https://pandas.pydata.org/docs/user_guide/merging.html','按显式主键连接并validate基数；同一part id在不同lot可重复，不能按行序或局部id归因。')
 group=src('https://pandas.pydata.org/docs/user_guide/groupby.html','按实体字段分组统计，并可transform返回原行；适用于site中心、lot良率和电压分组工作边界。')
 ni=src('https://www.ni.com/en/solutions/semiconductor.html','半导体测试覆盖研发验证、晶圆级参数测试到量产ATE；输入测试条件/结果与良率统计为应用语境，不连接NI硬件。')
 secom=src('https://archive.ics.uci.edu/dataset/179/secom','SECOM为半导体工艺特征与pass/fail数据，含大量特征和缺失；说明过程/测试关联的实体需求，本轮仅调研未下载或跑该数据。')
 wf=[src('https://github.com/xlhaw/wfmap/blob/1.0.3/README.md','wfmap提供数值/类别热图及wafer map，输入MAP_ROW/MAP_COL及测量或bin列。'),
     src('https://github.com/xlhaw/wfmap/blob/1.0.3/wfmap/__init__.py','发布实现使用df.pivot三个位置参数和类别替换；需显式局部pandas3适配，图例/矩阵须独立检查。'),sd[1]]
 db=src('https://scikit-learn.org/stable/auto_examples/cluster/plot_dbscan.html','DBSCAN按eps/min_samples形成密度连通簇，噪声标签为-1；坐标单位和半径必须一致。')
 classify=src('https://scikit-learn.org/stable/auto_examples/classification/plot_classifier_comparison.html','分类器比较明确玩具数据仅用于理解，不能推导真实准确率；本场景用固定可解释几何特征。')
 leakage=src('https://scikit-learn.org/stable/common_pitfalls.html#data-leakage','训练测试先分离，特征定义与列序一致；修复标签不允许用测试标签拟合预处理。')
 mad=src('https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.median_abs_deviation.html','MAD从中位数计算绝对偏差，对离群点比标准差稳健；scale=normal对正态一致估计作缩放。')
 zscore=src('https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.zscore.html','zscore使用传入样本自身均值和标准差；把待判异常也放入基线可能稀释离群程度。')
 ttest=src('https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ttest_ind.html','独立组比较equal_var=False为Welch检验，必须先统一单位和解释检验前提。')
 fisher=src('https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.fisher_exact.html','2x2固定边际精确检验返回优势比和双侧概率；用组合数枚举独立核对小样本良率比较。')
 pearson=src('https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.pearsonr.html','Pearson量化线性关联，不能把相关系数解释为因果；连接错wafer可反转符号。')
 sh=src('https://en.wikipedia.org/wiki/Shmoo_plot','Shmoo扫描电压/频率等条件并标记DUT通过/失败，观察安全工作区域；为二级场景参考，具体表格操作用pandas发布API。')
 entities=[entity('ate_source','id source_hash format endian record_count revision','登记固定源→完整性/字段检查→解析；源变更使规范表和所有结果失效。'),
           entity('test_table','lot_id wafer_id die_id x y site test_id attempt value unit hard_bin soft_bin revision','绑定跨lot主键和测试条件→单位/重复检查→明确首测或末测口径→保存；不得把测试记录数当die数。'),
           entity('analysis_config','id baseline_ids feature_columns thresholds limits coordinate_unit seed revision','固定基线/坐标/规格与分组→分析；修改配置重新创建结果，跨任务不共用可变模型。'),
           entity('analysis_result','id input_revision config_revision labels metrics plot_file status','运行→独立计数/解析oracle核对→诊断错误并重跑→保存；人工数据不能支持量产准确率结论。')]
 boundaries=['所有运行输入是固定人工STDF字节、晶圆图或量测；未接ATE、未使用真实生产数据或声称行业标准全覆盖。',
             '工具集为参考公开API，只有固定任务路径运行验证；状态ID服务、任务隔离和工具JSON封装仍需后续环境实现。',
             'NumPy掩码、坐标几何摘要和独立解析公式为显式工程层；统计检验/聚类/分类仍调用所选包。']
 def emit(sid,desc,pkgs,symbols,sources,tasks,bridges=(),extra=()):
    c.write(dict(scenario_id=sid,description=desc,packages=pkgs,symbols=symbols,sources=sources,entities=entities,
                 capabilities=list(dict.fromkeys(s['capability'] for s in symbols)),bridges=list(bridges),boundaries=boundaries+list(extra),
                 runtime_infrastructure=[{'reference':'runtime.array_schema_and_fixture_io','reason':'字节样例构造、record长度完整性、数组/掩码、显式特征、主键与单位校验及JSON输出；均在验证脚本公开。'},
                                         {'reference':'runtime.public_alias_and_inheritance','reason':'公开导出别名和pandas继承定义按源码核查；索引器与STDF迭代按语言协议使用。'},
                                         {'reference':'runtime.local_wfmap_adapter','reason':'仅wafer绘图将旧pivot位置参数映射为关键字，并要求绘图矩阵为float；Matplotlib仅保存/读取返回Axes，非替代分析算法。'}],
                 tasks=tasks,runtime_report=(BASE/f'runtime/test_analysis/{sid}.json').as_posix(),runtime_scope='两次重复相同的正常/错误/修复固定任务，独立字节/数组/组合数/解析oracle。'))
 def rec(i,d,initial,refs,assertions):return recipe(i,d,initial,[('构造/核查/诊断/修复/重跑',refs)],assertions,FIXTURE)
 tbl=tables();trefs=['pandas.core.frame.DataFrame.__init__','pandas.core.frame.DataFrame.assign','pandas.core.frame.DataFrame.sort_values','pandas.core.frame.DataFrame.drop_duplicates']
 drefs=['Semi_ATE.STDF.utils.records_from_file.__init__','Semi_ATE.STDF.STDR.STDR.to_dict']
 sdpk=[('Semi-ATE-STDF','primary','STDF记录/字段入口与构造便于固定字节验证。'),('pandas','complement','规范主键表、重测和bin/yield聚合。'),('pystdf','excluded','事件式STDF解析与主包功能重复，保留完整池后择一。')]
 stdfextra=['Semi-ATE-STDF固定正式tag0.1.33/commit；上游安装元数据为0.0.0、PyPI为0.1.28，不能以metadata冒称发布号。',
            '固定样例含FAR/WIR/PRR；截断由显式record长度检查拒绝，源解析器可能静默停止，不声称其自动拒绝所有坏文件。']
 bridge=[{'from':'SemiATE record.to_dict','to':'pandas canonical table','contract':'lot/wafer来自文件上下文，PART_ID跨lot不唯一，attempt是固定样例PART_TXT约定，实际接入需显式映射；首测/末测口径保留。'}]
 emit('05.01.01','STDF解析环境：读取固定发布版记录流，检查端序、记录长度和PRR字段，再建立可审计结果表；SemiATE负责格式解析，完整性检查为显式工程层。',sdpk,stdf_symbols()+tbl,sd,
      [rec('repair_stdf_endian','定位将小端STDF按大端解释的长度错误，按FAR/记录规则恢复解析；比较独立构造的大小端文件，验证六记录及四个器件字段相同。','FAR/WIR/4PRR固定二进制文件。',drefs,['大小端PRR字段完全一致；4器件记录坐标和bin符合字节oracle。']),
       rec('reject_truncated_stdf','检测末尾少两字节的记录长度越界，恢复完整文件重新解析；独立完整性检查拒绝截断，不依赖迭代器是否静默结束。','相同字节内容末尾截断。',drefs,['截断完整性false；修复6记录。'])],bridge,stdfextra)
 emit('05.01.02','Lot/Wafer/Die/Test规范化：把STDF记录映射到稳定复合主键与测试尝试，保留坐标、bin及来源，修复重测和跨lot身份碰撞；规范表可JSON往返。',sdpk,stdf_symbols()+tbl,[sd[0],sd[1],merge],
      [rec('repair_retest_key','从4条PRR识别D2失败后通过，增加attempt并按末测口径生成3die规范表；保存/重载验证主键一致。','同一lot/wafer三die，D2重测。',drefs+trefs+['pandas.core.generic.NDFrame.to_json','pandas.io.json._json.read_json'],['末测3die，D2 hard_bin1。']),
       rec('repair_cross_lot_identity','合并两个lot的同名die测试记录，诊断只用PART_ID/attempt丢失一半数据；补lot/wafer复合键后保留8条。','L01/L02各4记录。',trefs+['pandas.core.reshape.concat.concat'],['错误4，修复8；复合键唯一。'])],bridge,stdfextra)
 emit('05.01.03','Bin与良率分析：在相同lot/wafer/die口径下分别统计首测/末测良率与hard/soft bin，修复重测分母和bin语义混用；不把记录均值直接当最终良率。',sdpk,stdf_symbols()+tbl,[sd[1],sd[2],group],
      [rec('repair_yield_denominator','解析D2重测的4记录，分别取首测与末测并统计通过；纠正记录通过率0.5，验收首测1/3、最终2/3。','三die中D1通过/D2重测通过/D3失败。',drefs+trefs,['分母die3；首测1/3，末测2/3。']),
       rec('separate_hard_soft_bins','按最终测试生成hard和soft计数，排除被重测覆盖的soft10并防止把soft20当hard分类。','相同固定STDF。',drefs+trefs+['pandas.core.base.IndexOpsMixin.value_counts'],['hard{1:2,2:1}；soft{1:2,20:1}；各总数3。'])],bridge,stdfextra)
 wfsy=[sel('wfmap','wfmap',n,None,'visualize','晶圆坐标和类别/数值可视化、图例和相关展示；旧pandas接口由显式局部适配。') for n in 'auto_vlim num_heatmap cat_heatmap create_incmap wafermap defectmap wif_trend wif_trends twin_trends wif_corrplot'.split()]
 emit('05.02.01','Wafer-map可视化：按die行列坐标输出数值/类别图，独立核对矩阵方向和bin图例；wfmap发布版配pandas3需显式局部pivot兼容层。',[('wfmap','primary','晶圆图的高层绘制入口。'),('pandas','complement','坐标/类别表及局部兼容桥。')],wfsy+tbl,wf,
      [rec('repair_wafer_coordinates','绘制四die数值图并发现坐标转置，修复行列映射后重画，核对二维矩阵而非只看图片非空。','值1/2/3/4，2×2坐标。',['wfmap.num_heatmap','pandas.core.frame.DataFrame.rename','pandas.core.frame.DataFrame.pivot'],['错误[[1,3],[2,4]]；正确[[1,2],[3,4]]，PNG存在。']),
       rec('verify_bin_map_legend','绘制P/P/F/P类别图并读取类别计数和颜色码；检查P3/F1且不同类别编码不同，保存PNG。','同四die类别。',['wfmap.cat_heatmap','pandas.core.base.IndexOpsMixin.value_counts'],['图例P3/F1，代码不同。'])],extra=['wfmap1.0.3原始pivot三个位置参数在pandas3抛TypeError；只用实例WfFrame适配参数与float矩阵，不修改发布源码。'])
 cluster=[sel('scikit-learn','sklearn.cluster._dbscan','DBSCAN','__init__ fit fit_predict','cluster'),sel('scikit-learn','sklearn.base','BaseEstimator','get_params set_params','model')]
 emit('05.02.02','空间缺陷聚类：以统一die-pitch坐标执行DBSCAN，显式配置半径/邻居并区分连通簇与噪声，诊断过大半径或长度单位错配；不强制每个die分到簇。',[('scikit-learn','primary','公开密度聚类和参数状态。'),('pandas','complement','die身份/坐标及簇标签持久化。')],cluster+tbl,[db,wf[0],secom],
      [rec('repair_spatial_radius','对两个四点方形簇和一点噪声，定位eps20合并所有点；改eps1.1/min_samples3，验收两簇和噪声索引8。','9个固定坐标。',['sklearn.cluster._dbscan.DBSCAN.__init__','sklearn.cluster._dbscan.DBSCAN.fit_predict'],['标签0×4,1×4,-1；基于距离独立已知分组。']),
       rec('repair_die_coordinate_units','对被乘1000的坐标发现全部变噪声，统一回die-pitch并重跑；验收与原单位结果一致。','坐标毫米/微米类比例错误。',['sklearn.cluster._dbscan.DBSCAN.__init__','sklearn.cluster._dbscan.DBSCAN.fit_predict'],['错误全-1；恢复两簇与噪声。'])])
 tree=[sel('scikit-learn','sklearn.tree._classes','DecisionTreeClassifier','__init__ fit predict_proba','classify'),sel('scikit-learn','sklearn.tree._classes','BaseDecisionTree','predict get_depth get_n_leaves feature_importances_ decision_path','classify'),sel('scikit-learn','sklearn.base','BaseEstimator','get_params set_params','model'),sel('scikit-learn','sklearn.metrics._classification','confusion_matrix',None,'observe')]
 tr=['sklearn.tree._classes.DecisionTreeClassifier.__init__','sklearn.tree._classes.DecisionTreeClassifier.fit','sklearn.tree._classes.BaseDecisionTree.predict','sklearn.metrics._classification.confusion_matrix']
 emit('05.02.03','Wafer缺陷图样分类：对center/edge/scratch人工图计算显式几何摘要，固定浅树训练预测，校验标签与特征定义；不把小图示例声称为WM811K训练或生产分类器。',[('scikit-learn','primary','浅树分类与混淆矩阵。'),('pandas','complement','wafer身份、特征列及标签表。')],tree+tbl,[classify,leakage,wf[0]],
      [rec('repair_pattern_labels','训练标签循环错配造成类别错误，纠正9个训练样本标签后重训，对3个测试图验收center/edge/scratch。','7×7人工图；中心/边缘/对角占比及失败数。',tr,['三测试标签正确，混淆矩阵单位阵；无真实准确率结论。']),
       rec('repair_pattern_feature_definition','发现测试中心占比误取1-占比，恢复与训练一致的特征计算后推断；保留训练/测试分离并验收三类。','固定模型与三测试图。',tr,['错误定义预测不符，修复全部一致。'])],extra=['几何摘要为明确工程层，不隐藏另一个图像模型；未下载/训练WM811K。'])
 emit('05.03.01','Shmoo工作域分析：在电压×频率扫描表中标记pass/fail，固定单位与唯一网格，形成工作边界并检查重复条件；pandas承载工程表层，没有伪称专用ATE控制API。',[('pandas','primary','规范扫描网格/分组边界和持久化。')],tbl,[sh,ni,group],
      [rec('repair_shmoo_units','把频率错记为GHz量级后边界缩小1000倍，恢复MHz并按电压分组取通过上界，验收100/110/120MHz。','V1/1.1/1.2×MHz90/100/110/120，人工pass条件f≤100v。',['pandas.core.frame.DataFrame.pivot','pandas.core.frame.DataFrame.assign','pandas.core.frame.DataFrame.groupby','pandas.core.groupby.groupby.GroupBy.max'],['3×4网格；固定三条边界。']),
       rec('reject_duplicate_shmoo_cell','注入重复电压/频率单元，pivot应拒绝多值；修复唯一键后重建3×4网格并与独立真值比较。','12格+1重复行。',['pandas.core.reshape.concat.concat','pandas.core.frame.DataFrame.pivot','pandas.core.frame.DataFrame.duplicated'],['重复ValueError；12唯一格。'])])
 analytic=[('scipy','primary','稳健散布、独立组检验与相关性入口。'),('pandas','complement','lot/site/测试表、单位和受约束连接。')]
 limitstats=stats('median_abs_deviation zscore iqr trim_mean sem describe')
 emit('05.03.02','测试规格与guardband：把量测、规格和单位分开，检查上下限顺序，比较原规格及收紧边界的接受集合；不将数据统计自动当设计规格。',analytic,limitstats+tbl,[mad,sd[2],ni],
      [rec('repair_limit_order','发现下限10.5大于上限9.5导致全拒绝，纠正为9.5/10.5；计算中位数与MAD并验收七个量测全通过。','9.7到10.3步0.1。',['scipy.stats._stats_py.median_abs_deviation'],['median10/MAD0.2；通过7。']),
       rec('apply_limit_guardband','在相同测量上收紧上下限各0.3，验收新边界9.8/10.2和通过5个，并标记原先通过现被挡住的两端点。','固定规格9.5/10.5。',['scipy.stats._stats_py.median_abs_deviation'],['拒绝索引0/6；通过5。'])])
 emit('05.04.01','参数测试PAT离群筛查：固定正常基线与稳健MAD尺度，按site分组区分系统偏移和组内异常；对待判样本混入基线的错误进行修复，阈值有明确统计口径。',analytic,limitstats+tbl,[mad,zscore,group],
      [rec('repair_pat_baseline','对正常9/10/11附近及异常50，诊断全样本zscore稀释异常而不报3sigma；固定前七正常基线并用normal-scaled MAD，验收仅索引7异常。','7正常+1待判高值。',['scipy.stats._stats_py.zscore','scipy.stats._stats_py.median_abs_deviation'],['错误无异常，修复仅7。']),
       rec('separate_site_offset','将同分布两site整体相差100的数据按site中位数中心化，验收中心10/110与组内残差≤1，避免把全部B site当单die离群。','A/B各七点。',['pandas.core.frame.DataFrame.groupby','pandas.core.groupby.groupby.GroupBy.median','pandas.core.groupby.generic.SeriesGroupBy.transform'],['两site中心明确，组内无3sigma离群。'])])
 emit('05.05.01','Lot/Split-lot比较：统一条件与单位后比较连续量测和通过计数，输出Welch统计量与Fisher精确概率；数据表保留组别与来源，不把显著性直接当工程因果。',analytic,stats('ttest_ind fisher_exact median_abs_deviation describe sem')+tbl,[ttest,fisher,ni],
      [rec('repair_lot_measurement_unit','比较两组均值相差2的六点量测，发现B单位缩小1000倍；恢复统一单位并计算Welch检验，用独立方差公式验收t。','A0/0/1/1/2/2；B=A+2。',['scipy.stats._stats_py.ttest_ind'],['t与手算一致，p<0.01。']),
       rec('compare_split_lot_yield','比较8通过2失败与2通过8失败的两个split lot，计算双侧Fisher并用固定边际组合数枚举核对。','2×2列为通过/失败。',['scipy.stats._stats_py.fisher_exact'],['OR16；双侧p与独立枚举一致。'])])
 emit('05.05.02','测试-工艺跨域关联：用稳定wafer主键连接良率和压力等过程摘要，约束一对一映射并报告线性关联；修复乱序连接和重复键，输出可追溯证据而非自动认定根因。',analytic,stats('pearsonr spearmanr ttest_ind describe median_abs_deviation')+tbl,[merge,pearson,secom],
      [rec('repair_cross_domain_join','对乱序压力和四片良率，定位按位置相关性符号相反；改wafer键连接后计算Pearson，用独立协方差公式验收负相关。','yieldA.9/B.8/C.5/D.4，pressureD4/C3/B2/A1。',['pandas.core.frame.DataFrame.merge','scipy.stats._stats_py.pearsonr'],['错误相关正；修复<-0.97且解析一致。']),
       rec('reject_rca_duplicate_key','向过程表加入重复wafer，确认one_to_one拒绝；清理输入键后重连，验收四行且无隐式笛卡尔积。','四wafer+重复过程行。',['pandas.core.frame.DataFrame.merge','pandas.core.reshape.concat.concat'],['MergeError；修复4行。'])],bridges=[{'from':'test yield per wafer','to':'process feature per wafer','contract':'lot/wafer和revision一致，一对一主键；重复键拒绝，不按位置关联，不解释为因果。'}])

if __name__=='__main__':main()
