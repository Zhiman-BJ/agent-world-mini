# 03 芯片设计与流片、04 实验室表征与设备控制执行记录

2026-09-18整理：本组输出已合入 [正式产物](../../pypi_outputs/final_results/README.md)，L3统一位于`final_results/l3/`；重复worker输出已移出产物目录，恢复位置见正式产物说明。下文旧输出路径和命令保留为重建记录，重建的worker目录不上传。

原分工为 L1 03 的24项 + L1 04 的7项，共31个L3。随后03.12.01–04光刻四项移交`lithography`，03.11.01/04模拟设计和协同两项移交`analog_bridge`；本分组最终独占 **25个L3（03的18项 + 04的7项）**，不改02及其他分组。25项均有至少两个真实正常/错误/修复固定任务，且全部通过`--verify-sources`构建与`--check`复核；合计334类、130顶层函数、1361类方法，1491参考操作、50条实跑任务。仅最终种子进入 `seed_gen/pypi_outputs/scenario_collection/l1_design_lab/`，包发现记录不表示整个包或所有工具已逐项验证。

## 已完成小样例

03.08.01 Waveform Investigation：vcdvcd 2.6.0 主索引 + pyvcd 0.5.0 写入/元数据检查/复查配置。全量候选分别 16、64 个参考操作，联合选择 7 类、1 函数、37 类方法，共 **38** 操作；不足 50 的理由保存在 profile，未保留弃用 getter 或内部格式化函数凑数。

两条任务真实运行：计数波形在 30 ns 的首个错误值定位与修复；1 us 错误单位修复到 1 ns，首跳变固定为 10 ns，并验证倒序时间被拒绝。pyvcd 写文件 → vcdvcd 读文件桥接通过，文件别名和位宽保持；另生成 GTKW 配置，但没有运行 RTL 仿真器或 GUI。正常/修复文件字节一致。

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_sources --discover
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_design_lab/research/waveform_urls.json --output seed_gen/scenario_collection/l1_design_lab/research/waveform_web
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_waveform_sources
C:/Apps/anaconda3/Scripts/uv.exe venv .venv-scenario-design-waveform --python C:/Apps/anaconda3/python.exe
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-design-waveform/Scripts/python.exe pyvcd==0.5.0 vcdvcd==2.6.0
.venv-scenario-design-waveform/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_waveform.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_waveform_profile
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/l1_design_lab/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/l1_design_lab --merged-name semiconductor_scenario_design_lab.json --group-by-l1 --verify-sources
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/l1_design_lab/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/l1_design_lab --merged-name semiconductor_scenario_design_lab.json --group-by-l1 --check
```

版本证据：vcdvcd 无 GitHub Release（404），PyPI 2.6.0 与官方 v2.6.0 tag/setup.py 版本一致，提交 `515273543d74da21588785a2b192159c8ac7a66a`。pyvcd 最新正式 Release 0.5.0，提交 `2bb6080c036b5b7d77f55bd2ae61579ef0dc43c7`；在线 latest 文档显示 0.5.1.dev0，签名始终来自固定 tag。

实际遇到并修正的问题：

- pyvcd 0.5.0 改用 `src/vcd` 布局，最初提取 `Cannot resolve module 'vcd' below ...pyvcd`；source_root 修正为 src，未修改源码。
- writer 注册名为 count 时，VCD 文件引用是 `dut.count`，不会根据位宽自动附加 `[1:0]`；首次运行 `KeyError: 'dut.count[1:0]'` 后按真实声明修正。位宽作为独立属性验收。
- 倒序 change 的真实异常是 `VCDPhaseError: Out of order timestamp: 5`，不是 ValueError；按固定版本接口修正捕获并保留错误文本。

## 实验室批次

| 场景 | class | function | class_func | all_func | 固定验收 |
| --- | ---: | ---: | ---: | ---: | --- |
| 04.01.01 DC IV | 11 | 3 | 78 | 81 | Vth 0.4V、gm 1mS、100uA限流错误修复 |
| 04.01.02 CV | 11 | 3 | 78 | 81 | Vbi 0.8V、1MHz恢复目标曲线、F/pF单位修复 |
| 04.01.03 Stress | 11 | 3 | 78 | 81 | 两温度阈值表、39.0625s达到30mV漂移 |
| 04.02.01 Pulse/AWG | 15 | 1 | 82 | 83 | 真实emulation编译/输出模拟，80ns×0.4、192点、原生波形往返 |
| 04.03.01 Driver | 12 | 2 | 77 | 79 | 真实E5080B贡献驱动+官方YAML，频段/范围/枚举错误修复 |
| 04.04.01 VISA | 4 | 2 | 32 | 34 | 真实TCP socket、三点回读、终止符错误超时→修复 |
| 04.04.02 Virtual Instrument | 11 | 2 | 76 | 78 | QCoDeS→PyVISA→YAML@sim；量程不匹配ESR位32→修复 |

合计本分工当前555个参考操作（含波形38），16条任务。三种measurement任务使用脚本中明确声明的解析虚拟DUT，实际执行QCoDeS参数/量程/采集/SQLite读回，不是实测半导体或经过校准的TCAD/BTI模型。VISA任务真实使用pyvisa-py连接本机127.0.0.1 SCPI fixture，未扫描外网或连接实体仪器。

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_design_lab/research/lab_urls.json --output seed_gen/scenario_collection/l1_design_lab/research/lab_web
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_design_lab/research/lab_additional_urls.json --output seed_gen/scenario_collection/l1_design_lab/research/lab_additional_web
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_design_lab_lab_sources
C:/Apps/anaconda3/Scripts/uv.exe venv .venv-scenario-lab-measurement --python C:/Apps/anaconda3/python.exe
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-lab-measurement/Scripts/python.exe qcodes==0.59.0 pyvisa==1.16.2 pyvisa-py==0.8.1 pyvisa-sim==0.7.0 qcodes-contrib-drivers==0.25.0
.venv-scenario-lab-measurement/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_measurement.py
.venv-scenario-lab-measurement/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_transport.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_measurement_profiles
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_transport_profiles
```

