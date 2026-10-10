"""Joint selection for three virtual semiconductor measurement scenarios."""
import copy
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe
from seed_gen.scripts.prepare_design_lab_sources import BASE, RAW

P='qcodes.parameters.parameter_base.ParameterBase'
M='qcodes.dataset.measurements.Measurement'
D='qcodes.dataset.measurements.DataSaver'
I='qcodes.instrument.instrument_base.InstrumentBase'
R='qcodes.dataset.data_set.DataSet'


def common_symbols():
    def s(module,name,methods,cap,reason,construction=None):
        return symbol('qcodes','qcodes.'+module,name,methods,cap,reason,construction)
    return [
        s('instrument.instrument','Instrument','__init__ close is_valid','state','创建具名虚拟仪器并关闭资源；不使用close_all影响其他任务。'),
        s('instrument.instrument_base','InstrumentBase','add_parameter get_component snapshot_base invalidate_cache validate_status full_name set get','state','真实定义的继承接口，统一参数注册/观察/缓存失效与按名称读写。','使用已选Instrument.__init__创建实例；不重复构造基类。'),
        s('parameters.parameter','Parameter','__init__ unit label increment sweep param_spec','configure','带单位、量程、显式get/set回调的测量参数；公开getter/setter是构造产生，调用走真实__call__。'),
        s('parameters.parameter_base','ParameterBase','__call__ vals validators validate get_ramp_values step post_delay inter_delay snapshot_base full_name raw_value gettable settable set_to restore_at_exit','configure','保留调用协议、校验、步进、时序、快照与受控恢复，避免伪造get/set源码方法。','Parameter.__init__或InstrumentBase.add_parameter产生Parameter，继承本类真实实现。'),
        s('validators.validators','Numbers','__init__ validate min_value max_value','configure','固定偏压、温度、时间/频率量程；错误输入在执行前拒绝。'),
        s('dataset.measurements','Measurement','__init__ parameters register_parameter register_custom_parameter unregister_parameter add_before_run add_after_run set_shapes run','acquire','显式依赖/setpoints注册、执行前后动作和采集上下文，不暴露后台写线程内部。'),
        s('dataset.measurements','DataSaver','add_result flush_data_to_database run_id points_written dataset','acquire','由Measurement.run上下文返回，写入当前采样并刷盘。','通过已选Measurement.run上下文生成，__init__参数由runner管理。'),
        s('dataset.data_set','DataSet','run_id path_to_db guid number_of_results paramspecs metadata completed add_metadata get_parameter_data to_pandas_dataframe_dict write_data_to_text_file get_metadata','persist','重载结果、单位/依赖和运行状态、元数据/表格导出；仅保留可审计查询，不允许随意修改已完成结果。','由Measurement产生，或由已选load_by_id恢复；不手工调用复杂底层构造。'),
        s('dataset.experiment_container','Experiment','exp_id name sample_name data_sets last_data_set finish','persist','固定样品和实验身份、查看运行并结束实验。','通过已选load_or_create_experiment返回。'),
        s('station','Station','__init__ snapshot_base add_component remove_component get_component','state','将仪器集合及配置绑定到同一场景快照；排除全局关闭和自动任意驱动加载。'),
        s('dataset.dond.sweeps','LinSweep','__init__ get_setpoints param num_points delay','acquire','常见等间隔扫描计划，与手动自适应参数设置互补。'),
        s('dataset.sqlite.database','initialise_or_create_database_at',None,'persist','为任务指定隔离SQLite数据库，不默认写全局数据库。'),
        s('dataset.experiment_container','load_or_create_experiment',None,'persist','创建/恢复明确样品的实验容器。'),
        s('dataset.data_set','load_by_id',None,'persist','从隔离数据库重新加载当前run并检查实际落盘的数据。'),
    ]


