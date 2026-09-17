"""Reviewed VISA transport and virtual-instrument seeds."""
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe
from seed_gen.scripts.prepare_design_lab_sources import BASE, RAW

RM='pyvisa.highlevel.ResourceManager'
MSG='pyvisa.resources.messagebased.MessageBasedResource'
RES='pyvisa.resources.resource.Resource'
VP='qcodes.instrument.visa.VisaInstrument'
PB='qcodes.parameters.parameter_base.ParameterBase'
IB='qcodes.instrument.instrument_base.InstrumentBase'
IN='qcodes.instrument.instrument.Instrument'


def visa_symbols():
    return [
        symbol('pyvisa','pyvisa.highlevel','ResourceManager','session last_status close list_resources list_resources_info list_opened_resources resource_info open_resource','session','显式backend和资源地址，创建/检查/关闭会话；不暴露裸底层session给Agent。','ResourceManager通过源码__new__实现工厂构造，默认提取规则不计__new__；真实ResourceManager(@py/@sim)已运行，不伪造__init__。'),
        symbol('pyvisa','pyvisa.resources.resource','Resource','session last_status resource_info close get_visa_attribute set_visa_attribute','session','ResourceManager.open_resource返回对象，读取session/状态与必要VISA属性并关闭。','由已选ResourceManager.open_resource创建，不手工调用基类__init__。'),
        symbol('pyvisa','pyvisa.resources.messagebased','MessageBasedResource','encoding read_termination write_termination write_raw write write_ascii_values write_binary_values read_bytes read_raw read read_ascii_values read_binary_values query query_ascii_values query_binary_values read_termination_context flush','io','高层ASCII/二进制消息、终止符和读取；保留常用测量数据传输而排除GPIB特有控制/触发事件。','由ResourceManager根据VISA地址创建TCPIP资源，继承这些真实定义方法。'),
        symbol('pyvisa','pyvisa.highlevel','list_backends',None,'diagnose','确认后端可用性，不把安装包推断为所有硬件资源均可用。'),
        symbol('pyvisa','pyvisa.util','get_debug_info',None,'diagnose','记录VISA库和可选传输依赖，区分TCP已运行与USB/GPIB未测试。'),
    ]


