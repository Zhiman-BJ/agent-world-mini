"""Reviewed scene-level reliability selections, with explicit method boundaries."""
from pathlib import Path
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_metrology_quality')

def cls(package,module,name,methods,cap,why,construction=None):
    return symbol(package,module,name,methods,cap,why,construction)

def rcls(module,name,methods,cap,why):return cls('reliability','reliability.'+module,name,methods,cap,why)
def lcls(module,name,methods,cap,why,construction=None):return cls('lifelines','lifelines.'+module,name,methods,cap,why,construction)
def scls(module,name,methods,cap,why,construction=None):return cls('surpyval','surpyval.'+module,name,methods,cap,why,construction)
def funcs(package,module,names,cap,why):return [symbol(package,package+'.'+module,n,None,cap,why) for n in names.split()]

DIST_METHODS='__init__ PDF CDF SF HF CHF quantile inverse_SF mean_residual_life stats random_samples'
def distributions(names):return [rcls('Distributions',n+'_Distribution',DIST_METHODS,'observe','寿命分布对象支持密度/可靠度/风险/分位寿命及固定种子仿真；同一指标以当前拟合模型为准。') for n in names.split()]
def fitters(names,cap='fit'):return [rcls('Fitters',n,'__init__',cap,'高层拟合器自动保存参数、拟合准则和分布结果；不暴露底层log-likelihood实现。') for n in names.split()]
def conversions():return [rcls('Convert_data',n,'__init__','data','在失效/右删失与分组计数之间转换，保留总样本数和观测标志。') for n in 'FR_to_XCN XCN_to_FR FR_to_FNRN FNRN_to_FR XCN_to_FNRN FNRN_to_XCN'.split()]