def main():
    c=Collection(base=BASE,raw=RAW)
    source=c.source
    sources=[
        source('https://microsoft.github.io/Qcodes/examples/basic_examples/15_minutes_to_QCoDeS.html','官方教程以dummy DAC/DMM展示仪器、参数、扫描和数据处理，是明确的无硬件采集案例。在线0.60.dev，源码固定0.59。',['instrument','parameter','dataset'],['Instrument','Parameter','Measurement'],['无硬件固定样例、参数操作和持久化']),
        source('https://microsoft.github.io/Qcodes/examples/Parameters/Parameters.html','Parameter负责状态值、单位、验证器、get/set命令及缓存时间；示例自定义get_raw/set_raw和dummy instruments。',['parameter','unit','validator','cache'],['Parameter','Numbers','ParameterBase.__call__'],['拒绝超量程输入，显式单位和测量参数配置']),
        source('https://microsoft.github.io/Qcodes/examples/DataSet/Performing-measurements-using-qcodes-parameters-and-dataset.html','官方Measurement例子创建数据库/实验，register_parameter定义独立参数与setpoints，run上下文逐点add_result并加载数据。',['experiment','measurement_run','setpoint','dataset'],['register_parameter','run','add_result','load_by_id'],['扫描和真实SQLite落盘回读验收']),
    ]
    b1500=source('https://microsoft.github.io/Qcodes/examples/driver_examples/Qcodes%20example%20with%20Keysight%20B1500%20Parameter%20Analyzer.html','官方B1500教程明确SMU compliance，CV setup_staircase_cv及run_sweep的电容/耗散与电压setpoints；用作真实实验场景来源，当前只运行解析虚拟DUT。',['smu','cmu','compliance','frequency','voltage_sweep'],['Parameter','Measurement'],['IV量程修复与CV频率/单位校验；不声称B1500驱动已实跑'])
    setup=[('创建参数并限制范围',['qcodes.instrument.instrument.Instrument.__init__',I+'.add_parameter','qcodes.parameters.parameter.Parameter.__init__','qcodes.validators.validators.Numbers.__init__'])]
    acquisition=[('配置、扫描并采集',[P+'.__call__',M+'.__init__',M+'.register_parameter',M+'.run',D+'.add_result',D+'.flush_data_to_database'])]
    readback=[('重新加载数据集并对固定目标验收',['qcodes.dataset.data_set.load_by_id',R+'.get_parameter_data',R+'.completed',R+'.number_of_results'])]
    tasks={
        '04.01.01':[
            recipe('measure_threshold_and_transconductance','对明确给定平方律虚拟MOS扫描0.5–1.3V的5个栅压点，落盘并重载I-V；从sqrt(I)-V拟合阈值和系数，断言Vth=0.4V、beta=1mA/V²和Vg=0.9V处gm=1mS。','解析虚拟DUT I=min(0.001*max(Vg-0.4,0)^2,compliance)，默认限流1mA；不是真实器件模型。',setup+acquisition+readback,['I=[10,90,250,490,810]uA atol1e-18A','Vth误差<1e-12V','beta误差<1e-15A/V²','5个数据点落盘且completed'], 'verify_measurement.py:dc'),
            recipe('repair_compliance_and_safe_bias','诊断100uA错误限流造成的I-V截顶与阈值偏差，恢复1mA限流重新扫描；固定5点电流目标必须全部通过，5V越界设置应被拒绝，最后归零偏压。','故障配置compliance=100uA，量程固定Vg∈[0,1.5]V，目标不随错误设置改变。',[('检查/修改限流及偏压',[P+'.__call__',P+'.validate'])]+acquisition+readback,['错误阈值偏离0.4V超过0.1V','修复与正常电流逐项一致','5V抛ValueError','最终Vg=0V'], 'verify_measurement.py:dc compliance/range'),
        ],
        '04.01.02':[
            recipe('acquire_depletion_cv','在1MHz固定频率扫描虚拟反偏结的0、0.2、0.8、1.6、3.2V，采集电容并存储；由(100pF/C)²的线性拟合恢复内建电势0.8V，核对单位与所有采样点。','明确虚拟结C=100pF/sqrt(1+Vr/0.8V)；低频附加项单独定义。',setup+acquisition+readback,['归一化1/C²=[1,1.25,2,3,5] atol2e-14','Vbi误差<1e-12V','截距=1','5点SI单位数据从SQLite读取'], 'verify_measurement.py:cv'),
            recipe('repair_cv_frequency_and_units','检测错误10kHz设置导致的10pF附加电容和错误pF解释；切换1MHz重新扫描，以原C-V目标验收并正确把100e-12F转换为100pF。','虚拟DUT在frequency<100kHz时额外加10pF；验收固定1MHz下的曲线。',[('核查并修复频率与单位',[P+'.__call__','qcodes.parameters.parameter.Parameter.unit'])]+acquisition+readback,['错误归一化曲线最大偏差>1','修复与正常曲线完全相等','零偏压100pF','频率1MHz'], 'verify_measurement.py:cv frequency/units'),
        ],
        '04.01.03':[
            recipe('measure_temperature_stress_series','对给定虚拟偏压应力定律，在300K和360K按0、1、16、81、256s测量阈值漂移，保存两组参数依赖与结果，分别核验固定阈值表。','明确教学定律Vth=0.4V+0.01V*(t/s)^0.25*T/300K，不声称校准BTI物理模型或真实时间等待。',setup+acquisition+readback,['300K=[0.4,0.41,0.42,0.43,0.44]V','360K=[0.4,0.412,0.424,0.436,0.448]V','误差<1e-14V','两组独立采集落盘'], 'verify_measurement.py:stress'),
            recipe('adapt_stress_to_fixed_drift','在360K下发现256s使阈值漂移超过30mV目标，反算并执行39.0625s的下一应力点；阈值必须为0.43V，拒绝500K超量程设置，恢复300K和零应力初态。','固定目标阈值0.43V，错误应力时长256s，温度范围250–400K。',[('读取当前阈值，调整下一时长',[P+'.__call__',P+'.validate'])]+acquisition+readback+[('恢复明确初态',[P+'.__call__','qcodes.instrument.instrument.Instrument.close'])],['错误阈值0.448V被拒绝','修复39.0625s→0.43V atol1e-14V','500K抛ValueError','reset参数后阈值0.4V'], 'verify_measurement.py:stress target/range/reset'),
        ],
    }
    details={
        '04.01.01':('虚拟MOS的直流I-V扫描、限流诊断和Vth/gm提取','virtual_smu',['vg V [0,1.5]','compliance A [1e-8,0.01]','drain_current A','beta 0.001A/V²','threshold 0.4V']),
        '04.01.02':('虚拟LCR/结电容的反偏C-V扫描、频率配置及单位/内建电势校验','virtual_lcr',['reverse_bias V [0,5]','frequency Hz [1e3,1e6]','capacitance F','C0=100pF','Vbi=0.8V']),
        '04.01.03':('虚拟DUT温度与偏压应力实验、阈值漂移观察及下一应力点选择','virtual_stress_dut',['stress_seconds [0,1e4]','temperature K [250,400]','threshold V','explicit_fixture_model','target_drift V']),
    }
    for sid,(scope,device,attrs) in details.items():
        design=dict(scenario_id=sid,description=scope+'。QCoDeS统一参数、量程、采集上下文和SQLite结果；本轮器件响应来自明确的解析虚拟fixture，验收针对测量策略/配置/持久化，不是实际硬件或器件物理求解。',
            packages=[('qcodes','primary','统一Instrument/Parameter/Measurement/DataSet状态；在完整2367操作池联合选择通用测量主链。'),('pymeasure','excluded','Instrument/Procedure采集与主框架重叠；本场景已由QCoDeS闭合，未混入另一套状态/结果对象。')],
            sources=copy.deepcopy(sources+([b1500] if sid!='04.01.03' else [])),
            entities=[
                dict(name=device,identity='instrument_id',attributes=['revision','model_kind=explicit_analytic_virtual_DUT']+attrs,lifecycle='create -> configure/validate -> acquire -> safe initial-state restore -> close；修改配置后旧测量指标须重新采集，不覆盖已完成运行。'),
                dict(name='parameter',identity='instrument_id + full_name',attributes=['unit','validator','gettable/settable','cached_value','timestamp','step','delay'],lifecycle='注册后按量程设置/读取；参数修改使依赖指标失效，不能把旧缓存充当新观测。'),
                dict(name='measurement_run',identity='isolated_database_id + run_id',attributes=['sample_name','instrument_revision','setpoint_parameters','dependent_parameters','rows','completed','settings_snapshot'],lifecycle='registered -> running -> flush -> completed -> reload/export；失败运行保留证据，修复创建新run，原oracle只读。'),
            ],capabilities=['state','configure','acquire','persist'],symbols=common_symbols(),
            bridges=[dict(from_entity='Instrument Parameter',to='Measurement -> DataSaver -> SQLite -> DataSet',tool='Measurement.register_parameter/run + DataSaver.add_result + load_by_id',contract='独立参数和依赖参数的full_name与SI单位必须对应；实测发现load_by_id后的get_parameter_data必须传full_name字符串，裸Parameter对象会错误使用短名。回读值rtol2e-14，独立物理目标保留更严的各场景绝对容差。')],
            runtime_infrastructure=[dict(reference='python.virtual_dut_fixture',kind='explicit_virtual_fixture',reason='get_cmd回调是verify_measurement.py中公开的解析虚拟响应；只模拟I-V、C-V和应力参数，不冒充真实硬件驱动或包自带半导体求解器。后续环境必须显式实现此DUT/状态适配。'),dict(reference='numpy.sqlite_json_fixture',kind='infrastructure',reason='固定表、sqrt/线性拟合、代数反算和容差断言；Path/JSON和隔离数据库目录。NumPy不提供隐藏第二套器件求解器。')],
            boundaries=['QCoDeS运行来自固定0.59.0，网页latest为0.60.0.dev166；保留真实源码签名。','未连接真实SMU/B1500/LCR、温控仪或应力设备；无真实等待/热动力学/器件噪声。','物理公式是制造的可验证fixture，不能据此声称实际测量精度、器件寿命或BTI预测有效。','全部参考接口未逐一实跑；只验收报告中6条固定任务。','当前未实现完整Agent facade、任务隔离权限和revision自动失效；实验脚本使用独立SQLite文件与关闭仪器避免串扰。'],
            tasks=tasks[sid],runtime_report=(BASE/f'runtime/measurement/{sid}.json').as_posix(),runtime_scope='真实QCoDeS参数校验/读写、Measurement/DataSaver采集和SQLite重载；虚拟解析响应+独立固定表错误/修复目标。')
        c.write(design)


if __name__=='__main__':
    main()