def main():
    c=Collection(base=BASE,raw=RAW)
    s=c.source
    visa_sources=[
        s('https://pyvisa.readthedocs.io/en/latest/introduction/communication.html','教程串联ResourceManager、list_resources、open_resource与query(*IDN?)，并说明终止符错误会导致VisaIOError超时；SOCKET默认不在INSTR列表中。',['resource_manager','session','command','reply'],['ResourceManager','open_resource','query','read_termination'],['修复SCPI终止符、避免把枚举缺失误判为设备不存在']),
        s('https://pyvisa.readthedocs.io/en/latest/introduction/resources.html','资源由open_resource生成；session关闭后InvalidSession，timeout以毫秒计，read/write termination、chunk_size需要匹配设备。',['session','timeout_ms','termination','chunk'],['close','read','write','get_visa_attribute'],['关闭验证、超时定位及单位校验']),
        s('https://pyvisa-py.readthedocs.io/en/latest/installation.html','pyvisa-py仅标准库即可使用TCPIP INSTR/SOCKET，串口/USB/GPIB另需依赖；get_debug_info列出可用资源与缺依赖。',['backend','resource_type','optional_dependency'],['ResourceManager','PyVisaLibrary.get_debug_info'],['用本地TCP fixture完成真实后端路径验证']),
        s('https://pyvisa.readthedocs.io/en/latest/introduction/configuring.html','文档明确ResourceManager(@py)指定纯Python后端，IVI依赖外部VISA库。',['backend_selection'],['ResourceManager'],['固定后端以复现实验，避免环境默认后端漂移']),
    ]
    common_boundaries=['参考接口为后续环境基础设施，VISA地址/裸命令不应全部直接暴露给Agent；需受控设备配置/查询facade。','固定样例只运行报告中的接口，未验证全部参考操作。','当前没有完整Agent权限、状态revision、reset隔离与文件生命周期实现。']
    tcp_symbols=visa_symbols()+[symbol('pyvisa-py','pyvisa_py.highlevel','PyVisaLibrary','get_debug_info','diagnose','后端硬依赖只暴露诊断；其底层open/read/write与PyVISA公共资源职责重复。','通过ResourceManager(@py)的visalib获得；get_debug_info为静态方法。')]
    c.write(dict(scenario_id='04.04.01',description='为半导体实验自动化提供受控VISA通信基础设施：明确后端、资源地址、终止符、超时、消息/数组和会话生命周期。本轮通过真实pyvisa-py TCPIP SOCKET连接本地SCPI fixture，完成读取与超时修复，不连接硬件。',
        packages=[('pyvisa','primary','统一资源和消息接口；避免直接暴露VISA底层调用。'),('pyvisa-py','complement','实际TCPIP SOCKET后端；只选后端能力诊断，传输细节隐藏在公共资源后。')],sources=visa_sources,
        entities=[dict(name='connection_profile',identity='profile_id',attributes=['backend=@py','resource_name','timeout_ms','read_termination','write_termination','encoding'],lifecycle='create -> validate -> open -> query -> close/reopen；修改终止符/后端后重新验收，不能只看进程退出码。'),dict(name='visa_session',identity='session_id',attributes=['resource_type','open_or_closed','last_status','command_log','response_bytes'],lifecycle='由ResourceManager创建；closed后session必须失效；通信错误保留配置和日志，修复用新会话复验。')],
        capabilities=['session','io','diagnose'],symbols=tcp_symbols,
        bridges=[{'from':'PyVISA ResourceManager(@py)','to':'pyvisa-py -> localhost TCPIP SOCKET -> explicit SCPI fixture','tool':RM+'.open_resource', 'contract':'localhost动态端口、ASCII、读写换行、timeout毫秒；固定identity/电压/数组真实经TCP字节传输。关闭session后不可继续访问。'}],
        runtime_infrastructure=[dict(reference='python.local_scpi_fixture',kind='explicit_virtual_fixture',reason='socketserver/线程提供只绑定127.0.0.1的固定SCPI服务；响应/量程在verify_transport.py显式定义，不冒充实体仪器。'),dict(reference='python.numeric_json',kind='infrastructure',reason='固定数组/identity比对、JSON保存和exception检查。timeout是资源描述符，通过open_resource kwargs配置，不伪造源码方法。')],
        boundaries=common_boundaries+['未测试USB、GPIB、串口、HiSLIP/VXI-11真实仪器；诊断Available也不代表实跑。'],
        count_exception='34个操作覆盖后端诊断、会话、终止符、ASCII/二进制数据和错误恢复；该L3原文明确为不直接暴露Agent的传输层，不为凑50加入不支持的GPIB事件/寄存器操作。',
        tasks=[
            recipe('visa_socket_acquisition','使用@py后端连接给定本地SCPI服务，核验身份，设置并回读0.2/0.6/1.2V及三点ASCII数组；关闭后访问session必须报InvalidSession。','只绑定127.0.0.1的固定SCPI服务，1.5V量程，换行消息边界。',[('打开并检查资源',[RM,RM+'.open_resource',MSG+'.query']),('写入/采样',[MSG+'.write',MSG+'.query',MSG+'.query_ascii_values']),('关闭验证',[RES+'.close',RES+'.session'])],['固定identity一致','电压=[0.2,0.6,1.2]V','数组=[0.1,0.4,0.9]','关闭后InvalidSession'], 'verify_transport.py:transport'),
            recipe('repair_visa_termination','定位长连接SCPI读取缺少换行终止符导致的真实超时；恢复读写换行、重新打开资源，以同一身份和1.2V回读目标验收。','故障read_termination=None，timeout120ms；服务端持续保持TCP连接。',[('读取并捕获超时',[RM+'.open_resource',MSG+'.query']),('修复通信配置并重开',[RES+'.close',RM+'.open_resource',MSG+'.read_termination',MSG+'.write_termination']),('重新验证同一目标',[MSG+'.query',MSG+'.write','pyvisa_py.highlevel.PyVisaLibrary.get_debug_info'])],['错误VI_ERROR_TMO','修复identity一致','1.2V回读准确','未放宽目标或关闭检查'], 'verify_transport.py:wrong/fixed socket'),
        ],runtime_report=(BASE/'runtime/transport/04.04.01.json').as_posix(),runtime_scope='真实PyVISA1.16.2+pyvisa-py0.8.1 TCP socket，显式本地SCPI fixture，正常/终止符错误/修复。'))
    qsymbols=[
        symbol('qcodes','qcodes.instrument.visa','VisaInstrument','__init__ address resource_manager visa_handle visabackend set_terminator close write_raw ask_raw snapshot_base','state','真实QCoDeS-PyVISA桥接，显式YAML@sim后端和终止符；不自动连接真实设备。'),
        symbol('qcodes','qcodes.instrument.instrument','Instrument','ask write is_valid','state','按源码定义保留VisaInstrument继承的公开命令入口。','由已选VisaInstrument.__init__构造实际对象。'),
        symbol('qcodes','qcodes.instrument.instrument_base','InstrumentBase','add_parameter get_component snapshot_base invalidate_cache validate_status full_name set get','configure','注册参数、快照、缓存失效和按名称访问；真实设备状态必须get回读。','由VisaInstrument构造并继承，不单独创建基类。'),
        symbol('qcodes','qcodes.parameters.parameter','Parameter','__init__ unit label increment sweep','configure','带SCPI命令与parser/validator的参数；对客户端配置与后端设备量程分离验收。'),
        symbol('qcodes','qcodes.parameters.parameter_base','ParameterBase','__call__ vals validators validate raw_value snapshot_base full_name gettable settable set_to restore_at_exit','configure','调用实际动态get/set包装、检查/修复量程和配置；set缓存不可作为真实电压。','由InstrumentBase.add_parameter或Parameter.__init__创建。'),
        symbol('qcodes','qcodes.validators.validators','Numbers','__init__ validate min_value max_value','configure','SMU电压连续量程。'),
        symbol('qcodes','qcodes.validators.validators','Enum','__init__ validate values','configure','输出开关明确只有0/1。'),
        symbol('pyvisa-sim','pyvisa_sim.highlevel','SimVisaLibrary','get_debug_info','diagnose','模拟后端由PyVISA加载，只公开后端身份诊断；不重复暴露底层读写实现。','由VisaInstrument内部ResourceManager(YAML@sim)加载，或调用静态get_debug_info。'),
    ]
    sim_sources=[visa_sources[0],
        s('https://pyvisa-sim.readthedocs.io/en/latest/definitions.html','自定义YAML定义devices/resources、终止符、dialogues、带默认值/范围的properties，以及*ESR? command_error=32状态位。',['device_definition','property','range','error_register'],['ResourceManager','query','write'],['客户端量程与后端不一致的定位及修复']),
        s('https://pyvisa-sim.readthedocs.io/en/latest/','官方说明PyVISA-sim通过模拟backend在无硬件条件下开发测试仪器控制，支持自定义设备。',['simulation_backend','resource'],['SimVisaLibrary'],['无硬件设备策略和错误恢复']),
        s('https://microsoft.github.io/Qcodes/examples/writing_drivers/Creating-Instrument-Drivers.html','官方建议文本接口用VisaInstrument便于模拟；Parameter带SI单位、set_cmd/get_cmd、parser和validator，量程应软件预先拒绝。',['visa_instrument','parameter','unit','validator'],['VisaInstrument','add_parameter','Parameter','Numbers'],['模拟SMU参数注册、有效设置/读回、量程修复']),
    ]
    c.write(dict(scenario_id='04.04.02',description='以PyVISA-sim的YAML设备模型和QCoDeS参数驱动构成无硬件仪器环境，练习偏压序列、实际回读、输出开关、错误状态和客户端量程修复；明确只模拟命令/属性状态，不自动生成半导体物理响应。',
        packages=[('qcodes','primary','统一仪器和参数，任务使用已注册参数；复用真实VisaInstrument封装。'),('pyvisa','complement','真实ResourceManager/消息通道桥接；裸命令仅用于后端诊断和少数受控适配。'),('pyvisa-sim','complement','实现YAML设备状态和错误响应，不能由客户端缓存替代。'),('pymeasure','excluded','提供另一套仪器/Procedure框架，与主状态模型重复；无需共存暴露。')],sources=sim_sources,
        entities=[dict(name='virtual_device_definition',identity='fixture_id',attributes=['YAML checksum','model identity','commands','property defaults/ranges','error_register_bits'],lifecycle='load immutable fixture -> instantiate backend -> execute commands -> reset by fresh backend/configured safe state；不能让Agent修改oracle范围。'),dict(name='instrument_parameter',identity='instrument_id + parameter_name',attributes=['unit','set_cmd','get_cmd','validator','cached_setpoint','actual_readback'],lifecycle='configure -> validate -> set -> query actual -> compare；backend拒绝后缓存与实际可能不同，必须读回。'),dict(name='error_status',identity='instrument_id + status_query',attributes=['command_error bit32','query_error bit4','cleared_after_read'],lifecycle='非法命令/范围设置导致状态位，读取诊断并修复设置，再查询无错误。')],
        capabilities=['state','configure','session','io','diagnose'],symbols=qsymbols+visa_symbols(),
        bridges=[{'from':'QCoDeS Parameter','to':'VisaInstrument -> PyVISA -> YAML@sim -> actual readback','tool':VP+'.__init__ + '+IB+'.add_parameter + '+PB+'.__call__','contract':'SI单位V、换行、VOLT {:.6f}/VOLT?、0–1.5V后端范围；客户端误设0–2V时1.8V写入被拒绝且原1.2V保持，ESR返回32。'}],
        runtime_infrastructure=[dict(reference='python.yaml_virtual_fixture',kind='explicit_virtual_fixture',reason='fixtures/virtual_smu.yaml明确模拟设备命令、属性、量程和错误；没有硬件物理响应。YAML固定只读，客户端参数可修改。'),dict(reference='python.assert_json',kind='infrastructure',reason='固定电压/ESR目标、JSON记录和异常比较。')],
        boundaries=common_boundaries+['未连接真实SMU；没有I-V/噪声/热动力学模型。','范围修复仅修改客户端validator，不能放宽YAML后端或固定目标。'],
        tasks=[
            recipe('virtual_instrument_bias_sequence','加载固定YAML模拟SMU，经QCoDeS-PyVISA设置输出打开并依次设0.2/0.6/1.2V；每点通过真实get命令回读，核对身份、电压序列和无错误状态。','模拟设备初始0V/输出关闭，固定1.5V量程与已知SCPI身份。',[('创建并注册参数',[VP+'.__init__',IB+'.add_parameter','qcodes.parameters.parameter.Parameter.__init__']),('配置和回读',[PB+'.__call__',IN+'.ask']),('核查后端',[VP+'.resource_manager','pyvisa_sim.highlevel.SimVisaLibrary.get_debug_info'])],['identity固定一致','实际回读=[0.2,0.6,1.2]V','output=1','ESR=0'], 'verify_transport.py:simulated'),
            recipe('repair_virtual_driver_range','发现客户端允许1.8V但设备拒绝，读取ESR位32及仍为1.2V的实际电压；将客户端validator修为0–1.5V，越界提前失败，合法1V设置成功后关闭输出并归零。','客户端错误范围0–2V，YAML后端固定范围0–1.5V，之前实际值1.2V。',[('确认错误与真实值',[PB+'.__call__',IN+'.ask']),('修复范围并重试',[PB+'.vals','qcodes.validators.validators.Numbers.__init__',PB+'.validate',PB+'.__call__']),('恢复安全初值',[PB+'.__call__',VP+'.close'])],['ESR&32非零','错误写入后实际1.2V','修复后1.8V抛ValueError','合法1V且ESR=0','最后0V且output=0'], 'verify_transport.py:client/backend mismatch'),
        ],runtime_report=(BASE/'runtime/transport/04.04.02.json').as_posix(),runtime_scope='真实QCoDeS0.59→PyVISA1.16.2→PyVISA-sim0.7.0，固定YAML设备，正常/错误量程/修复。'))


if __name__=='__main__':
    main()
