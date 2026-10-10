"""Specific contributed VNA driver with one state/control stack."""
import copy
from seed_gen.scripts.scenario_collection_support import Collection,symbol,recipe
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW
from seed_gen.scripts.prepare_design_lab_transport_profiles import visa_symbols,PB,IB,IN,VP


def main():
    c=Collection(base=BASE,raw=RAW)
    s=c.source
    virtual=__import__('json').loads((BASE/'research/04.04.02.json').read_text(encoding='utf-8'))
    qsymbols=[copy.deepcopy(x) for x in virtual['symbols'] if x['package']=='qcodes']
    # This constructor belongs to the specific factory; keep base only for binding.
    visa=next(x for x in qsymbols if x['name']=='VisaInstrument')
    visa['methods'].remove('__init__')
    visa['construction_reason']='实际对象由已选KeySight_E5080B.__init__构造；VisaInstrument为其继承绑定。'
    instrument=next(x for x in qsymbols if x['name']=='Instrument')
    instrument['methods'].append('get_idn')
    symbols=[symbol('qcodes-contrib-drivers','qcodes_contrib_drivers.drivers.Keysight.Keysight_E5080B','KeySight_E5080B','__init__','driver','固定型号VNA驱动构造完整参数集，用真实Parameter接口配置；get_data/get_frequencies未由官方YAML支持，本场景配置/错误恢复不保留未验证采样入口。')]+qsymbols+visa_symbols()+[
        symbol('pyvisa-sim','pyvisa_sim.highlevel','SimVisaLibrary','get_debug_info','diagnose','官方YAML模拟后端身份，不重复暴露低层命令方法。','由KeySight_E5080B的pyvisa_sim_file参数加载。')]
    c.write(dict(scenario_id='04.03.01',description='围绕固定发布版Keysight E5080B VNA驱动配置测量频段、点数、功率、带宽和S参数通道，并诊断修复范围/枚举/扫频起止冲突。QCoDeS贡献驱动+统一参数+官方PyVISA-sim模型形成可回读状态；不声称已完成实体VNA采集或RF校准。',
        packages=[('qcodes-contrib-drivers','primary','选择具体VNA型号和其官方模拟fixture，避免抽象驱动列表替代可运行任务。'),('qcodes','complement','硬依赖，提供实际Parameter/validator/VisaInstrument和继承方法。'),('pyvisa','complement','真实消息/资源通道，用于诊断和适配；后续限制Agent裸命令。'),('pyvisa-sim','complement','官方YAML固定设备状态，保障本地无硬件回读。'),('pylablib','excluded','独立设备状态/SCPI驱动生态与当前VNA控制职责重叠；无需引入第二通信抽象。'),('RsInstrument','excluded','面向R&S的另一套SCPI封装，当前选择Keysight驱动和官方模拟fixture，其同步/日志优势不补当前任务缺口。')],
        sources=[s('https://github.com/QCoDeS/Qcodes_contrib_drivers/blob/v0.25.0/tests/Keysight/test_E5080B.py','官方测试用pyvisa_sim_file创建E5080B，并逐参数写入/回读频率、点数、功率、带宽、扫频、S参数；get_data测试采用monkeypatch，因此不能据它声称实际数据获取。',['vna','frequency_range','power','sweep','parameter'],['KeySight_E5080B','ParameterBase.__call__'],['固定配置表与不合法设置修复']),
                 s('https://github.com/QCoDeS/Qcodes_contrib_drivers/blob/v0.25.0/src/qcodes_contrib_drivers/sims/Keysight_E5080B.yaml','官方YAML定义模拟identity、start/stop频率、点数、source_power、IF bandwidth等有记忆属性及TCPIP资源名。',['simulation_definition','actual_parameter_state'],['VisaInstrument','query'],['确认真实驱动命令与官方后端对话一致']),
                 s('https://microsoft.github.io/Qcodes/examples/writing_drivers/Creating-Instrument-Drivers.html','官方建议VisaInstrument+Parameters+SI单位+validators，量程应软件预先检查并能够使用模拟设备。',['driver','unit','range','enum'],['VisaInstrument','Parameter','Numbers','Enum'],['范围与枚举错误早拒绝，跨参数不变量由场景验证']),
                 s('https://rsinstrument.readthedocs.io/en/latest/StepByStepGuide.html','官方介绍R&S VISA/Socket会话、同步SCPI、错误检查、模拟模式与日志，作为替代通信封装比较，当前Keysight场景排除。',['instrument_session','status_error'],['RsInstrument'],['用于替代包取舍，不声称已使用R&S硬件'])],
        entities=[dict(name='vna',identity='instrument_id',attributes=['model','backend_kind','source_release','RF_output','revision'],lifecycle='create official simulated driver -> configure -> query actual -> validate -> repair -> close；外部硬件连接未启用。'),dict(name='sweep_configuration',identity='instrument_id + revision',attributes=['start_freq Hz','stop_freq Hz','points','source_power dBm','if_bandwidth Hz','sweep_type','scattering_parameter'],lifecycle='各字段校验后仍需start<stop等整体不变量；修改配置使旧验证过期，RF保持关闭直到通过。'),dict(name='configuration_verdict',identity='verdict_id',attributes=['fixed_target','actual_readback','invalid_value_error','start_stop_invariant','passed'],lifecycle='从真实get命令生成，不能只读取客户端set缓存；修复创建新判定。')],
        capabilities=['driver','state','configure','session','io','diagnose'],symbols=symbols,
        bridges=[dict(from_entity='KeySight_E5080B',to='QCoDeS Parameter -> PyVISA -> official YAML@sim',tool='qcodes_contrib_drivers.drivers.Keysight.Keysight_E5080B.KeySight_E5080B.__init__',contract='官方tag的YAML字节与安装包一致；Hz/dBm单位、参数枚举、身份和实际回读保持。192.168.0.10仅模拟资源名，未连接真实网段。')],
        runtime_infrastructure=[dict(reference='python.fixed_configuration_oracle',kind='infrastructure',reason='固定1–3MHz/11点/-20dBm/1kHz/S21/RF off表及start<stop不变量；直接取driver.parameters中的公开Parameter并调用已选__call__。'),dict(reference='official.pyvisa_sim_fixture',kind='explicit_virtual_fixture',reason='使用官方发布版Keysight_E5080B.yaml，哈希见runtime report；参数状态模拟而非RF物理模型。')],
        boundaries=['未运行实体SMU/VNA/Scope；此L3选定VNA配置/错误恢复的可重复子场景，不代表所有仪器型号已验证。','未执行get_data/get_frequencies或校准；官方对应测试mock不能作为实测证据。','驱动导入有5秒等待和top-level VisaInstrument弃用警告，源码保持原样。','仅固定任务调用已实跑；完整Agent facade、权限、revision/reset待后续实现。'],
        tasks=[
            recipe('configure_contributed_vna_driver','构造固定E5080B官方模拟驱动，核验身份并配置1–3MHz、11点、-20dBm、1kHz IF、LIN和S21；逐项真实回读与固定表一致，保持RF关闭。','官方v0.25.0模拟YAML，初始设置未假定满足目标。',[('创建驱动和身份检查',['qcodes_contrib_drivers.drivers.Keysight.Keysight_E5080B.KeySight_E5080B.__init__',IN+'.get_idn']),('配置参数并实际回读',[PB+'.__call__',IB+'.get_component']),('确认生命周期',[VP+'.close'])],['8项配置等于固定target','identity=Keysight_E5080B_simulated','RF off','YAML安装/源码字节一致'], 'verify_driver.py:normal'),
            recipe('repair_vna_sweep_configuration','诊断起始4MHz大于终止3MHz的合法单参数但非法扫频组合，修复为1–3MHz；30dBm、500Hz和BAD扫频枚举必须被拒绝，错误尝试后完整配置仍符合固定目标。','stop=3MHz，错误start=4MHz；每个频率本身都在驱动允许范围，须检查跨参数关系。',[('读取并验证整体设置',[PB+'.__call__',PB+'.validate']),('修复起始频率，拒绝范围/枚举错误',[PB+'.__call__','qcodes.validators.validators.Numbers.validate','qcodes.validators.validators.Enum.validate']),('完整回读并关闭',[PB+'.__call__',VP+'.close'])],['错误start>=stop','修复8字段与target一致','3类非法输入均ValueError','错误输入后原状态保持'], 'verify_driver.py:wrong/fixed/error loops'),
        ],runtime_report=(BASE/'runtime/driver/04.03.01.json').as_posix(),runtime_scope='真实贡献驱动0.25.0、QCoDeS0.59、PyVISA1.16.2与官方PyVISA-sim0.7.0配置/错误恢复，未RF采样。'))


if __name__=='__main__':
    main()
