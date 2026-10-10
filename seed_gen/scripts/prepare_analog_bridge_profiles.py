"""Joint code-first analog and explicitly offline commercial bridge profiles."""
from pathlib import Path
from seed_gen.scripts.scenario_collection_support import Collection,symbol,recipe
from seed_gen.scripts.build_joint_scenario_seeds import read,file_sha
BASE=Path('seed_gen/scenario_collection/analog_bridge')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/analog_bridge')

def cl(p,m,n,methods,cap,why,construction=None):return symbol(p,m,n,methods,cap,why,construction)
def fn(p,m,names,cap,why):return [symbol(p,m,n,None,cap,why) for n in names.split()]
def main():
    c=Collection(base=BASE,raw=RAW)
    missing=BASE/'research/unavailable_candidates.json'
    exclusions=read(missing)
    ev={
      'https://github.com/fredrief/pade':'官方PADE把code-first层次原理图、版图/路由、连接检查与ngspice/Spectre放入同一流程；因此本场景需要跨域一致性，不仅输出原理图文本。该仓无发布tag/release，仅作为场景需求参考。',
      'https://github.com/dan-fritchman/Hdl21/blob/v6.0.0/examples/mos_sim.py':'正式6.0.0案例构造testbench、Op/Include后使用NGSPICE运行并解析电流，检查外部可执行文件；对本场景启发是仿真必须实际求解。',
      'https://github.com/dan-fritchman/Hdl21/blob/v6.0.0/hdl21/pdk/sample_pdk/readme.md':'官方明确sample PDK为110% made-up且模型随包；制造电阻工艺/分析样例可以可验证，但不能宣称物理工艺签核。',
      'https://github.com/Vlsir/Vlsir/blob/v6.0.0/VlsirTools/readme.md':'实际仿真入口接受SimInput和SimOptions，返回protobuf或Python结果；NGSPICE后端有独立执行与解析层，网表写出不等于仿真通过。',
      'https://github.com/KLayout/klayout/blob/v0.30.12/src/db/db/gsiDeclDbNetlistDeviceExtractor.cc':'正式0.30.12内嵌文档定义矩形电阻R=L/W*sheet_rho，R/C层与tA/tB终端，提取R/A/P及几何参数；此公式可连接版图尺寸到电路电阻与独立解析验收。',
      'https://unihd-cag.github.io/skillbridge/usage/overview.html':'Python客户端负责参数/对象翻译和传输，SKILL脚本必须在Virtuoso内部运行；没有商业后端只能验证客户端协议子链。',
      'https://unihd-cag.github.io/skillbridge/examples/create_path.html':'官方例子打开layout cellview并以cv_id、layer、width和pts生成正交金属路径；相同实体引用、单位和参数名是请求合同。',
      'https://unihd-cag.github.io/skillbridge/examples/lib_save.html':'官方例子遍历白名单库，筛选schematic视图后打开、db.check再db.save，给出商业设计生命周期与顺序检查场景。',
      'https://github.com/unihd-cag/skillbridge/blob/releases/1.8.0/tests/test_test_channel.py':'正式1.8.0官方测试用DummyWorkspace预装响应并核对请求、远程引用及ParseError；这些是协议fixture，不是Cadence成功证据。',
    }
    def src(urls):return [c.source(u,ev[u],['design_spec','cellview','geometry','netlist','simulation_or_command_result'],['create','configure','extract','simulate_or_encode','check','persist'],['独立冻结规格、错误配置拒绝、修复后重新生成与验收']) for u in urls]
    symbols=[
      cl('hdl21','hdl21.module','Module','__init__ add get','schematic','统一原理图容器、具名信号及实例。'),
      cl('hdl21','hdl21.signal','Signal',[],'schematic','内部网络带name/width状态。','@datatype生成构造，不虚构源__init__。'),
      cl('hdl21','hdl21.primitives','Primitive','__call__ Params ports','schematic','内置IdealResistor/Vdc原语参数调用及端口合同；理想模型不误写为PDK器件。','库预定义Primitive实例h.Resistor和h.Vdc，参数调用返回PrimitiveCall。'),
      cl('hdl21','hdl21.primitives','PrimitiveCall','name ports','schematic','已参数化器件，通过动态实例化协议连接具名网络。','由Primitive.__call__返回；calls_instantiate生成连接__call__，不伪造成直接定义方法。'),
      cl('hdl21','hdl21.sim.data','Sim','add run Tb','simulation','将电路与分析绑定并实际运行后端，参数修改需新建run目录。','@datatype生成构造，tb/attrs/name为源字段。'),
      cl('hdl21','hdl21.sim.data','Op','tp','simulation','直流工作点分析与显式分析name。','@datatype生成构造，使用Op(name="op")防止默认Analysis0查询错。'),
      cl('vlsirtools','vlsirtools.spice.spice','SimOptions',[],'simulation','固定NGSPICE、Python结果格式与独立运行目录。','dataclass生成构造，不伪造源方法。'),
      cl('vlsirtools','vlsirtools.spice.sim_data','SimResult','index get to_proto','result','按分析名读取结果与Schema导出；退出成功不替代数值oracle。','由仿真run返回。'),
      cl('vlsirtools','vlsirtools.spice.sim_data','OpResult','to_proto','result','工作点data保存电压/电流与分析名。','SimResult.get返回；普通data字段不伪造方法。'),
      cl('klayout','klayout.db','Layout','__init__ layer create_cell top_cell read write','layout','统一DBU、层/顶单元与GDS文件往返。'),
      cl('klayout','klayout.db','Cell','shapes begin_shapes_rec bbox cell_index','layout','获取层形状和递归几何，建立实际GDS输入。','Layout.create_cell/top_cell返回，不单独构造游离Cell。'),
      cl('klayout','klayout.db','Shapes','insert clear size','layout','受控插入矩形/路径及查询。','Cell.shapes返回，不单独构造。'),
      cl('klayout','klayout.db','Box','__init__ area width height','geometry','明确整数DBU矩形电阻和接触。'),
      cl('klayout','klayout.db','Point','__init__ distance','geometry','探针与布线坐标。'),
      cl('klayout','klayout.db','Path','__init__ length polygon','geometry','金属路径生成并可查询长度/多边形。'),
      cl('klayout','klayout.db','Region','__init__ area bbox count width_check space_check','geometry','从GDS恢复区域、基本几何/规则诊断；本次任务主要器件提取，未声称完整DRC。'),
      cl('klayout','klayout.db','LayoutToNetlist','__init__ register extract_devices connect extract_netlist probe_net netlist each_error check_extraction_errors write read reset_extracted','extract','从实际几何提取器件和连接，再查询/保存结果；修复后重建避免旧缓存。'),
      cl('klayout','klayout.db','DeviceExtractorResistor','__init__','extract','按R/C几何及固定sheet_rho提取矩形电阻，避免把标签数值当提取值。'),
      cl('klayout','klayout.db','Netlist','circuit_by_name each_circuit to_s write read','extract','提取后网表与电路检索、持久化。','由LayoutToNetlist.netlist返回。'),
      cl('klayout','klayout.db','Circuit','each_device each_net net_by_name','extract','枚举真实器件/网络并核对固定拓扑。','Netlist.circuit_by_name返回。'),
      cl('klayout','klayout.db','Device','parameter net_for_terminal device_class','extract','读取电阻R/L/W和端子连接，不通过改提取结果来修复上游。','由Circuit.each_device返回。'),
      cl('klayout','klayout.db','Net','terminal_count is_floating to_s','extract','网络连接诊断；name为原生可写属性。','LayoutToNetlist.probe_net或Circuit查询返回。'),
    ]+fn('hdl21','hdl21.signal','Port','schematic','生成单一VSS端口和具名电路接口。')+fn('hdl21','hdl21.proto.exporting','to_proto','bridge','HDL21对象输出VLSIR电路Package。')+fn('hdl21','hdl21.sim.proto','to_proto','bridge','仿真Schema构造入口。')+fn('vlsirtools','vlsirtools.netlist.main','netlist','bridge','由同一VLSIR Schema导出ngspice网表。')
    design=dict(scenario_id='03.11.01',
      description='以同一Python参数规格联动原理图、版图、器件提取和电学求解：HDL21构造电路，KLayout生成GDS并从重读几何提取电阻/网络，VLSIRtools调用真实ngspice工作点。固定制造片电阻工艺验证1k/2k分压器及布局参数漂移修复；覆盖code-first模拟设计的局部完整闭环，不宣称真实OTA或代工厂签核。',
      packages=[('hdl21','primary','统一电路参数/网络/实例及仿真输入，保留一个原理图模型。'),('klayout','complement','统一GDS几何、器件和网络提取，不额外拼重复gdstk数据库。'),('vlsirtools','complement','同一VLSIR Schema的网表与真实ngspice执行/结果解析，补齐电学闭环。')],symbols=symbols,
      sources=src(['https://github.com/fredrief/pade','https://github.com/dan-fritchman/Hdl21/blob/v6.0.0/examples/mos_sim.py','https://github.com/dan-fritchman/Hdl21/blob/v6.0.0/hdl21/pdk/sample_pdk/readme.md','https://github.com/Vlsir/Vlsir/blob/v6.0.0/VlsirTools/readme.md','https://github.com/KLayout/klayout/blob/v0.30.12/src/db/db/gsiDeclDbNetlistDeviceExtractor.cc']),
      entities=[dict(name='design_spec',identity='design_id/revision',attributes=['R1_Ohm','R2_Ohm','Vin_V','sheet_rho_Ohm_per_square','L_um','W_um','expected_topology'],lifecycle='冻结需求→生成原理图/版图→比较提取结果；修复上游参数后所有派生结果重算。'),
        dict(name='layout',identity='gds_path/source_hash',attributes=['dbu_um','R_layer','C_layer','metal_paths','cell','contact_probe_positions'],lifecycle='由代码生成→写GDS→新Layout重读→器件/网络提取→保存l2n，不从源变量假装提取。'),
        dict(name='extracted_circuit',identity='extraction_id',attributes=['devices','R_L_W_A_P','terminal_nets','circuit_name','geometry_revision'],lifecycle='从物理区域得到→核对器件值/拓扑→转换HDL21测试电路→仿真；不得直接改R结果掩盖版图错误。'),
        dict(name='simulation_run',identity='run_directory',attributes=['schema','netlist','backend_version','analysis_name','raw_result','Vout_V','Isupply_A'],lifecycle='每次独立目录→真实ngspice进程→Python解析→独立2/3V和-1/3000A验收。')],
      bridges=[dict(source='HDL21 schematic/spec',target='KLayout geometry/extracted circuit',contract='固定sheet100Ohm/square，L10/20um与W1um映射1k/2k；KLayout实际器件端子网络和R值转HDL21原语，按无方向电阻端子集合比较。'),dict(source='HDL21 Sim/VLSIRtools',target='ngspice42 through WSL',contract='同一Schema生成网表，独立run目录；Windows调用WSL用户私有后端，解析真实二进制raw工作点，VSS映射地且analysis显式名op。')],
      runtime_infrastructure=[dict(reference='runtime.generated_hdl21_construction',kind='construction_protocol',reason='Signal/Sim/Op等由datatype生成构造；h.Resistor/Vdc是Primitive实例，PrimitiveCall连接调用由装饰器生成，不伪造AST方法。'),dict(reference='runtime.klayout_native_attributes',kind='infrastructure',reason='Layout.dbu与Net.name为适配器已记录原生属性/对象状态；读取不伪造为额外函数。'),dict(reference='runtime.wsl_ngspice_process',kind='infrastructure',reason='固定Ubuntu noble ngspice42+ds-3build1用户目录解包和lib路径，通过VLSIRtools已支持的NGSPICE_EXECUTABLE配置实际调用；外部二进制不是Python包。'),dict(reference='python.fixed_circuit_oracle',kind='infrastructure',reason='固定端子集合、1000/2000Ohm与解析分压/总电流独立断言；JSON/StringIO/Path只做文件/结果整理。')],
      tasks=[recipe('generate_extract_simulate_analog_divider','同一1V/1k/2k规格生成原理图及矩形电阻GDS，重读后提取R和VDD→OUT→VSS连接，分别仿真参考/提取电路，要求Vout=2/3V、源电流=-1/3000A且两者一致。','制造sheet100Ohm/square、W1um、L10/20um，oracle固定；每运行全新对象和目录。',[
        ('构造电路并导出',['hdl21.module.Module.__init__','hdl21.module.Module.add','hdl21.module.Module.get','hdl21.signal.Signal','hdl21.signal.Port','hdl21.primitives.Primitive.__call__','hdl21.proto.exporting.to_proto','vlsirtools.netlist.main.netlist']),
        ('生成GDS并重读',['klayout.db.Layout.__init__','klayout.db.Layout.layer','klayout.db.Layout.create_cell','klayout.db.Cell.shapes','klayout.db.Shapes.insert','klayout.db.Box.__init__','klayout.db.Point.__init__','klayout.db.Path.__init__','klayout.db.Layout.write','klayout.db.Layout.read','klayout.db.Layout.top_cell','klayout.db.Cell.begin_shapes_rec','klayout.db.Region.__init__']),
        ('真实器件与网络提取',['klayout.db.LayoutToNetlist.__init__','klayout.db.LayoutToNetlist.register','klayout.db.DeviceExtractorResistor.__init__','klayout.db.LayoutToNetlist.extract_devices','klayout.db.LayoutToNetlist.connect','klayout.db.LayoutToNetlist.extract_netlist','klayout.db.LayoutToNetlist.probe_net','klayout.db.LayoutToNetlist.netlist','klayout.db.Netlist.circuit_by_name','klayout.db.Circuit.each_device','klayout.db.Circuit.each_net','klayout.db.Device.parameter','klayout.db.Device.net_for_terminal']),
        ('实际DC与解析',['hdl21.sim.data.Sim','hdl21.sim.data.Op','hdl21.sim.data.Sim.run','vlsirtools.spice.spice.SimOptions','vlsirtools.spice.sim_data.SimResult.get'])],['两电阻1000/2000与三网络','L10/20,W1','Vout2/3与I=-1/3000 atol1e-12','参考/提取电路数值一致，重复两次一致'],'verify_code_analog.py'),
        recipe('repair_layout_schematic_parameter_drift','错误布局把第二电阻L20um写成15um，实际提取1.5k且ngspice输出.6V必须拒绝；恢复L20，重新生成/提取/仿真恢复2/3V和原电流，不直接修改提取R或结果。','原理图需求1k/2k与固定oracle不变。',[
          ('修复尺寸重建几何',['klayout.db.Box.__init__','klayout.db.Shapes.insert','klayout.db.Layout.write','klayout.db.Layout.read']),
          ('重新提取与核对',['klayout.db.LayoutToNetlist.extract_devices','klayout.db.LayoutToNetlist.extract_netlist','klayout.db.Device.parameter','klayout.db.Device.net_for_terminal']),
          ('重仿真并保存',['hdl21.sim.data.Sim.run','vlsirtools.spice.sim_data.SimResult.get','klayout.db.LayoutToNetlist.write','klayout.db.Netlist.to_s'])],['错误R1500、Vout.6、I=-.0004','修复R2000与原数值','两次重复一致'],'verify_code_analog.py')],
      boundaries=['PADE当前无官方release/tag，PyPI pade是无关多Agent框架，已排除且不提取main冒充稳定源码。','本场景为制造片电阻模拟子链；未做真实OTA、有源器件/寄生/噪声/温度或商业PDK签核，HDL21官方sample PDK也明确为虚构演示。','实际ngspice42固定Ubuntu分发二进制，不声称外部后端为最新；未安装到全局。HDL21/VLSIRtools用可核验稳定tag6.0.0，PyPI7与开发tag差异按原manifest记录。','只运行两条固定任务的接口子集；后续Agent状态管理、reset/step、任务隔离权限尚未实现。'],
      runtime_report=(BASE/'runtime/code_analog/03.11.01.json').as_posix(),runtime_scope='实际GDS往返、器件提取与ngspice工作点；正常/错误/修复各使用独立对象目录，每完整任务两次重复一致。')
    designs=[design]
    p='skillbridge';prefix='skillbridge.client.'
    symbols=[
      cl(p,prefix+'workspace','Workspace','__init__ open close flush make_current is_current id max_transmission_length try_repair make_table make_vector globals','workspace','连接与句柄生命周期、参数容器和显式会话；本次固定任务只直接构造离线通道。'),
      cl(p,prefix+'functions','FunctionCollection','__init__','command','Workspace中的db/rod/sch等前缀集合，用动态__getattr__产生RemoteFunction，不把函数名膨胀为源码方法。'),
      cl(p,prefix+'functions','RemoteFunction','__init__ __call__ lazy var','command','统一远程函数的参数编码、预览与调用；每个动态SKILL名字沿用同一源接口。'),
      cl(p,prefix+'functions','LiteralRemoteFunction','lazy var','command','需要保留SKILL原名时使用的调用对象。','Workspace索引协议或继承RemoteFunction构造获得。'),
      cl(p,prefix+'translator','DefaultTranslator','__init__ register_remote_variable_type encode decode','translate','真实客户端编码/响应解码与远程类型；当前只接收固定可信fixture文本。'),
      cl(p,prefix+'translator','Translator','encode_call encode_getattr encode_setattr encode_read_variable encode_assign encode_dir decode_dir encode_globals decode_globals encode_help decode_help','translate','继承的具体协议构造与解码辅助，排除抽象encode/decode，避免与DefaultTranslator重叠。','具体DefaultTranslator继承调用。'),
      cl(p,prefix+'remote','RemoteVariable','__init__','object','对象引用句柄绑定同一通道/translator。'),
      cl(p,prefix+'objects','RemoteObject','skill_id skill_parent_type skill_type getdoc lazy','object','远程对象类型/身份和属性查询辅助；本地引用不代表商业数据库真实存在。','由Workspace响应解码注册的RemoteVariable构造返回。'),
      cl(p,prefix+'objects','LazyList','filter foreach','object','大型库/单元列表的服务端筛选与遍历；本批未接真实库。','由RemoteObject.lazy属性返回。'),
      cl(p,prefix+'hints','Symbol',[],'translate','命名符号参数，区别字符串。','NamedTuple生成构造。'),cl(p,prefix+'hints','Key',[],'translate','SKILL关键字参数值。','NamedTuple生成构造。'),
      cl(p,prefix+'var','Var',[],'object','惰性变量引用，保留数据流而不请求当前值。','dataclass生成构造和运算协议，未伪造未抽取的dunder方法。'),
      cl(p,prefix+'translator','ParseError',[],'diagnose','真实响应错误映射到客户端异常。','由DefaultTranslator.decode抛出，继承Exception。'),
    ]+fn(p,prefix+'translator','snake_to_camel camel_to_snake python_value_to_skill build_skill_path','translate','名称/值和对象路径转换，诊断参数名与层级引用。')+fn(p,prefix+'functions','keys','translate','显式SKILL关键字列表构造。')
    designs.append(dict(scenario_id='03.11.04',
      description='面向Python控制Virtuoso原理图/版图的商业设计桥接，选择正式发布skillbridge替代无发布标签的BAG/BAG3候选；以会话、cellview引用、路径参数及check/save请求序列为状态。当前只验证真实客户端对冻结命令合同的编码、对象绑定和错误解码，不连接Cadence，不把固定响应当成实际版图生成或DRC通过。',
      packages=[('skillbridge','primary','官方正式发布的Virtuoso SKILL桥接；作为商业设计调用层，不能宣称替代BAG完整模拟生成器/验证框架。')],symbols=symbols,
      sources=src(['https://unihd-cag.github.io/skillbridge/usage/overview.html','https://unihd-cag.github.io/skillbridge/examples/create_path.html','https://unihd-cag.github.io/skillbridge/examples/lib_save.html','https://github.com/unihd-cag/skillbridge/blob/releases/1.8.0/tests/test_test_channel.py']),
      entities=[dict(name='workspace',identity='workspace_id',attributes=['channel','translator','is_current','closed','max_transmission_length','backend_kind'],lifecycle='构造离线通道/未来受控真实连接→发请求→flush/close；本地测试不打开商业服务。'),
        dict(name='cellview_reference',identity='remote_variable_id',attributes=['library','cell','view','view_type','open_mode','workspace_ref'],lifecycle='由响应解码返回句柄→绑定后续命令→错误后重新取得正确句柄；本地句柄只为fixture。'),
        dict(name='command_plan',identity='plan_id/revision',attributes=['function_prefix','function_name','keyword_values','layer_purpose','width_um','points_um','expected_request'],lifecycle='Python参数→真实lazy编码预览→独立冻结文本比较→按请求顺序发送；错误宽度需改参数重编码。'),
        dict(name='protocol_evidence',identity='transcript_id',attributes=['requests','scripted_responses','ParseError','save_after_check','repeat_match','domain_execution'],lifecycle='固定fixture对每请求严格比较→真实解码→错误后不得save→修复并重新执行；domain_execution永为not_executed。')],
      bridges=[dict(source='Workspace dynamic db/rod functions',target='RemoteFunction/Translator SKILL text',contract='open_cell_view_by_type映射dbOpenCellViewByType；cv_id映射?cvId且保留同一remote引用，layer为层/用途列表，坐标和宽度本例按um。动态名字是参数，不是新源API。'),dict(source='strict offline text fixture',target='DefaultTranslator/ParseError',contract='手写固定命令/响应序列只验证客户端协议；返回True不是商业成功证明，未实现任何SKILL几何或数据库引擎。')],
      runtime_infrastructure=[dict(reference='runtime.strict_offline_fixture_channel',kind='infrastructure',reason='本地FixtureChannel仅对固定SKILL文本逐项比较并返回录制的可信响应；不是领域求解器或Cadence模拟器。未把官方DummyWorkspace的测试方法隐藏为Agent动作。'),dict(reference='runtime.dynamic_function_and_remote_construction',kind='construction_protocol',reason='Workspace初始化FunctionCollection，动态前缀属性生成RemoteFunction；响应由DefaultTranslator注册RemoteObject工厂。具体db/rod名字沿用选中的__call__/lazy方法，不新增假函数。'),dict(reference='python.frozen_protocol_oracle',kind='infrastructure',reason='独立手写官方示例结构对应的SKILL字面量与严格请求顺序，不调用被测encode产生期待值；JSON只保存transcript。')],
      tasks=[recipe('repair_layout_command_width_contract','按LIB/DIVIDER/layout与0.08um宽度、两正交5um路径规格，真实编码和发送open→rodCreatePath×2→save；0.8um错误命令必须与冻结合同不符，改回0.08重编码完全匹配，并关闭会话。','只用严格离线文本fixture；cv句柄/响应是预置值，目标是请求合同而非真实版图。',[
        ('创建会话和动态调用对象',['skillbridge.client.workspace.Workspace.__init__','skillbridge.client.functions.FunctionCollection.__init__','skillbridge.client.functions.RemoteFunction.__init__']),
        ('错误预览与修复编码',['skillbridge.client.functions.RemoteFunction.lazy','skillbridge.client.translator.Translator.encode_call']),
        ('严格请求发送与解码后关闭',['skillbridge.client.functions.RemoteFunction.__call__','skillbridge.client.translator.DefaultTranslator.decode','skillbridge.client.workspace.Workspace.flush','skillbridge.client.workspace.Workspace.close'])],['固定完整SKILL命令字面量一致','错误width0.8拒绝/修复.08','remote cv引用一致','两次重复一致且domain_execution=not_executed'],'verify_commercial_bridge.py'),
        recipe('repair_schematic_view_and_save_sequence','只读r模式的schematic句柄收到脚本化error时，真实客户端抛ParseError且不能继续save；改a模式取得新句柄，按check→save固定顺序编码/解码后关闭。','错误响应仅由明确fixture脚本提供，不代表真实Cadence权限检查。',[
          ('打开并接收协议错误',['skillbridge.client.functions.RemoteFunction.__call__','skillbridge.client.translator.DefaultTranslator.decode','skillbridge.client.translator.ParseError']),
          ('改模式重发并关闭',['skillbridge.client.functions.RemoteFunction.lazy','skillbridge.client.functions.RemoteFunction.__call__','skillbridge.client.workspace.Workspace.close'])],['错误被ParseError保留','错误后没有save请求','修复使用新editable引用且check先于save','两次固定transcript一致'],'verify_commercial_bridge.py')],
      boundaries=['BAG/BAG3官方两仓无release/tag，排除未发布源码，不将同名无关包混入；skillbridge只是商业设计桥接层，不是BAG完整生成器的功能等价物。','无Virtuoso、SKILL server、商业许可证或真实PDK；未真实生成/保存schematic/layout，未运行DRC、LVS、仿真。','实际运行的是发布客户端的命令生成和响应解码；手写fixture返回True只代表录制响应，验收对象为独立请求合同/序列。','动态SKILL函数名不作为独立Python源方法计数；未来须基于官方/受控服务器能力约束其白名单与参数，不能任意字符串推断为已实现工具。','参考响应解码仅用于可信固定fixture/受控对端；后续Agent状态、权限、reset/step与真实远程隔离尚未实现。'],
      count_exception='公开客户端通过统一动态RemoteFunction承载商业函数，不按任意SKILL名字膨胀数量；约47个实际源参考操作覆盖会话/命令/值/对象/错误合同，不为凑50引入测试工具或服务器内部方法。',
      runtime_report=(BASE/'runtime/commercial_bridge/03.11.04.json').as_posix(),runtime_scope='离线严格文本fixture，真实skillbridge客户端/Translator/ParseError；两完整合同任务各重复两次，明确没有商业后端执行。'))
    for design in designs:
        design['capabilities']=list(dict.fromkeys(s['capability'] for s in design['symbols']))
        design['excluded_unavailable_candidates']=exclusions
        design['boundaries'].append('未发布候选的完整检查记录：'+missing.as_posix()+'；SHA256='+file_sha(missing))
        c.write(design)

if __name__=='__main__':main()