随后执行前节构建与`--check`。截至此记录8场景两项均通过，`uv pip check`确认measurement环境86包、pulse环境77包兼容。

实际错误与修正：QCoDeS 0.59.0 `load_by_id`后`get_parameter_data(Parameter)`以短名查找导致`KeyError: 'current'`，修正为传`parameter.full_name`；SQLite数值序列化误差最大约1.08e-19A（约1e-16相对误差），持久化比较使用rtol2e-14，独立物理目标容差未放宽。contrib源码含UTF-8 BOM触发AST `invalid non-printable character U+FEFF`，由主代理修正共享读取为utf-8-sig并保留原字节哈希，33项回归通过；本分工随后全量提取成功。

PyVISA-sim GitHub正式Release为0.7.0，PyPI为0.7.1，按指南选择Release并在venv安装0.7.0。QCoDeS网页latest为0.60.dev、PyVISA网页为1.16.3.dev，参考签名分别固定0.59.0与1.16.2。错误旧网址404保留在网页索引，不计有效来源。

驱动/AWG批次命令：

```powershell
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_design_lab_vendor_sources
.venv-scenario-lab-measurement/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_driver.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_driver_profile
C:/Apps/anaconda3/Scripts/uv.exe venv .venv-scenario-lab-pulse --python C:/Apps/anaconda3/python.exe
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-lab-pulse/Scripts/python.exe laboneq==26.7.0 zhinst-toolkit==1.4.0
.venv-scenario-lab-pulse/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_pulse.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_pulse_profile
```

相关网页清单分别为`research/driver_urls.json`、`pulse_urls.json`、`pulse_additional_urls.json`，采用相同collector命令。GitHub notebook页面虽然200但只有iframe占位，未计入有效来源；已找到正文可读的官方教程。LabOne Q目录为`src/python/laboneq`；`pulse_library.const/gaussian`被装饰器转为工厂，源码AST签名是采样器，当前选`PulseFunctional`真实attrs构造而不使用错误签名。实际编译调用安装wheel中的Rust后端，输出矩形0.4/80ns/192点；错误面积16ns修复为32ns，Toolkit打包误差2.44148e-5≤1/32767，未执行硬件校准。

RsInstrument官方仓库无tag或Release且仅一次提交；不能把main当发布版。`prepare_design_lab_vendor_sources.py`下载PyPI1.131.0 sdist校验SHA256，将全部包内.py逐个与固定`b600fd6cf693dbcfa3cbfba0f638dfa14ff2c0aa`比较（仅CRLF/LF规范化）一致；证据`research/rsinstrument_release_equivalence.json`。manifest的tag是分发版本，ref是实际commit，未创建Git标签。此包在VNA场景排除作为替代通信封装。

