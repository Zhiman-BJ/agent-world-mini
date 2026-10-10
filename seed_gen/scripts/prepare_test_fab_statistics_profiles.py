"""Reviewed FDC/SPC selections from all released candidate pools."""
from seed_gen.scripts.scenario_collection_support import Collection, recipe
from seed_gen.scripts.prepare_test_fab_production_profiles import BASE,RAW,sel,entity

FIXTURE=(BASE/'verify_statistics.py').as_posix()

def pandas_symbols(time=False):
    rows=[sel('pandas','pandas.core.frame','DataFrame','__init__ shape from_records to_records to_numpy to_dict assign rename set_index reset_index isna dropna duplicated drop_duplicates sort_values groupby merge corr mean std quantile','table','构建wafer/lot/trace表，核对主键/缺失/单位并连接结果；不暴露任意文件路径读写以外的远程数据库。'),
          sel('pandas','pandas.core.generic','NDFrame','copy astype equals to_json to_csv describe','table','DataFrame继承的复制、类型、摘要与持久化；基类不直接构造。'),
          sel('pandas','pandas.core.groupby.groupby','GroupBy','count size mean std sum min max','table','按wafer/lot/step聚合；由DataFrame.groupby获得，不直接构造。'),
          sel('pandas','pandas.io.parsers.readers','read_csv',None,'table'),sel('pandas','pandas.io.json._json','read_json',None,'table'),
          sel('pandas','pandas.core.reshape.concat','concat',None,'table')]
    if time:
        rows.extend([sel('pandas','pandas.core.series','Series','__init__ to_dict to_frame sort_index isna dropna mean std','table'),
                     sel('pandas','pandas.core.indexes.datetimes','date_range',None,'table'),sel('pandas','pandas.core.tools.datetimes','to_datetime',None,'table')])
    return rows

def feature_symbols(calculators=False):
    rows=[sel('tsfresh','tsfresh.feature_extraction.extraction','extract_features',None,'features','显式column_id/sort/value和特征配置，从完整轨迹生成一行/wafer的特征表。'),
          sel('tsfresh','tsfresh.feature_extraction.settings','MinimalFCParameters','__init__','features'),
          sel('tsfresh','tsfresh.feature_extraction.settings','EfficientFCParameters','__init__','features'),
          sel('tsfresh','tsfresh.feature_extraction.settings','from_columns',None,'features'),
          sel('tsfresh','tsfresh.utilities.dataframe_functions','impute',None,'features'),
          sel('tsfresh','tsfresh.utilities.dataframe_functions','roll_time_series',None,'features'),
          sel('tsfresh','tsfresh.feature_selection.selection','select_features',None,'features'),
          sel('tsfresh','tsfresh.transformers.feature_augmenter','FeatureAugmenter','__init__ set_timeseries_container fit transform','features'),
          sel('tsfresh','tsfresh.transformers.feature_selector','FeatureSelector','__init__ fit transform','features')]
    if calculators:
        for name in 'mean median minimum maximum variance standard_deviation abs_energy root_mean_square mean_abs_change linear_trend number_peaks autocorrelation quantile'.split():
            rows.append(sel('tsfresh','tsfresh.feature_extraction.feature_calculators',name,None,'features','保留常见压力/温度/RF轨迹特征诊断，与高层批量抽取共享算法，不再加不相关频谱长尾。'))
    return rows

def spc_symbols():
    p='src.spc_lib.'
    return [sel('spc-lib',p+'charts.variables',name,'__init__ fit','control') for name in ('IMRChart','XBarRChart','XBarSChart')]+[
        sel('spc-lib',p+'charts.attributes',name,'__init__ fit','control') for name in ('PChart','CChart')]+[
        sel('spc-lib',p+'charts.time_weighted','EWMAChart','__init__ fit get_ewma_values','control'),
        sel('spc-lib',p+'charts.time_weighted','CUSUMChart','__init__ fit get_cusum_stats','control'),
        sel('spc-lib',p+'core.base_chart','BaseControlChart','capability','capability','控制图继承的Cp/Cpk入口，不直接构造抽象图；中心/控制限是对象状态属性。'),
        sel('spc-lib',p+'rules.western_electric','detect_violations',None,'rules','按明确规则编号/窗口和固定sigma返回命中；包内编号与传统WE命名须显式映射。'),
        sel('spc-lib',p+'stats.diagnostics','diagnose',None,'control','检查正态/自相关等前提；不能用控制限当规格限。')]