def main():
    collection=Collection(base=BASE,raw=RAW)
    R='https://reliability.readthedocs.io/en/latest/'
    L='https://lifelines.readthedocs.io/en/latest/'
    S='https://surpyval.readthedocs.io/en/latest/'
    evidence={
        R+'Fitting%20a%20specific%20distribution%20to%20data.html':'示例提供30个固定失效时间和Weibull MLE参数51.858/2.80086，说明failures/right_censored分离、拟合分布对象及拟合准则。',
        R+'Working%20with%20fitted%20distributions.html':'解释拟合后的分布对象、时间/可靠度置信界与quantile/SF用途，强调CI_x/CI_y对应不同问题。',
        R+'What%20is%20censored%20data.html':'区分实际失效与试验终止仍存活的右删失记录，删失时间不是失效时间。',
        L+'Survival%20analysis%20with%20lifelines.html':'Kaplan-Meier完整例子使用duration和event_observed，给出风险集乘积公式、置信区间及分组比较。',
        L+'Quickstart.html':'公开fit/predict接口、事件标志及DataFrame协变量输入，展示生存回归拟合和输出。',
        L+'Survival%20Regression.html':'定义Cox和AFT的不同假设、duration/event列与协变量编码，指出普通回归忽略删失的问题及预测解释。',
        R+'What%20is%20Accelerated%20Life%20Testing.html':'以电子制造商寿命测试为例，要求多应力组且失效机制相同，温度/电压加速外推不能脱离适用范围。',
        R+'Fitting%20a%20single%20stress%20model%20to%20ALT%20data.html':'完整示例将failure times、stress和use_level_stress输入高层ALT拟合器，读取寿命-应力参数与使用条件预测。',
        R+'Getting%20your%20ALT%20data%20in%20the%20right%20format.html':'要求failure/failure_stress逐项配对，右删失应力不能出现未观测失效的新应力组。',
        R+'ROCOF.html':'使用Laplace趋势检验判别可修复系统事件强度，区分累计时间和interarrival，MTBF=1/ROCOF。',
        R+'Mean%20cumulative%20function.html':'示例按每个系统记录维修时刻与最后退役时刻，非参数MCF依据风险集中系统数累计维修计数。',
        S+'Recurrent%20Event%20Modelling%20with%20SurPyval.html':'重复事件使用累计时间x、item i、删失c；最后右删失不能再跟后续事件，并提供HPP/NHPP拟合。',
        S+'Degradation%20Analysis.html':'退化路径越过阈值形成伪失效时间，路径物理合理性是外推的主要假设；未越过阈值的对象保留右删失。',
        S+'Degradation%20Modelling%20with%20SurPyval.html':'给出四单位线性轨迹和150阈值、DegradationAnalysis.fit及新单位failure_time/RUL预测；说明伪失效与实际失效不同。',
        'https://github.com/derrynknife/SurPyval/blob/v0.18.0/surpyval/degradation/path_models.py':'发布源码定义线性、指数、幂律及二次路径、数据适用域检查和阈值反函数，线性阈值时刻为(threshold-a)/b。',
    }
    def sources(urls):
        return [collection.source(u,evidence[u],['observation','configuration','fitted_model','prediction'],
            ['fit','query','diagnose','restore'],['固定观测拟合与错误配置修复，按独立目标验收']) for u in urls]
    def task(sid,tid,desc,initial,steps,assertions):
        return recipe(tid,desc,initial,steps,assertions,f'verify_reliability.py:{sid}; runtime/reliability/{sid}.json')
    def ref(t,method):return t['module']+'.'+t['name']+'.'+method
    common_boundaries=[
        '仅参考种子与指定固定样例；尚未实现Agent实体注册表、JSON facade、reset隔离或全部API验收。',
        '试验观测为明确标记的制造/文档样例，不声称真实半导体HTOL/HAST结果或实测可靠性预测。',
        '时间单位、观测截止与事件编码属于实体契约；改变上游字段后所有拟合/预测失效并需重算。',
        '静态源方法保留空说明；属性、继承和运行时结果字段不伪造为新顶层函数。',
    ]
    common_entities=[
        {'name':'observation_table','identity':'dataset_id','attributes':['unit_id','lot_id','time_hours','event_or_censor','source','revision'],
         'lifecycle':'载入固定观测→校验单位/事件标志→纠错保留原始副本→重算；删除/修改观测使下游模型失效。'},
        {'name':'model_config','identity':'config_id','attributes':['model_family','fixed_parameters','covariate_encoding','fit_options','valid_range'],
         'lifecycle':'选择适用模型和参数→拟合→检查诊断；配置变更不能复用旧参数。'},
        {'name':'fitted_model','identity':'model_id','attributes':['source_revision','parameters','fit_status','likelihood','prediction_units','diagnostics'],
         'lifecycle':'生成→独立数值验收→预测/导出；绑定观测和配置版本，保留初始状态以重置。'},
    ]
    # 08.01.01: retain three common families, grouped inputs, diagnostics and plots.
    symbols=distributions('Weibull Lognormal Exponential')+fitters('Fit_Weibull_2P Fit_Weibull_2P_grouped Fit_Lognormal_2P Fit_Exponential_1P')+conversions()
    symbols+=funcs('reliability','Probability_plotting','Weibull_probability_plot Lognormal_probability_plot Exponential_probability_plot PP_plot_parametric QQ_plot_parametric plot_points plotting_positions','diagnose','比较三种常用寿命族的拟合残差和分位点，保留尾部失配诊断。')
    symbols+=[rcls('Nonparametric',n,'__init__','diagnose','非参数生存曲线用于分布假设的观测基准。') for n in ['KaplanMeier','NelsonAalen']]
    designs=[dict(scenario_id='08.01.01',description='面向半导体批次失效时间的寿命分布环境：以单位ID、小时、事件/删失状态维护输入，拟合Weibull/对数正态/指数族并查询可靠度、风险与B10寿命。固定样例用独立MLE方程检验结果，保留单位修复与诊断链。',
        packages=[('reliability','primary','高层寿命拟合、分布和诊断形成统一主链。'),('surpyval','excluded','一般寿命拟合与主包重复；完整候选池保留，其退化专长单列08.01.06。')],symbols=symbols,
        sources=sources([R+'Fitting%20a%20specific%20distribution%20to%20data.html',R+'Working%20with%20fitted%20distributions.html',R+'What%20is%20censored%20data.html']),
        tasks=[task('08.01.01','fit_lifetime','为30条固定失效小时拟合两参数Weibull，输出SF和B10；用独立MLE得分方程核验alpha=51.85800154、beta=2.80085998及B10约23.22091534小时。','固定失效列表；允许重置到原始小时数据。',[
            ('拟合并读取分布',['reliability.Fitters.Fit_Weibull_2P.__init__']),('输出可靠度与分位寿命',['reliability.Distributions.Weibull_Distribution.SF','reliability.Distributions.Weibull_Distribution.quantile'])],['alpha/beta与独立方程rtol2e-4','SF/B10与解析公式rtol2e-4']),
            task('08.01.01','repair_time_units','识别失效时间放大1000倍造成的尺度错误，恢复小时并重拟合；在同一oracle下通过alpha、beta与寿命目标。','原始单位证据与固定小时目标；错误导入把时间乘1000。',[
            ('比较错误尺度并按来源纠正',['reliability.Fitters.Fit_Weibull_2P.__init__']),('重拟合并验收可靠度',['reliability.Distributions.Weibull_Distribution.SF'])],['错误尺度必须失败','恢复后同一参数oracle通过'])])]
    # 08.01.02: lifelines owns censoring; base methods kept once at defining class.
    symbols=[lcls('fitters','BaseFitter','__init__ label','construct','KM/NA从BaseFitter继承构造及标签。'),
        lcls('fitters.kaplan_meier_fitter','KaplanMeierFitter','fit fit_interval_censoring fit_left_censoring median_survival_time_ survival_function_at_times cumulative_density_at_times plot_survival_function','fit','非参数删失拟合及阶梯生存曲线。','由公开lifelines.KaplanMeierFitter别名构造，__init__定义在BaseFitter。'),
        lcls('fitters','ParametricUnivariateFitter','__init__ fit fit_left_censoring fit_interval_censoring params_ summary AIC_ BIC_ event_table survival_function_at_times cumulative_density_at_times cumulative_hazard_at_times hazard_at_times confidence_interval_ confidence_interval_survival_function_ print_summary','fit','参数模型的共同拟合/诊断和事件表，避免复制到每个子类。','通过ExponentialFitter/WeibullFitter实例继承，基类不作为另一种实体。'),
        lcls('fitters.exponential_fitter','ExponentialFitter','percentile','observe','指数模型提供曝光量/事件数可独立核验的均值。','构造与fit由ParametricUnivariateFitter继承。'),
        lcls('fitters.weibull_fitter','WeibullFitter','percentile','observe','补充变化风险的Weibull模型，保留分位寿命。','构造与fit由ParametricUnivariateFitter继承。'),
        lcls('fitters','UnivariateFitter','predict subtract divide conditional_time_to_event_ percentile plot_survival_function plot_cumulative_hazard','observe','对已拟合生存曲线查询、比较和条件剩余寿命。','只保留子类继承的方法，不直接创建抽象基类。')]
    symbols+=funcs('lifelines','utils','survival_table_from_events group_survival_table_from_events survival_events_from_table datetimes_to_durations qth_survival_time qth_survival_times median_survival_times restricted_mean_survival_time','data','显式转换事件/删失与风险表及时间单位，查询分位和受限平均生存时间。')
    symbols+=funcs('lifelines','statistics','logrank_test multivariate_logrank_test pairwise_logrank_test survival_difference_at_fixed_point_in_time_test','diagnose','比较批次/组生存曲线，保留事件标志而非简单失效均值。')
    symbols+=[lcls('statistics','StatisticalResult','__init__ summary to_ascii to_html','diagnose','命名统计检验结果和报告输出，保留统计量及p值。')]
    designs.append(dict(scenario_id='08.01.02',description='以单位观测截止和事件状态为核心的删失寿命环境：构造风险表、Kaplan-Meier曲线及指数/Weibull拟合，支持右/左/区间删失入口与组比较。固定样例验证右删失风险集和曝光量，其他删失入口仅为来源参考。',
        packages=[('lifelines','primary','事件表、KM和参数删失拟合在同一实体链中完成。'),('reliability','excluded','右删失拟合与主实现重叠。'),('surpyval','excluded','一般删失寿命功能与主实现重叠，避免同时暴露不同事件编码。')],symbols=symbols,
        sources=sources([L+'Survival%20analysis%20with%20lifelines.html',R+'What%20is%20censored%20data.html',R+'Fitting%20a%20specific%20distribution%20to%20data.html']),
        tasks=[task('08.01.02','censor_km','按六条固定观测的事件/删失标志拟合Kaplan-Meier，在2/4/6小时验收可靠度5/6、5/8、5/16；拒绝把所有删失标为失效的曲线。','时间[2,3,4,5,6,8]，event=[1,0,1,0,1,0]。',[
            ('构造并拟合KM',['lifelines.fitters.BaseFitter.__init__','lifelines.fitters.kaplan_meier_fitter.KaplanMeierFitter.fit']),('查询独立风险集时刻',['lifelines.fitters.kaplan_meier_fitter.KaplanMeierFitter.survival_function_at_times'])],['三时刻atol1e-12','错误事件曲线必须被拒绝']),
            task('08.01.02','repair_censor_exposure','将误标为失效的观测截止恢复为右删失，重拟合指数寿命；均值须等于总曝光28小时/3次失效，不能接受28/6。','错误标记使所有六条均为事件；原始观测账本提供三个删失。',[
            ('恢复标记并拟合指数模型',['lifelines.fitters.ParametricUnivariateFitter.__init__','lifelines.fitters.ParametricUnivariateFitter.fit']),('读取拟合参数',['lifelines.fitters.ParametricUnivariateFitter.params_'])],['均值9.333333小时rtol1e-4','错误均值4.666667必须失败'])]))
    # 08.01.03: AFT main model with Cox as an explicitly distinct hypothesis.
    symbols=[lcls('fitters.weibull_aft_fitter','WeibullAFTFitter','__init__ predict_percentile predict_expectation','fit','主模型AFT，量化协变量对应的寿命倍率。'),
        lcls('fitters.log_normal_aft_fitter','LogNormalAFTFitter','__init__ predict_percentile predict_expectation','fit','可比较重尾AFT误差形状；不得把分布选择当作因果验证。'),
        lcls('fitters','ParametericAFTRegressionFitter','fit fit_interval_censoring fit_left_censoring predict_survival_function predict_median predict_hazard predict_cumulative_hazard plot_partial_effects_on_outcome','fit','AFT子类继承拟合与预测，API保留源码Parameteric拼写。','WeibullAFTFitter/LogNormalAFTFitter创建对象，不直接构造此公共基类。'),
        lcls('fitters','ParametricRegressionFitter','score log_likelihood_ratio_test summary print_summary median_survival_time_ mean_survival_time_ concordance_index_ AIC_ BIC_','diagnose','从已拟合AFT读取模型准则和预测质量；不重复其已覆写的fit/predict。','由AFT子类继承，公共基类不单独构造。'),
        lcls('fitters.coxph_fitter','CoxPHFitter','__init__ fit print_summary compute_followup_hazard_ratios plot_partial_effects_on_outcome','fit','Cox提供不同的比例风险假设供数据适用性比较；当前实跑仅AFT。'),
        lcls('fitters','RegressionFitter','compute_residuals','diagnose','拟合器支持的残差类型用于适用性诊断，需检查具体子类实现。','由具体回归拟合器获得，不单独构造。'),
        lcls('fitters.mixins','ProportionalHazardMixin','check_assumptions','diagnose','Cox比例风险假设诊断，不将AFT倍率当作Cox风险比。','通过CoxPHFitter混入方法获得。')]
    symbols+=funcs('lifelines','utils','datetimes_to_durations survival_table_from_events k_fold_cross_validation restricted_mean_survival_time median_survival_times normalize unnormalize','data','准备观测时间与编码尺度，保留训练归一化参数和可重复交叉验证。')
    symbols+=funcs('lifelines','utils.concordance','concordance_index','diagnose','比较风险排序与带删失观测，仅作为统计质量指标。')
    symbols+=funcs('lifelines','statistics','logrank_test multivariate_logrank_test pairwise_logrank_test proportional_hazard_test','diagnose','分组差异与比例风险检验。')
    symbols+=[lcls('statistics','StatisticalResult','__init__ summary to_ascii to_html','diagnose','保留检验结果和可审计报告。')]
    designs.append(dict(scenario_id='08.01.03',description='将温度/电压/批次编码和删失寿命表连接到AFT/Cox生存回归，主任务用AFT的寿命倍率而非普通最小二乘处理寿命。保持训练/查询编码一致，保存假设与预测范围；样例只证实确定性两组AFT倍率，不声称因果或真实设备寿命。',
        packages=[('lifelines','primary','统一回归、删失语义、预测和假设诊断。')],symbols=symbols,
        sources=sources([L+'Survival%20Regression.html',L+'Quickstart.html',L+'Survival%20analysis%20with%20lifelines.html']),
        tasks=[task('08.01.03','fit_aft_covariates','拟合600条两组固定Weibull分位样本的AFT模型，输出低应力组与基线中位寿命；验收系数ln2和寿命倍率2。','两组匹配分位时间，第二组时间精确乘2，low_stress编码0/1。',[
            ('构造主模型并拟合协变量',['lifelines.fitters.weibull_aft_fitter.WeibullAFTFitter.__init__','lifelines.fitters.ParametericAFTRegressionFitter.fit']),('比较条件生存和中位寿命',['lifelines.fitters.ParametericAFTRegressionFitter.predict_median','lifelines.fitters.ParametericAFTRegressionFitter.predict_survival_function'])],['系数ln2 atol2e-4','中位寿命比2 rtol2e-4']),
            task('08.01.03','repair_covariate_encoding','发现查询把两组物理标签编码反转后修正映射，保持模型不变重新预测；拒绝0.5倍率并恢复2倍寿命。','训练字典low_stress:0/1与错误查询顺序[1,0]。',[
            ('对照训练字典纠正查询并预测',['lifelines.fitters.ParametericAFTRegressionFitter.predict_median']),('核对生存顺序',['lifelines.fitters.ParametericAFTRegressionFitter.predict_survival_function'])],['错误比约0.5','恢复比2且低应力组存活率更高'])]))
    symbols=distributions('Weibull Lognormal Exponential')+conversions()
    symbols+=[rcls('ALT_fitters',n,'__init__','fit','常用单温度/应力高层ALT模型，区分寿命分布和应力加速关系。') for n in 'Fit_Weibull_Exponential Fit_Lognormal_Exponential Fit_Exponential_Exponential Fit_Weibull_Eyring Fit_Lognormal_Eyring Fit_Weibull_Power'.split()]
    symbols+=fitters('Fit_Weibull_2P Fit_Lognormal_2P Fit_Exponential_1P','diagnose')
    symbols+=[rcls('PoF','acceleration_factor','__init__','diagnose','温度加速因子用于独立物理参数解释，温度单位必须显式转换。')]
    symbols+=funcs('reliability','Probability_plotting','Weibull_probability_plot Lognormal_probability_plot Exponential_probability_plot','diagnose','比较应力组原始寿命形状，防止忽略机制变化。')
    designs.append(dict(scenario_id='08.01.04',description='面向HTOL温度或单应力加速试验的寿命-应力模型环境：绑定失效/删失记录与对应应力，拟合Arrhenius/Eyring/幂律候选并在明确使用条件下预测。固定样例检查Kelvin输入和指数-Arrhenius解析MLE；不把数学外推当作已验证失效机制。',
        packages=[('reliability','primary','领域ALT拟合器维护应力组、拟合状态和使用条件分布。')],symbols=symbols,
        sources=sources([R+'What%20is%20Accelerated%20Life%20Testing.html',R+'Fitting%20a%20single%20stress%20model%20to%20ALT%20data.html',R+'Getting%20your%20ALT%20data%20in%20the%20right%20format.html']),
        tasks=[task('08.01.04','fit_arrhenius_alt','对400/450/500K三个制造样本组拟合指数-Arrhenius模型，输出350K均值寿命；验收a=3000、b=1及均值exp(3000/350)。','每组60个归一化指数分位，组均值精确为exp(3000/T)，时间单位小时。',[
            ('绑定failure/stress并拟合',['reliability.ALT_fitters.Fit_Exponential_Exponential.__init__']),('查询使用温度分布',['reliability.Distributions.Exponential_Distribution.SF'])],['fit.success为真且独立目标rtol3e-3','350K均值约5278.6小时']),
            task('08.01.04','repair_kelvin_stress','诊断把摄氏温度直接送入Arrhenius指数造成的寿命外推错误，恢复Kelvin并重拟合；在相同350K解析目标下验收。','错误应力126.85/176.85/226.85°C与76.85°C使用温度。',[
            ('按单位来源恢复Kelvin并重算',['reliability.ALT_fitters.Fit_Exponential_Exponential.__init__'])],['摄氏输入输出必须失败','修复均值rtol3e-3'])]))
    # 08.01.05: retain only one nonparametric MCF and complement by parameterized HPP/NHPP.
    symbols=[rcls('Repairable_systems',n,'__init__','nonparametric','维修累计账本、趋势和MCF入口；最后时刻为退役截止。') for n in ['MCF_nonparametric','MCF_parametric','ROCOF','reliability_growth','optimal_replacement_time']]
    symbols += [scls('recurrent.parametric.hpp','HPP','__init__ fit fit_from_recurrent_data','fit','补充具有删失曝光和序列化结果的恒定事件强度模型。'),
        scls('recurrent.parametric.nhpp_fitter','NHPPFitter','fit fit_from_recurrent_data from_params','fit','为CrowAMSAA等模型提供共同的公开拟合入口。','由surpyval.recurrent中发布的拟合器实例导出；不新增重复构造实体。'),
        scls('recurrent.parametric.crow_amsaa','CrowAMSAA','__init__ cif iif inv_cif','fit','在趋势证据支持时采用幂律强度，而不把恒定率自动推广。'),
        scls('recurrent.parametric.parametric_recurrence','ParametricRecurrenceModel','to_dict from_dict cif mcf iif inv_cif residuals trend_test cramer_von_mises cif_cb plot','observe','拟合工厂返回参数事件模型，读取强度/MCF、诊断和保存。','由HPP.fit或NHPPFitter.fit返回，避免手工拼不完整模型。'),
        scls('recurrent.inference','LikelihoodInferenceMixin','parameter_names log_likelihood aic bic covariance standard_errors param_cb','diagnose','提供返回模型继承的拟合准则和参数不确定性。','由ParametricRecurrenceModel继承，不直接构造。'),
        scls('utils.recurrent_event_data','RecurrentEventData','__init__ to_xrd event_types get_interarrival_times get_previous_x get_events_for_item get_times_to_first_events','data','维修记录身份、累计/间隔时间与事件状态转换。'),
        scls('serialisation','SerialisableMixin','to_json from_json','persist','标准JSON模型持久化。','由参数事件模型继承，不直接构造。')]
    designs.append(dict(scenario_id='08.01.05',description='为可修复设备维护带系统ID的维修事件和退役截止，计算非参数MCF与恒定/变化事件强度；真实桥接同一账本到reliability嵌套列表和SurPyval的x/i/c表示。按总曝光与事件计数独立验收，不把退役时刻当作维修。',
        packages=[('reliability','primary','负责非参数MCF、维修趋势和更换策略入口。'),('surpyval','complement','补充曝光建模的HPP/NHPP参数模型及持久化，排除重复非参数MCF。')],symbols=symbols,
        count_exception='此场景裁去重复非参数MCF、模型内部似然和随机过程长尾后主链约40余项，计数低于50有意保留，不填入无关寿命分布。',
        sources=sources([R+'Mean%20cumulative%20function.html',R+'ROCOF.html',S+'Recurrent%20Event%20Modelling%20with%20SurPyval.html']),
        tasks=[task('08.01.05','repair_counts_and_exposure','将两系统维修账本分别输入非参数MCF与HPP，验证四次维修、20系统小时曝光、最终MCF=2以及HPP率0.2/小时。','账本[[2,6,10],[3,8,10]]，两条10小时均为退役截止。',[
            ('读取账本估计MCF',['reliability.Repairable_systems.MCF_nonparametric.__init__']),('转换x/i/c并拟合HPP',['surpyval.recurrent.parametric.hpp.HPP.fit']),('比较累计维修数',['surpyval.recurrent.parametric.parametric_recurrence.ParametricRecurrenceModel.mcf'])],['MCF阶梯.5/1/1.5/2','rate4/20且10h MCF=2']),
            task('08.01.05','repair_retirement_endpoints','发现遗漏退役截止使最后维修被误作删失，补回每系统10小时截止并重算；拒绝最终MCF=1并恢复2。','错误输入[[2,6],[3,8]]；原始系统账本证明观察均持续到10小时。',[
            ('修复终止记录并重新估计',['reliability.Repairable_systems.MCF_nonparametric.__init__'])],['缺失截止时MCF=1必须失败','恢复MCF=2'])]))
    symbols=[scls('degradation.degradation_analysis','DegradationAnalysis_','fit fit_from_df','fit','从逐单位时序测量生成路径拟合和伪失效分布。','公开DegradationAnalysis是源码DegradationAnalysis_的预建实例，按其真实类方法登记。'),
        scls('degradation.degradation_analysis','DegradationModel','to_dict from_dict is_accelerated path predict_failure_time predict_remaining_life predict_rul sf ff df hf Hf qf mean random induced_life life_parameter_covariance cb plot','observe','拟合工厂返回退化模型，保留路径、阈值预测、总体生存和不确定性诊断。','由DegradationAnalysis.fit返回；不手工调用内部状态构造器。'),
        scls('degradation.path_models','PathModel','path inv_path jacobian check_data fit','configure','公开路径协议用于物理形状、数据域和阈值映射。','具体路径实例/注册表获得，抽象协议不单独构造。'),
        scls('degradation.path_models','LinearPath_','path inv_path jacobian fit','configure','电阻等近线性漂移的显式路径与阈值解析。','公开LinearPath预建实例，无需额外构造。'),
        scls('degradation.path_models','QuadraticPath_','path inv_path jacobian fit','configure','曲率明显时作为受控候选，须检查外推适用范围。','公开QuadraticPath预建实例。'),
        scls('degradation.path_models','ExponentialPath_','path inv_path jacobian check_data','configure','乘性退化候选，要求正数据并避免无物理依据外推。','公开ExponentialPath预建实例。'),
        scls('degradation.degradation_analysis','InducedFailureDistribution','to_dict from_dict ff sf qf mean median random','diagnose','从路径参数诱导总体分布用于与伪失效分布比较，保留never-fails质量。','由DegradationModel.induced_life工厂获得；不伪装为额外失效观测。'),
        scls('serialisation','SerialisableMixin','to_json from_json','persist','模型JSON保存恢复，保留阈值与路径数据。','由退化模型继承，不单独构造。')]
    symbols+=funcs('surpyval','degradation.path_models','get_path_model','configure','通过已注册公开名称选择路径，错误名称应失败。')
    designs.append(dict(scenario_id='08.01.06',description='面向失效前参数漂移的退化环境：按单位ID维护测量时间/数值/阈值，拟合路径并形成明确标记的伪失效时间、剩余寿命与总体寿命分布。支持阈值修复和模型保存恢复；固定无噪声轨迹只验证路径及阈值计算，不证明真实RUL置信界校准。',
        packages=[('surpyval','primary','具备专用退化路径、伪失效、RUL与模型序列化，避免通用寿命包替代退化实体。')],symbols=symbols,
        sources=sources([S+'Degradation%20Analysis.html',S+'Degradation%20Modelling%20with%20SurPyval.html','https://github.com/derrynknife/SurPyval/blob/v0.18.0/surpyval/degradation/path_models.py']),
        tasks=[task('08.01.06','fit_degradation_paths','拟合四单位线性漂移10+b*t，在阈值150下计算伪失效140/b，并预测新单位10+0.35*t于400小时越阈、300小时观测后剩100小时。','b=[.31,.28,.44,.37]；每单位100/200/300/400小时制造测量。',[
            ('拟合路径和伪失效',['surpyval.degradation.degradation_analysis.DegradationAnalysis_.fit']),('预测新单位阈值和剩余寿命',['surpyval.degradation.degradation_analysis.DegradationModel.predict_failure_time','surpyval.degradation.degradation_analysis.DegradationModel.predict_remaining_life'])],['四个140/b rtol1e-10','新单位400h/100h atol1e-8']),
            task('08.01.06','repair_degradation_threshold','将错录为15的失效阈值恢复150，重新拟合并将模型写为JSON再加载；核对伪失效与生存曲线，不把越阈外推误写为实际失效。','原测量与阈值单位一致，错误阈值低一数量级。',[
            ('修复阈值并重拟合',['surpyval.degradation.degradation_analysis.DegradationAnalysis_.fit']),('保存/恢复后比较曲线',['surpyval.degradation.degradation_analysis.DegradationModel.to_dict','surpyval.degradation.degradation_analysis.DegradationModel.from_dict','surpyval.degradation.degradation_analysis.DegradationModel.sf'])],['错误越阈时间必须失败','修复同一140/b目标','roundtrip曲线atol1e-12'])]))
    for design in designs:
        if design['scenario_id']=='08.01.03':
            design['count_exception']='AFT/Cox已覆盖构造、删失拟合、预测、诊断和编码，公共继承方法只计一次；46项已闭合，不重复展开基类方法凑50。'
        if design['scenario_id']=='08.01.06':
            design['count_exception']='退化路径拟合、RUL、总体分布、诊断和持久化共49项；没有必要为达到50添加当前样例不需要的新随机退化过程。'
        design['capabilities']=list(dict.fromkeys(s['capability'] for s in design['symbols']))
        design['entities']=common_entities
        if design['scenario_id']=='08.01.04':
            design['entities']=common_entities+[{'name':'stress_program','identity':'stress_id','attributes':['temperature_K','time_unit','failure_stress_pairing','use_stress_K','mechanism_assumption'],'lifecycle':'载入应力→核对Kelvin→绑定每条观测→拟合；应力变化使外推失效。'}]
        elif design['scenario_id']=='08.01.05':
            design['entities']=common_entities+[{'name':'repair_ledger','identity':'system_id','attributes':['ordered_event_hours','retirement_hours','event_type','exposure'],'lifecycle':'记录维修与截止→排序和风险集检查→估计MCF/率→纠错重算。'}]
        elif design['scenario_id']=='08.01.06':
            design['entities']=common_entities+[{'name':'degradation_track','identity':'unit_id','attributes':['measurement_times_hours','values','unit','threshold','path_family','pseudo_failure_flag'],'lifecycle':'记录多次测量→拟合路径→阈值预测→序列化；测量/阈值改变使模型和RUL失效。'}]
        design['bridges']=[{'from':'versioned unit observation table','to':'selected fitter -> bound model -> prediction','contract':'保留单位ID/事件编码/时间小时和输入版本；结果对象属性不独立伪造为新API。保存原始数据及配置可重拟合恢复。'}]
        if design['scenario_id']=='08.01.05':design['bridges'].append({'from':'reliability per-system repair list with final retirement','to':'SurPyval x/i/c','contract':'逐系统最后一项c=1右删失，其余c=0维修；i保持系统身份，x为累计小时而非间隔。真实固定样例4事件/20系统小时已验证。'})
        design['runtime_infrastructure']=[{'reference':'python.numpy_pandas_json','kind':'infrastructure','reason':'确定性数组/表格、单位变换、JSON存储及独立算术oracle；没有隐藏第二套寿命求解器。'}]
        design['boundaries']=common_boundaries
        design['runtime_report']=(BASE/f'runtime/reliability/{design["scenario_id"]}.json').as_posix()
        design['runtime_scope']='固定正常/错误/修复以及独立解析/数值oracle；只覆盖任务配方，未声称全部参考方法实跑。'
        collection.write(design)

if __name__=='__main__':main()