## 版图、规则及连通批次

| 场景 | class | function | class_func | all_func |
| --- | ---: | ---: | ---: | ---: |
| 03.01.01 PCell/hierarchy | 14 | 8 | 59 | 67 |
| 03.01.02 Routing | 14 | 11 | 59 | 70 |
| 03.02.01 GDS/OASIS CRUD | 9 | 0 | 58 | 58 |
| 03.02.02 DRC | 7 | 0 | 56 | 56 |
| 03.02.03 Connectivity | 7 | 0 | 53 | 53 |

本批304参考操作、10条真实正常/错误/修复任务。`verify_gdsfactory.py`验证10µm+20µm、宽0.5µm层次链：面积15µm²、末端30µm、两个顶层端口和一条内部网；错误25µm第二段→35µm末端/17.5µm²，修复回目标；错误1µm端口宽度触发真实PortWidthMismatchError。路由为100µm直线路径与3×100µm bundle，修复错误放置与3源/2终点错误，GDS原生重读核对面积60/150µm²与3个独立路径。

`verify_klayout.py`验证两矩形层次的GDS/OASIS往返、DBU误设导致100µm²→恢复1µm²、矩形并/交面积；width/space100nm规则真实生成违规EdgePairs，80nm错误→120/150nm修复归零；真实LayoutToNetlist/probe_net提取中漏via造成3网断路、多余桥造成1网短路，修复后固定A-B连通/C独立的2网拓扑通过。连通提取不等于完整MOS器件提取/LVS。

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_layout_sources
C:/Apps/anaconda3/Scripts/uv.exe venv .venv-scenario-design-layout --python C:/Apps/anaconda3/python.exe
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-design-layout/Scripts/python.exe gdsfactory==9.51.0 klayout==0.30.12 gdstk==1.0.1
.venv-scenario-design-layout/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_klayout.py
.venv-scenario-design-layout/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_gdsfactory.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_klayout_profiles
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_gdsfactory_profiles
```

随后运行前文`build_joint_scenario_seeds --verify-sources`与`--check`。全部13项通过；layout环境102包的`uv pip check`通过。网页采集清单`research/layout_urls.json`、`layout_additional_urls.json`、`layout_final_urls.json`保留失败/占位证据，但场景来源只引用已读正文。klayout.de为200 challenge，采用官方klayout.org和固定tag源码文档；gdsfactory旧.html只是31字符跳转，改用带结尾/的实际文档URL。

版本组合问题：首次强制安装最新kfactory3.2.1出现`gdsfactory==9.51.0 depends on kfactory[ipy]>=3.0.4,<3.1.dev0 ... requirements are unsatisfiable`。依官方硬依赖固定3.0.4提交`ef56a574d7a2ac5da0751e9804167ace5f394a0d`，最新3.2.1提交`493a9b5ab2c54f05d6641f7def07d83f32f8e818`仅作为版本比较证据，见research/kfactory_compatibility.json，全量候选池1037操作保留但profile不引用。兼容3.0.4全量1016操作，gdsfactory942、KLayout4532、GDSTK195。GDSTK使用共享原生导出+stub/doc适配，作为重叠替代excluded，不重复加入行动空间。

运行时还发现：未激活generic PDK会抛`ValueError: No active PDK`，任务明确先执行Pdk.activate。最初错误假设单一直线路由`route_width=1.0`一定会拒绝0.5µm端口，实际该路线仍保持端口宽度/面积，故未把它报告成成功的异常测试；改为可观测且真实生效的目标放置错误，90µm/55µm²→100µm/60µm²。原生连接的PortWidthMismatchError测试保留且真实通过。

## 原理图、RTL与AST批次

| 场景 | class | function | class_func | all_func |
| --- | ---: | ---: | ---: | ---: |
| 03.04.01 原理图 | 17 | 29 | 29 | 58 |
| 03.04.02 Schema/网表 | 17 | 30 | 29 | 59 |
| 03.05.01 RTL | 16 | 5 | 62 | 67 |
| 03.08.02 AST | 50 | 0 | 44 | 44 |

本批228操作、8条固定正常/错误/修复任务，全17项已重新来源提取、生成及`--check`通过。AST空方法语义类型保留构造继承关系，`children/show`统一保留Node源定义，生成入口visit保留ConvertVisitor，不复制到每个子类凑数，故44操作合理。

`verify_schematic.py`实际HDL21/VLSIR/VLSIRTools6.0.0构造两管NMOS电流镜结构，固定d/g/s/b连接表与W尺寸比2。错误输出管gate=out修复iin、W比3修复2；通用物理Nmos直接网表化触发`Invalid direct-netlisting of physical ... Either compile to a target technology, or replace with an ExternalModule.`，以显式NMOS ExternalModule修复；真实protobuf往返后误引用missing_gate_net触发`Unknown signal`，恢复iin后SPICE文本相同。没有执行SPICE，尺寸比不能声称模拟电流比。HDL21动态datatype构造/calls_instantiate协议存入metadata，源__call__ opt-in保留，全量500操作。

`verify_rtl.py`实际Amaranth0.5.10 Simulator对3bit计数器9周期运行，手写表[1,2,2,3,4,5,6,7,0]；增量2失败修回1。SyncFIFO四字17/34/51/68，depth2丢后两字，修回4；满时99不覆盖旧字，最终按序排空。真实VCD保存。结束后reset会重新创建未await testbench coroutine而警告，删去无必要reset后重新运行干净通过。

`verify_ast.py`实际PyVerilog1.3.0编辑/重解析/代码生成并交WSL Icarus12.0编译执行。四组固定(0,0)/(1,2)/(255,1)/(255,255)手算9bit和[0,3,256,510]；漏b得到[0,1,255,255]，Plus(a,b)修复；8bit输出截断[0,3,0,254]，改9bit修复。Icarus由`apt-get download iverilog`与`dpkg-deb -x`解包到`/home/zjs32/.local/share/semiconductor-design-lab-20260917/iverilog`，没有全局apt安装，需显式`-B .../usr/lib/x86_64-linux-gnu/ivl`。不是形式等价证明或完整SystemVerilog验证。

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_logic_sources
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_hdlconvertor_sources
.venv-scenario-design-schematic/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_schematic.py
.venv-scenario-design-logic/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_rtl.py
.venv-scenario-design-logic/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_ast.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_schematic_profiles
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_rtl_profile
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_ast_profile
```