def main():
 c=Collection(base=BASE,raw=RAW)
 def src(url,text):return c.source(url,text,['trace','wafer','baseline','model'],['load','configure','fit','observe'],['detect_repair_validate'])
 ts=[src('https://tsfresh.readthedocs.io/en/latest/text/quick_start.html','官方机器人故障示例从多传感器轨迹、id和故障标签抽取特征；可迁移实体结构而非冒称真实半导体训练数据。'),
     src('https://tsfresh.readthedocs.io/en/latest/text/data_formats.html','column_id/sort/value/kind定义实体、排序和测量量；结果每id一行，关键列不可NaN/Inf。'),
     src('https://tsfresh.readthedocs.io/en/latest/text/feature_extraction_settings.html','default_fc_parameters把计算器映射到参数列表；可固定少量特征并追溯输出列名。')]
 cp=[src('https://centre-borelli.github.io/ruptures-docs/user-guide/detection/pelt/','PELT用惩罚项控制分段、min_size控制间隔、jump限制候选网格；高惩罚或粗网格可能漏掉真实切换。'),
     src('https://centre-borelli.github.io/ruptures-docs/user-guide/costs/costl2/','CostL2定义为每段相对段均值的平方和，明确均值变化假设，可用独立SSE验收。'),
     src('https://centre-borelli.github.io/ruptures-docs/user-guide/detection/dynp/','Dynp给定变点数最小化段代价；说明网格与计算复杂度。')]
 ad=[src('https://adtk.readthedocs.io/en/stable/userguide.html','ADTK面向无监督/规则异常；区分离群点、spike和level shift，需要人工定义异常类型。'),
     src('https://adtk.readthedocs.io/en/stable/api/detectors.html','ThresholdAD按绝对阈值，IQR等从历史训练；各检测器可经Pipeline统一fit/detect。'),
     src('https://adtk.readthedocs.io/en/stable/api/data.html','validate_series、事件/标签转换与时间序列划分；DatetimeIndex是检查事件时间语义的输入边界。')]
 rv=[src('https://riverml.xyz/latest/api/drift/PageHinkley/','Page-Hinkley逐点更新，threshold/min_instances/alpha控制检测，只有漂移无warning区域。'),
     src('https://riverml.xyz/latest/api/drift/ADWIN/','ADWIN维护可变长窗口并比较子窗均值；width/estimation与更新顺序是可观察状态。'),
     src('https://github.com/online-ml/river/blob/0.26.1/river/drift/page_hinkley.py','发布源码展示update后查询drift_detected、漂移后内部重置与增减方向；独立任务每次新建检测器。')]
 sp=[src('https://github.com/denccchick/spc-lib/blob/1.0.0/examples/demonstration.ipynb','发布例从日期和五个晶圆测量列构建控制图并诊断；输入二维子组，文档示例不等于可直接pip导入。'),
     src('https://github.com/denccchick/spc-lib/blob/1.0.0/src/spc_lib/charts/variables.py','IMR用baseline_mask、相邻基线MR均值/1.128估计sigma；控制限和中心为状态。'),
     src('https://github.com/denccchick/spc-lib/blob/1.0.0/src/spc_lib/core/base_chart.py','capability从控制图估计sigma计算Cp/Cpk，允许单侧规格；必须先fit且不能混淆规格与控制限。'),
     src('https://github.com/denccchick/spc-lib/blob/1.0.0/src/spc_lib/rules/western_electric.py','规则1为单点>3sigma，规则5为三点中两点>=2sigma同侧并标整个窗口；不是无说明的标准WE编号。')]
 merge=src('https://pandas.pydata.org/docs/user_guide/merging.html','pandas merge按键连接，validate约束one_to_one等基数；位置拼接会把错wafer特征归到OOC结果。')
 leakage=src('https://scikit-learn.org/stable/common_pitfalls.html#data-leakage','官方要求先划分再拟合预处理，并保持训练/推断同一特征空间；固定模型不能用测试集调参。')
 entities=[entity('trace','id wafer_id equipment_id step_id time value unit revision','加载→主键/单位/排序核查→按step分组；改测量或映射使旧特征/判定失效。'),
           entity('feature_table','trace_revision wafer_id columns values missing_policy','按固定配置抽取→查NaN/列序→保存；所有训练/预测按列名对齐。'),
           entity('baseline_model','id train_ids config center sigma threshold revision','仅训练基线→固定配置推断→观察诊断→保存参数；reset新建对象且清除在线历史。'),
           entity('finding','id input_revision indices timestamps rule confidence status','从当前模型和数据产生→对独立目标验收→关联wafer；不将关联自动解释为因果。')]
 boundaries=['输入为人工固定压力/量测数据，未连接真实机台，未衡量真实晶圆异常检出率。','保留完整候选PythonAPI后联合筛选；原生后端仅通过公开Python包装器使用，未冒称原生导出表全部解析。',
             '静态选集包含未实跑方法；固定任务重复两次相同，不是完整Agent环境验收。','Array算术、掩码、JSON和ID校验为工程/验证基础设施，包求解与模型训练实际调用发布API。']
 def emit(sid,desc,packages,symbols,sources,tasks,bridges=(),spc=False):
    extra=[]
    if spc:extra=['spc-lib1.0.0安装包import失败（内部src.spc_lib绝对导入）；必须显式把固定未修改发布仓库根加sys.path并使用src.spc_lib，选集记录该真实namespace。不是透明安装可用。',
                  '该发布版规则编号混合额外运行规则；任务明确规则1/5含义，不称已完整符合某SEMI/WE标准。']
    c.write(dict(scenario_id=sid,description=desc,packages=packages,symbols=symbols,sources=sources,entities=entities,
                 capabilities=list(dict.fromkeys(s['capability'] for s in symbols)),bridges=list(bridges),
                 runtime_infrastructure=[{'reference':'runtime.array_and_fixture_io','reason':'NumPy数组形状/显式掩码、基础算术/JSON与固定输入生成；不另行替代检测/拟合。'},
                                         {'reference':'runtime.source_namespace_bootstrap','reason':'仅SPC任务把已固定发布根加sys.path并import src.spc_lib；源码保持干净。'},
                                         {'reference':'runtime.public_alias_and_inheritance','reason':'pandas读写/索引、sklearn继承predict、River drift_detected状态属性和ADTK Pipeline组合按源码和实跑确认；不伪造动态或继承方法。'}],
                 boundaries=boundaries+extra,tasks=tasks,runtime_report=(BASE/f'runtime/statistics/{sid}.json').as_posix(),
                 runtime_scope='固定正常/错误/修复数据，真实特征/分类/变点/规则/控制图API；独立数组或解析oracle，两次一致。',
                 count_exception='49个操作已覆盖在线更新、窗口、统计、观测、重建和参数状态；不为达到50加入另一种未需检测器。' if sid=='06.02.04' else ''))
 def rec(identifier,desc,initial,steps,assertions):return recipe(identifier,desc,initial,steps,assertions,FIXTURE)
 fref=['pandas.core.frame.DataFrame.__init__','pandas.core.frame.DataFrame.assign','tsfresh.feature_extraction.extraction.extract_features']
 emit('06.02.01','FDC轨迹特征环境：以wafer/step/time/value/unit表示测量，固定特征配置后生成一行一晶圆的特征表，检查单位、身份和列语义；tsfresh负责计算，pandas负责规范表格。',
      [('tsfresh','primary','轨迹到工程特征的明确高层入口。'),('pandas','complement','带主键表格、单位赋值及特征表保存。')],feature_symbols(True)+pandas_symbols(),ts,
      [rec('repair_trace_units','从两条固定压力轨迹抽取均值、最大值、能量和方差，定位Pa误当kPa；统一除1000后重算，验收A的1.5/3/14/1.25。','A=[0,1000,2000,3000]Pa，B=[1000]*4Pa。',[('构造/改单位/抽取',fref)],['A独立解析特征1.5/3/14/1.25；B均值1。']),
       rec('repair_trace_identity','诊断两晶圆轨迹误用同一id导致合并为一行；恢复A/B身份重新抽特征，验证两行且B均值1kPa。','8行测量、两个wafer，各4时间点。',[('按真实wafer分组抽取',fref)],['错误1行，修复2行，id为A/B。'])],bridges=[{'from':'pandas DataFrame','to':'tsfresh extract_features','contract':'wafer为column_id，time为排序，pressure统一kPa；输出按wafer索引，不能按返回行位置拼接。'}])
 changes=[sel('ruptures','ruptures.detection.'+module,name,'__init__ fit predict fit_predict','segment') for module,name in [('pelt','Pelt'),('dynp','Dynp'),('binseg','Binseg'),('window','Window')]]+[
          sel('ruptures','ruptures.costs.costl2','CostL2','__init__ fit error','cost'),sel('ruptures','ruptures.costs.costl1','CostL1','__init__ fit error','cost'),
          sel('ruptures','ruptures.costs.costrbf','CostRbf','__init__ fit error','cost'),sel('ruptures','ruptures.base','BaseCost','sum_of_costs','cost'),
          sel('ruptures','ruptures.metrics.precisionrecall','precision_recall',None,'observe'),sel('ruptures','ruptures.metrics.hausdorff','hausdorff',None,'observe')]
 emit('06.02.02','工艺step切换与离线漂移分段：用ruptures配置代价、惩罚与候选间隔，并把索引边界映射回轨迹时间；已知阶跃用于检验漏检与粗网格错误，sktime的广泛框架不重复引入。',
      [('ruptures','primary','专门离线分段，PELT和Dynp固定样例可独立验收。'),('pandas','complement','轨迹整理和时间/实体映射。'),('sktime','excluded','可提供更广时间序列框架，但当前不需第二套适配/模型生命周期。')],changes+pandas_symbols(),cp,
      [rec('repair_change_penalty','分段0/5/0各30点的压力轨迹，发现过大pen只返回末端；调整惩罚后验收边界30/60/90并保持段均值。','无噪声阶跃，PELT model=l2。',[('拟合并调惩罚',['ruptures.detection.pelt.Pelt.__init__','ruptures.detection.pelt.Pelt.fit','ruptures.detection.pelt.Pelt.predict'])],['错误[90]，修复[30,60,90]。']),
       rec('repair_change_grid','对37/35/28点三段轨迹诊断jump=5不能命中真实切换；改jump=1重跑Dynp两变点，验收37/72/100且独立段内SSE=0。','指定两个真实变点，候选网格初值5。',[('修复候选间隔',['ruptures.detection.dynp.Dynp.__init__','ruptures.detection.dynp.Dynp.fit','ruptures.detection.dynp.Dynp.predict'])],['粗网格不精确，修复真实索引；末端100不是额外变点。'])])
 ads=[sel('adtk','adtk.pipe._pipe','Pipeline','__init__ fit detect transform fit_detect score get_params','detect'),
      sel('adtk','adtk.pipe._pipe','Pipenet','__init__ fit detect transform fit_detect score get_params summary','detect')]
 for name in ('ThresholdAD','InterQuartileRangeAD','QuantileAD','PersistAD','LevelShiftAD','VolatilityShiftAD'):ads.append(sel('adtk','adtk.detector._detector_1d',name,'__init__','detect','检测器交给所选Pipeline/Pipenet调用，保留公开构造；继承predict不伪造顶层定义。'))
 for name in ('validate_series','validate_events','to_events','to_labels','expand_events','split_train_test'):ads.append(sel('adtk','adtk.data._data',name,None,'data'))
 for name in ('precision','recall','f1_score','iou'):ads.append(sel('adtk','adtk.metrics._metrics',name,None,'observe'))
 emit('06.02.03','轨迹规则/模式异常环境：显式选择阈值、历史IQR或局部变化检测器，用ADTK管道串接并在有时间索引的轨迹上产生事件；固定样例验证阈值与索引契约，不自动发现故障语义。',
      [('adtk','primary','规则异常与Pipeline/Pipenet组合。'),('pandas','complement','DatetimeIndex序列/表格是明确输入对象。')],ads+pandas_symbols(True),ad,
      [rec('repair_anomaly_threshold','把固定压力异常平台送入ADTK管道，诊断高阈值10漏报；调整为3后验收仅第3/4点为异常。','7点[0,.1,0,4,4.1,0,.1]，秒级DatetimeIndex。',[('构造并检测',['adtk.detector._detector_1d.ThresholdAD.__init__','adtk.pipe._pipe.Pipeline.__init__','adtk.pipe._pipe.Pipeline.detect','adtk.data._data.validate_series'])],['错误无报警；修复布尔向量F/F/F/T/T/F/F。']),
       rec('repair_time_index','检测无DatetimeIndex的轨迹应被拒绝；恢复秒级索引，以独立正常基线训练IQR管道后推断，验收第3/4点异常并保留时间身份。','20点[-1,0,1,0]重复基线，7点测试。',[('修复序列并拟合',['pandas.core.series.Series.__init__','pandas.core.indexes.datetimes.date_range','adtk.detector._detector_1d.InterQuartileRangeAD.__init__','adtk.pipe._pipe.Pipeline.fit','adtk.pipe._pipe.Pipeline.detect'])],['RangeIndex拒绝；训练/测试时间段分离，修复命中3/4。'])])
 rvs=[sel('river','river.drift.adwin','ADWIN','__init__ width n_detections variance total estimation update','drift'),
      sel('river','river.drift.page_hinkley','PageHinkley','__init__ update','drift'),sel('river','river.base.base','Base','clone mutate','state')]
 for module,name,methods in [('mean','Mean','__init__ update update_many revert get'),('var','Var','__init__ n update revert update_many get'),('ewmean','EWMean','__init__ update get'),('ewvar','EWVar','__init__ update get'),('minimum','Min','__init__ update get'),('maximum','Max','__init__ update get'),('quantile','Quantile','__init__ update get'),('quantile','RollingQuantile','__init__ update get window_size')]:rvs.append(sel('river','river.stats.'+module,name,methods,'statistics','在线基线中心/散布/分位状态，用于诊断单位、冷启动和漂移前后窗口；不加离线模型。'))
 rvs.extend([sel('river','river.preprocessing.scale','StandardScaler','__init__ learn_one transform_one','statistics'),sel('river','river.utils.rolling','Rolling','__init__ window_size update','statistics'),sel('river','river.utils.rolling','TimeRolling','__init__ update','statistics')])
 emit('06.02.04','设备逐片/逐点在线漂移监测：River维护可观察窗口、均值方差和检测器历史，按固定顺序更新并查询漂移，诊断阈值与跨任务状态污染；已知均值阶跃只验证特定延迟目标。',
      [('river','primary','流式更新与漂移状态属于同一对象生命周期。')],rvs,rv,
      [rec('repair_drift_threshold','逐点输入100个0后80个5，发现PageHinkley过高阈值不报警；改threshold=10、alpha=1重新回放，验收首次报警在100–105且此前无报警。','180点人工流，known_change=100。',[('重建并更新',['river.drift.page_hinkley.PageHinkley.__init__','river.drift.page_hinkley.PageHinkley.update'])],['坏阈值无报警；正确延迟≤5，不把该延迟推广为所有数据保证。']),
       rec('reset_drift_run','用ADWIN检测同一阶跃并保存窗口指标；为下一条独立平稳流新建检测器，验证初始width=0/mean=0、100点后mean=0且无报警。','两个任务流不得共享历史。',[('更新/观察/重置',['river.drift.adwin.ADWIN.__init__','river.drift.adwin.ADWIN.update','river.drift.adwin.ADWIN.width','river.drift.adwin.ADWIN.estimation'])],['阶跃报警在100–139；新模型平稳均值0，无漂移。'])])
 tree=[sel('scikit-learn','sklearn.tree._classes','DecisionTreeClassifier','__init__ fit predict_proba','classify'),
       sel('scikit-learn','sklearn.tree._classes','BaseDecisionTree','predict get_depth get_n_leaves feature_importances_ decision_path','classify','DecisionTreeClassifier继承这些公开预测/诊断方法，基类不直接构造。'),
       sel('scikit-learn','sklearn.base','BaseEstimator','get_params set_params','classify'),
       sel('scikit-learn','sklearn.model_selection._split','GroupShuffleSplit','__init__ split','split'),
       sel('scikit-learn','sklearn.metrics._classification','accuracy_score',None,'observe'),sel('scikit-learn','sklearn.metrics._classification','confusion_matrix',None,'observe')]
 tref=['sklearn.tree._classes.DecisionTreeClassifier.__init__','sklearn.tree._classes.DecisionTreeClassifier.fit','sklearn.tree._classes.BaseDecisionTree.predict','sklearn.metrics._classification.accuracy_score','sklearn.metrics._classification.confusion_matrix']
 emit('06.02.05','良/坏wafer工艺轨迹分类：tsfresh抽取可追溯特征，sklearn固定浅树训练与预测，pandas保持wafer身份和特征列。以标签错误和列错序为可修复任务，严格分离训练/测试wafer，不把玩具准确率当生产指标。',
      [('tsfresh','primary','固定波形到特征抽取。'),('scikit-learn','complement','浅树训练/预测和独立混淆矩阵。'),('pandas','complement','索引、列与身份保持。')],feature_symbols()+tree+pandas_symbols(),[ts[0],ts[1],leakage],
      [rec('repair_trace_labels','从12个wafer轨迹抽特征，按固定8训练/4测试划分，发现训练标签反转；修复标签重新训练深度1树，验收测试0/0/1/1且混淆矩阵对角2/2。','wafer均值正常约0–1、异常约5–6，固定随机种子17。',[('抽取特征',fref),('训练与验收',tref)],['错标签预测不符；修复准确率1，仅指四个人工测试wafer。']),
       rec('repair_feature_schema','用已训练模型预测故意反序的特征列并确认拒绝；按feature_names_in_恢复列序后预测，验收固定标签且训练测试id无交集。','固定模型与相同测试数据，列序错误。',[('预测与修复列契约',tref)],['错误列顺序ValueError；修复预测0/0/1/1。'])],bridges=[{'from':'tsfresh feature table','to':'sklearn DecisionTreeClassifier','contract':'wafer索引绑定标签，训练/测试id分离；列名和顺序由训练feature_names_in_固定，不能只按数组位置拼接。'}])
 sp_packages=[('spc-lib','primary','固定发布控制图、规则和能力指标；导入缺陷通过明确源码namespace方式验证。'),('pandas','complement','晶圆量测/基线掩码/关联/持久化。'),('pyspc','excluded','另一套控制图DSL与图形状态重复，当前直接数值结果与固定基线更适合验收。')]
 sprefs=['src.spc_lib.charts.variables.IMRChart.__init__','src.spc_lib.charts.variables.IMRChart.fit']
 emit('06.03.01','晶圆工艺量测SPC控制图：显式选择阶段I基线和二维子组，构建I-MR/Xbar-R等控制图；诊断错误基线或数组形状，验收中心、sigma和异常点。spc-lib1.0.0必须按说明进行源码namespace加载。',sp_packages,spc_symbols()+pandas_symbols(),sp[:3],
      [rec('repair_spc_baseline','针对前20点±0.5正常、后10点4.5/5.5偏移的量测，定位把异常段当基线造成漏检；改前20点基线重建IMR，验收中心0、UCL=3/1.128并命中后10点。','二维30×1量测，错误baseline_mask指向后10点。',[('拟合并修复基线',sprefs)],['错中心5；修复0，后10点均超上限。']),
       rec('repair_subgroup_shape','确认单列IMR数据误传一维会报轴错误；改30×1并按正常基线fit，验收MR sigma=1/1.128且行数/wafer顺序不变。','同一30点量测。',[('构造二维图',sprefs+['pandas.core.frame.DataFrame.to_numpy'])],['一维AxisError；二维可拟合。'])],spc=True)
 rule=['src.spc_lib.rules.western_electric.detect_violations']
 emit('06.03.02','SPC运行规则检查：以固定中心/sigma和按时间排序量测配置规则窗口，输出原序列违规索引并追溯规则编号。明确spc-lib规则1与规则5对应单点3sigma和三点中两点2sigma，不混用标准名称。',sp_packages,spc_symbols()+pandas_symbols(),[sp[1],sp[2],sp[3]],
      [rec('repair_rule_sigma','对[0,.2,3.5,.1,0]量测检查单点3sigma规则，发现sigma误设2漏报；修正sigma=1后验收只索引2违规。','center=0，rule1，完整窗口。',[('重查规则',rule)],['错误无违规；修复[2]。']),
       rec('enable_two_of_three','对[2.2,2.5,-.1]量测发现仅单点3sigma规则漏掉连续偏移；启用该版本规则5，独立计数同侧两点超过2sigma，验收标记整个0/1/2窗口。','center=0 sigma=1，版本规则映射明确。',[('选择规则窗口',rule)],['rule1空，rule5覆盖窗口0/1/2。'])],spc=True)
 cap=sprefs+['src.spc_lib.core.base_chart.BaseControlChart.capability']
 emit('06.03.03','过程能力Cp/Cpk环境：在已稳定基线图上给定同单位上下规格限，分别计算潜在能力和居中影响；验证单位错误与Cp/Cpk混淆，不能将控制限作为规格或由不稳定过程证明制造能力。',sp_packages,spc_symbols()+pandas_symbols(),sp[:3],
      [rec('repair_specification_unit','对±1交替量测拟合IMR，发现规格±6误录为±.006；统一单位后计算能力，验收sigma=2/1.128、Cp=Cpk=1.128。','20×1数据，真实LSL/USL=-6/+6。',[('拟合与能力计算',cap)],['错误Cp<.01；修复1.128。']),
       rec('separate_cp_cpk','将相同散布的过程中心移到0.6并重新拟合，在相同±6规格下比较指标；验收Cp仍1.128而Cpk降为1.0152，并纠正把Cp当实际最差侧能力的结论。','均值偏移但MR不变。',[('比较两个拟合图',cap)],['解析Cp不变，Cpk=5.4/(3*(2/1.128))。'])],spc=True)
 emit('06.03.04','SPC与FDC关联：将wafer量测OOC标记按稳定主键连接轨迹特征，诊断位置连接和重复键导致的归因错误；spc-lib负责控制图、tsfresh提供波形特征、pandas执行受基数约束的桥接。',
      sp_packages+[('tsfresh','complement','仅补充压力轨迹特征，避免隐藏手写替代提取器。')],spc_symbols()+pandas_symbols()+feature_symbols(),[sp[1],ts[1],merge],
      [rec('repair_wafer_trace_join','从六片量测识别W4/W5的OOC，连接乱序压力特征时发现按位置错配为0；改wafer键one_to_one连接，验收OOC晶圆压力均值5/6。','前四片基线，W4/W5量测3/4，轨迹特征返回乱序。',[('生成控制图',sprefs),('抽取并按键连接',fref+['pandas.core.frame.DataFrame.merge'])],['位置连接0/0错误；修复W4/W5→5/6。']),
       rec('reject_duplicate_trace_identity','注入同wafer重复特征行，确认one_to_one连接拒绝，修正输入主键后重连；验收六行、不丢片、不重复OOC，关联结果不冒称因果根因。','固定六wafer量测/特征。',[('核查并连接',['pandas.core.reshape.concat.concat','pandas.core.frame.DataFrame.merge','pandas.core.frame.DataFrame.duplicated'])],['重复键MergeError；修复六行且OOC=2。'])],bridges=[{'from':'SPC wafer metric + tsfresh feature index','to':'pandas one_to_one merge','contract':'wafer id是唯一键，所有revision/step/单位一致；重复键拒绝，严禁按行位置归因。'}],spc=True)

if __name__=='__main__':main()
