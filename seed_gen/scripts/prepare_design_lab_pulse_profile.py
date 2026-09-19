"""High-level offline AWG sequence with an explicit waveform-format bridge."""
from seed_gen.scripts.scenario_collection_support import Collection,symbol,recipe
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW


def main():
    c=Collection(base=BASE,raw=RAW)
    def s(module,name,methods,cap,reason,construction=None):
        return symbol('laboneq','laboneq.'+module,name,methods,cap,reason,construction)
    symbols=[
        s('dsl.device.device_setup','DeviceSetup','add_dataserver add_instruments add_connections instrument_by_uid logical_signal_by_uid set_calibration get_calibration reset_calibration list_calibratables from_descriptor from_yaml','setup','设备/逻辑信号/物理端口和校准的主状态；支持模板加载与显式构造。','attrs生成构造DeviceSetup(uid=...)，没有源码__init__，已在固定样例执行。'),
        s('dsl.device.instruments.hdawg','HDAWG','ports','setup','明确单一HDAWG设备和端口，避免纳入当前未验证的全部设备类型。','attrs生成构造HDAWG(uid,address,device_options)，经DeviceSetup.add_instruments加入，已运行。'),
        s('dsl.device.instrument','Instrument','output_by_uid input_by_uid calc_options ports','setup','HDAWG真实继承端口/选件查询，区分型号与物理连接。','由HDAWG生成构造返回，基类仅绑定继承方法。'),
        s('dsl.device.connection','create_connection',None,'setup','生成真实逻辑信号→IQ物理端口映射。'),
        s('dsl.experiment.experiment','Experiment','add_signal add experiment_signals_uids list_experiment_signals map_signal reset_signal_map signal_mapping_status get_signal_map set_signal_map set_calibration get_calibration reset_calibration play delay reserve acquire measure reset_oscillator_phase sweep acquire_loop_rt section','sequence','高层实验、信号映射、时序段、脉冲和采集循环；无任意外部callback或底层硬件节点写。','attrs生成Experiment(uid,signals等)；固定样例构造成功，不伪造__init__。'),
        s('dsl.experiment.experiment_signal','ExperimentSignal','__init__ is_mapped map disconnect oscillator amplitude port_delay delay_signal range is_calibrated reset_calibration','sequence','信号标识、映射状态、幅度/延迟/量程；剔除当前未需泵浦和复杂预补偿。'),
        s('dsl.experiment.pulse','PulseFunctional','evaluate generate_sampled_pulse','pulse','明确函数名、幅度、长度和采样率；跳过装饰器产生的工厂别名错误AST签名。','attrs生成构造(function,uid,amplitude,length,pulse_parameters,can_compress)，const固定样例真实运行。'),
        s('dsl.experiment.pulse','PulseSampled',[],'pulse','用户固定数组输入的采样脉冲，和函数脉冲互补，不另外暴露动态factory。','attrs生成(samples,uid,can_compress)，数组单位为归一化I/Q幅度；当前任务使用PulseFunctional采样结果。'),
        s('dsl.calibration.calibration','Calibration','get items keys values','calibrate','逻辑信号到校准配置的字典容器。','attrs生成Calibration(items=...)，本次通过位置字典构造已运行。'),
        s('dsl.calibration.signal_calibration','SignalCalibration',[],'calibrate','真实源类字段容器，记录oscillator/range/delay/amplitude校准。','attrs生成SignalCalibration(oscillator,range等)构造；源码未显式方法，不填造。'),
        s('dsl.calibration.oscillator','Oscillator',[],'calibrate','频率与软/硬调制类型的状态对象，本样例固定0Hz软件调制。','attrs生成Oscillator(uid,frequency,modulation_type)，固定样例已运行。'),
        s('dsl.parameter','LinearSweepParameter','values','sequence','脉冲幅度或时长的确定性扫描计划。','dataclass/attrs生成uid,start,stop,count构造，不人工填__init__。'),
        s('dsl.session','Session','__init__ connect disconnect connection_state compile compiled_experiment experiment signal_map device_setup device_calibration','compile','会话固定emulation模式；编译由安装wheel真实Rust后端执行，不暴露run/submit实体执行入口。'),
        s('core.types.compiled_experiment','CompiledExperiment','src waves recipe estimated_runtime wave_indices command_tables schedule result_properties','compile','观察编译程序、波形、索引和时序，未把底层私有Rust函数暴露为Agent接口。','由已选Session.compile返回生成的数据对象，不直接构造。'),
        s('simulator.output_simulator','OutputSimulator','__init__ max_output_length get_snippet','observe','编译后按物理通道/时间窗取真实输出波形样本，用固定幅度/时长/积分判据。'),
        symbol('zhinst-toolkit','zhinst.toolkit.waveform','Waveforms','__init__ assign_waveform assign_native_awg_waveform get_raw_vector get_sequence_snippet validate','bridge','离线波形槽位与原生16bit交织打包/解包，补齐低层数据格式验证；不暴露另一套联网Session。'),
    ]
    source=c.source
    c.write(dict(scenario_id='04.02.01',description='以LabOne Q定义单HDAWG的逻辑信号、校准和AWG脉冲序列，在明确emulation会话中真实编译并通过OutputSimulator按样本验证幅度、时长与面积；zhinst-toolkit仅负责离线原生波形格式桥接和量化检查。未执行真实硬件/量子比特校准。',
        packages=[('laboneq','primary','统一设备/信号/校准/实验/编译对象，高层管理时序；不额外暴露重复低层设备控制。'),('zhinst-toolkit','complement','硬依赖生态中仅选Waveforms离线槽位和原生格式转换，为实际数据桥接补充，排除第二Session/节点树控制。')],
        sources=[
            source('https://docs.zhinst.com/labone_q_user_manual/core/functionality_and_concepts/00_device_setup/tutorials/00_device_setup.html','官方教程定义设备序列号/选件、logical signals与physical ports、数据服务器和create_connection；明确设备组合影响采样率。',['device_setup','instrument','logical_signal','physical_port'],['DeviceSetup','HDAWG','create_connection'],['映射正确IQ输出、核对样本时钟']),
            source('https://docs.zhinst.com/labone_q_user_manual/core/functionality_and_concepts/03_sections_pulses/tutorials/01_pulse_library.html','官方脉冲教程覆盖预定义/自定义/采样脉冲和参数扫描；设置设备与校准后比较输出波形。',['pulse','sample_array','calibration','sweep'],['PulseFunctional','PulseSampled','Experiment.play'],['幅度/长度配置与采样率错误修复']),
            source('https://docs.zhinst.com/labone_q_user_manual/core/functionality_and_concepts/10_advanced_topics/tutorials/00_output_simulator.html','OutputSimulator在执行硬件前样本级模拟各通道输出；Rabi示例以do_emulation=True连接、compile后get_snippet读取wave/time。',['compiled_experiment','output_snippet'],['Session.compile','OutputSimulator.get_snippet'],['检查脉冲时长/幅度/面积，不能将输出模拟解释为量子比特测量']),
            source('https://docs.zhinst.com/zhinst-toolkit/en/latest/examples/awg.html','AWG教程Waveforms把-1..1浮点波形和marker转换为交织uint16原生格式，槽位/长度需与sequencer声明一致。',['waveform_slot','native_vector','sample_clock'],['Waveforms.assign_waveform','get_raw_vector','assign_native_awg_waveform'],['打包往返与量化误差检查']),
        ],
        entities=[
            dict(name='device_setup',identity='setup_id',attributes=['revision','HDAWG serial/options','logical_to_physical_map','IQ channels','sampling_rate','signal_calibration'],lifecycle='construct/load -> connect logical signals -> calibrate -> compile；任何映射/校准变化使已编译程序过期。'),
            dict(name='pulse_experiment',identity='experiment_id',attributes=['signals','sections','pulse.function','pulse.amplitude','pulse.length seconds','sweep_values','revision'],lifecycle='create -> play/delay/sweep -> map -> compile -> inspect；修改幅度/长度重编译，不复用旧snippet验收。'),
            dict(name='compiled_output',identity='compile_id',attributes=['source_revision','source_program','wave_indices','time_axis_seconds','waveform_samples','amplitude','duration','area'],lifecycle='Session.compile -> OutputSimulator -> fixed verifier；仅emulation，不上传真实AWG。'),
            dict(name='native_waveform',identity='slot_id',attributes=['sample_count','I/Q arrays','uint16 interleaved vector','sampling_rate','quantization_error'],lifecycle='assign -> pack -> decode -> check；改采样率后样本数与持续时间必须一致，不能只比较数组长度。'),
        ],capabilities=['setup','sequence','pulse','calibrate','compile','observe','bridge'],symbols=symbols,
        bridges=[{'from':'PulseFunctional.generate_sampled_pulse','to':'zhinst.toolkit.Waveforms -> native AWG vector -> Waveforms', 'tool':'laboneq.dsl.experiment.pulse.PulseFunctional.generate_sampled_pulse + zhinst.toolkit.waveform.Waveforms', 'contract':'80ns at2.4GSa/s→192实/虚样本，归一化幅度0.4；uint16原生转换量化误差≤1/32767且再打包字节一致，不能把生成数据说成已上传设备。'}],
        runtime_infrastructure=[dict(reference='numpy.pulse_oracle_and_storage',kind='infrastructure',reason='非零样本计数、dt、幅度、矩形面积80ns×0.4、NPZ存储和容差；不替代真实编译器或OutputSimulator。'),dict(reference='laboneq.public_generated_constructors',kind='generated_constructor_metadata',reason='DeviceSetup/Experiment/HDAWG/Calibration/SignalCalibration/Oscillator/PulseFunctional由attrs生成构造；保持源类空方法并在construction_reason记录字段，真实构造已执行。ModulationType为枚举数据。')],
        boundaries=['Session始终do_emulation=True；不连接实体设备/数据服务，不声称真实AWG电压或量子比特校准结果。','Rust私有编译器通过公开Session.compile真实调用；源码全量池为Python参考API，不静态枚举私有Rust实现。','pulse_library动态工厂的源码AST是采样器签名，当前不选该类入口；高层pulse对象attrs构造已记录，未伪造参数docstring。','GitHub notebook页面仅iframe占位，未计已读来源；采用实际返回正文的官方在线教程，固定tag notebook本地对照。','全部精选接口未逐一运行；仅固定两任务与真实Waveforms桥接。完整Agent权限、reset和revision失效仍待后续。'],
        tasks=[
            recipe('compile_and_verify_awg_pulse','构造单HDAWG IQ信号映射和0Hz软件调制校准，编排幅度0.4、长度80ns矩形脉冲；在emulation会话真实编译，用输出模拟器取样，断言幅度、192点、80ns和32ns面积。','2.4GSa/s HDAWG8，固定目标矩形，不依赖实际量子器件。',[('创建设备、连接和校准',['laboneq.dsl.device.device_setup.DeviceSetup','laboneq.dsl.device.instruments.hdawg.HDAWG','laboneq.dsl.device.connection.create_connection','laboneq.dsl.device.device_setup.DeviceSetup.add_connections','laboneq.dsl.device.device_setup.DeviceSetup.set_calibration','laboneq.dsl.calibration.calibration.Calibration','laboneq.dsl.calibration.signal_calibration.SignalCalibration','laboneq.dsl.calibration.oscillator.Oscillator']),('编排并编译',['laboneq.dsl.experiment.pulse.PulseFunctional','laboneq.dsl.experiment.experiment.Experiment','laboneq.dsl.experiment.experiment_signal.ExperimentSignal.__init__','laboneq.dsl.experiment.experiment.Experiment.set_signal_map','laboneq.dsl.experiment.experiment.Experiment.acquire_loop_rt','laboneq.dsl.experiment.experiment.Experiment.section','laboneq.dsl.experiment.experiment.Experiment.play','laboneq.dsl.session.Session.__init__','laboneq.dsl.session.Session.connect','laboneq.dsl.session.Session.compile']),('读取输出样本验收',['laboneq.simulator.output_simulator.OutputSimulator.__init__','laboneq.simulator.output_simulator.OutputSimulator.get_snippet'])],['幅度0.4 atol1e-8','active_samples192','80ns atol1e-14s','面积32ns atol1e-14s'], 'verify_pulse.py:normal'),
            recipe('repair_pulse_amplitude_and_sample_clock','诊断0.2幅度导致面积减半与2GSa/s导致160点的配置错误，恢复0.4幅度和2.4GSa/s；重新编译、采样并经Toolkit打包/解包，固定面积32ns、192点和16bit量化误差目标通过。','错误面积16ns、错误采样160点，目标矩形80ns/0.4/2.4GSa/s固定不变。',[('读取错误编译输出',['laboneq.dsl.session.Session.compile','laboneq.simulator.output_simulator.OutputSimulator.get_snippet']),('修复脉冲并生成正确采样',['laboneq.dsl.experiment.pulse.PulseFunctional','laboneq.dsl.experiment.pulse.PulseFunctional.generate_sampled_pulse','laboneq.dsl.session.Session.compile']),('跨包格式转换及检查',['zhinst.toolkit.waveform.Waveforms.__init__','zhinst.toolkit.waveform.Waveforms.assign_waveform','zhinst.toolkit.waveform.Waveforms.get_raw_vector','zhinst.toolkit.waveform.Waveforms.assign_native_awg_waveform']),('关闭仿真会话',['laboneq.dsl.session.Session.disconnect'])],['错误面积16ns拒绝','修复面积32ns','160→192点','原生向量再打包相同','幅度误差≤1/32767'], 'verify_pulse.py:wrong/fixed and toolkit'),
        ],runtime_report=(BASE/'runtime/pulse/04.02.01.json').as_posix(),runtime_scope='LabOneQ26.7.0真实emulation编译+OutputSimulator，Toolkit1.4.0原生波形往返；未硬件运行。'))


if __name__=='__main__':
    main()