原理图venv使用Python3.11.16：hdl21/vlsir/vlsirtools6.0.0；Python3.12首次因pandas1.5.3构建报`ModuleNotFoundError: pkg_resources`，改3.11后13包兼容检查通过。官方最新稳定tag6.0.0，PyPI7.0.0另记版本差异，未把main当发布源码。logic环境24包兼容。HDLConvertor候选原先Python wrapper不足，新增原生适配读取真实Cythondef、导出别名与include链，7类/2函数/20方法=22操作，__cinit__只作元数据。32项提取/联合选集回归通过；未编译hdlConvertor，仅作为PyVerilog替代候选excluded。

## PDK与真实HDL验证批次

| 场景 | class | function | class_func | all_func |
| --- | ---: | ---: | ---: | ---: |
| 03.03.01 PDK管理 | 2 | 6 | 7 | 13 |
| 03.06.01 cocotb功能验证 | 20 | 3 | 47 | 50 |
| 03.06.02 pyuvm事务验证 | 17 | 3 | 54 | 57 |
| 03.07.01 AXI-Stream | 13 | 3 | 37 | 40 |

本批160操作、8条实际任务，21场景合计1247操作/42任务的来源重提取、构建和`--check`均通过。Volare13与AXI40操作都有profile例外理由，未添加云端PDK上传或未运行AXI-MM/APB来凑数。

`verify_volare.py`实际操作全新fixture缓存中的sky130A/B、libs.tech和libs.ref/sky130_fd_sc_hd，两个明确虚构的40字符revision分别为全1和全2。真实`fetch/enable`在已有本地缓存上创建/切换链接，版本2修回1；错误库sky130_fd_sc_typo触发真实`ValueError: Unknown library`并修复。没有下载完整PDK、构建器件模型或运行工艺仿真；fixture revision不能作为真实工艺版本。

`verify_cocotb.py`与`test_hardware_verification.py`实际使用Icarus12.0和cocotb2.1.0 GPI执行`fixtures/verification_dut.sv`。`runtime/cocotb/results.xml`中三套测试全部成功，0错误/0失败。

- cocotb：四组输入的9bit和固定为[0,3,256,510]；drop_b错误得到[0,1,255,255]，truncate_carry错误得到[0,3,0,254]，两个修复任务恢复原目标表。
- pyuvm5.0.0：真正运行run_test、sequence/sequencer/driver、analysis端口和subscriber。错误硬件功能被scoreboard捕获；vector_count=3虽前三条值正确，仍因漏第四事务而coverage_complete=False，改4后恢复。观察由driver实际DUT采样后发布，未声称有另一独立monitor或完整UVM寄存器层。
- cocotbext-axi0.1.28：真实AXI-Stream source/sink/monitor传[0,1,127,128,255]并注入[1,1,0,0]周期背压。XOR1错误得到[1,0,126,129,254]；ready阻塞200ns期间source仍pending、sink/monitor为空，解除后收到原帧且source最终idle。未运行AXI-MM/APB/CDC。

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_verification_sources
.venv-scenario-design-logic/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_design_lab/verify_volare.py
wsl -d Ubuntu-24.04 -- /home/zjs32/.local/share/semiconductor-design-lab-20260917/venv/bin/python /mnt/d/Desktop/agent-world-mini/seed_gen/scenario_collection/l1_design_lab/verify_cocotb.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_pdk_profile
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_verification_profiles
```

WSL环境Python3.12.3，41包依赖兼容；uv0.8.15和Icarus在用户私有prefix，不全局安装。cocotbext依赖内部调用弃用的setimmediatevalue/Edge/Task.kill有DeprecationWarning，固定版本实际可运行，未修改官方源码。API重导出的源模块保留唯一原始定义；类的继承构造及protocol分别入metadata，不为每个派生类复制动作。

## 最后四项：流程、IP与模拟版图局部链

| 场景 | class | function | class_func | all_func | 实跑边界 |
| --- | ---: | ---: | ---: | ---: | --- |
| 03.09.01 RTL→GDS Flow | 9 | 0 | 62 | 62 | SC→Slang前端调度→独立Icarus；未完成后端P&R |
| 03.10.01 IP/Dependency | 13 | 0 | 58 | 58 | FuseSoC→EDAM→Edalize→make/Icarus真实构建仿真 |
| 03.11.02 Analog Auto-layout | 12 | 12 | 51 | 63 | 网表图 + mock PDK电容原语/DRC/LEF/GDS两个局部子链 |
| 03.11.03 Gridded Layout | 15 | 3 | 58 | 61 | 模板/栅格/放置/引脚/YAML/SKILL文本 |

本批244操作、8条实跑任务。`prepare_design_lab_eda_sources.py`的固定发布源码全量候选：SiliconCompiler2485、LibreLane593、FuseSoC204、Edalize310、laygo2 220、ALIGN291。场景03.09只选SiliconCompiler为主，LibreLane编排能力重叠排除；03.10的FuseSoC和Edalize通过EDAM契约互补；其他各使用单一几何/工艺状态体系。

`verify_siliconcompiler.py`实际SC0.38.8调度pyslang11.0.0展开Verilog；wrong top `missing_top`触发实际Slang错误并导致RuntimeError，恢复adder后errors=0。WIDTH=8产生[0,3,0,254]，9恢复[0,3,256,510]，结果由独立Icarus执行生成Verilog核对，未引用模块unused被剔除。**仅验证RTL到GDS的前端调度子链，未执行综合、timing/congestion/DRC、CTS/P&R或GDS流片**；这个限制已进入最终description/profile/runtime，不能将整个分类视作完整运行通过。初次Project先于chdir创建，捕获旧cwd写到build/adder；已用OptionSchema.set_builddir隔离每次run。确定为本脚本的旧目录可恢复移动到`runtime/siliconcompiler/initial_unisolated_build_adder`，最终报告所有工件在各自独立目录。

`verify_fusesoc.py`在新临时目录创建两个CAPI2 core `seed:lib:adder:1.0`和`seed:test:tb:1.0`，真实CoreManager→Edalizer.run/export→Edalize Sim.configure/build/run→make/Icarus/vvp。依赖adder2.0产生`DependencyError`，详细求解错误在exc.msg而非str(exc)；改回1.0后正确输出。EDAM WIDTH=8实际截断，再改9重建通过。最初漏Edalizer.export导致`No rule to make target src/.../adder.v`；修复导出后新flow API的选项必须放`flow_options.iverilog_options`，旧tool_options不生效导致ivlpp/ivl找不到。已按实际新API修正，没有修改包源码。

`verify_align.py`从官方v1.0源码安装，其内部版本0.9.8。SPICE反相器MN gate误接OUT使边数6→5，恢复IN后两管端口角色/6边准确。官方FinFET14nm_Mock_PDK真实生成电容原语LEF、几何JSON、二进制GDS，由独立gdstk读回：value8外包框约2.072×2.088µm超出独立1.2×1.2µm预算；恢复value2得到1.032×1.080µm、110多边形、PLUS/MINUS两引脚。两个参数mock DRC均0，说明面积预算与DRC是不同验收条件。官方DRC将Adding region写为ERROR日志，但实际num_errors=0。**没有将反相器自动布局，也未运行原生PnR、真实工艺签核或电容电学提取**。Windows路径含`<>`无法全checkout，使用WSL Git稀疏检出align/docs/examples/pdks/bin；tracked文件干净。PyPI align是无关语言学包，链接留空。

`verify_laygo2.py`使用stable-230804发布源码0.5.5，100×100nm两模板，pitch15使第二实例x90nm产生10nm重叠，pitch20修复为x120nm/间隔20nm；网名WRONG修复GATE，YAML恢复bbox仍[[0,0],[220,100]]，同时生成SKILL文本。初装同版本PyPI0.5.5的Design.bbox错误用np.minimum导致100nm范围，官方tag已修复为union；从固定发布源码重装后原220nm断言通过，没有放宽oracle。该版本SKILL import依赖`laygo2_tech`，从固定源码附带官方示例技术读取相对YAML；没有伪造模块。未运行Cadence、工艺路由/LVS。

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_eda_sources
wsl -d Ubuntu-24.04 -- /home/zjs32/.local/share/semiconductor-design-lab-20260917/uv-x86_64-unknown-linux-gnu/uv pip install --python /home/zjs32/.local/share/semiconductor-design-lab-20260917/venv-physical/bin/python --no-deps --reinstall /mnt/d/Desktop/agent-world-mini/seed_pypi_raw/l1_design_lab/laygo2
wsl -d Ubuntu-24.04 -- /home/zjs32/.local/share/semiconductor-design-lab-20260917/venv-physical/bin/python /mnt/d/Desktop/agent-world-mini/seed_gen/scenario_collection/l1_design_lab/verify_siliconcompiler.py
wsl -d Ubuntu-24.04 -- /home/zjs32/.local/share/semiconductor-design-lab-20260917/venv/bin/python /mnt/d/Desktop/agent-world-mini/seed_gen/scenario_collection/l1_design_lab/verify_fusesoc.py
wsl -d Ubuntu-24.04 -- /home/zjs32/.local/share/semiconductor-design-lab-20260917/venv-physical/bin/python /mnt/d/Desktop/agent-world-mini/seed_gen/scenario_collection/l1_design_lab/verify_align.py
wsl -d Ubuntu-24.04 -- /home/zjs32/.local/share/semiconductor-design-lab-20260917/venv-physical/bin/python /mnt/d/Desktop/agent-world-mini/seed_gen/scenario_collection/l1_design_lab/verify_laygo2.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_design_lab_eda_profiles
```

`venv-physical`59包`uv pip check`通过；laygo2和ALIGN固定仓库tracked文件干净，构建生成的untracked文件保留于raw忽略目录。网页证据包括`eda_web`与`eda_additional_web`，只引用已读200正文；404文档路径保留研究记录不计有效来源。`requirements-wsl-physical.freeze.txt`与`requirements-wsl-verification.freeze.txt`记录实际59/41包版本及源安装路径。

最终执行前文全25项`build_joint_scenario_seeds --verify-sources`得到`Generated 25 scenario-first seeds.`；随后`--check`得到`Verified 25 scenario-first seeds.`。新四项task ID与runtime证据逐个匹配，工具计数与选集一致，py_compile及所改路径diff检查通过。没有把未完成的全P&R、Cadence或真实仪器执行计作已通过。

## 总体边界

`research/package_discovery.json` 已保存 38 个分发名的 PyPI 版本/官方 URL 线索。`pade` 对应多 Agent 通信框架、`align` 对应语言学对齐，不能当作模拟设计PADE或ALIGN-analoglayout；lithosim无同名PyPI项目。ALIGN身份已核对并按官方发布源码完成本地子链；PADE/BAG/skillbridge和光刻剩余边界由已移交分组维护。

所有 reference API 并未逐项实跑；本轮也未实现完整 Agent reset/step/state 隔离。独立 oracle 仅覆盖已列脚本的固定波形、虚拟仪器、AWG输出、版图/DRC/连通任务，不代表真实工艺或仪器校准。
