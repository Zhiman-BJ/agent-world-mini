"""Amaranth-centered RTL environment with competing HDL frameworks excluded."""
from seed_gen.scripts.scenario_collection_support import Collection,symbol,recipe
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW


def s(module,name,methods,cap,why,construction=None):
    return symbol('amaranth','amaranth.'+module,name,methods,cap,why,construction)


def main():
    c=Collection(base=BASE,raw=RAW)
    symbols=[
        s('hdl._ir','Elaboratable',[],'describe','用户DUT实现elaborate的公开协议，生成真正硬件片段，不用Python数值函数代替。','继承Elaboratable并定义DUT自身__init__/elaborate；库基类无源码构造方法，保留原样。'),
        s('hdl._dsl','Module','__init__ If Elif Else Switch Case Default FSM State elaborate','describe','组合/同步语句、分支和状态机结构，剔除底层IR变换。'),
        s('hdl._ast','Signal','__init__ shape like','signal','可定义宽度/初值并查询形状的硬件信号。'),
        s('hdl._ast','Value','cast shape as_unsigned as_signed bool all any xor bit_select word_select replicate matches eq','signal','Signal等真实继承的表达式/切片/赋值能力，不重复运算符魔术方法。','Signal、Const和表达式返回Value，不直接构造抽象Value。'),
        s('hdl._ast','Const','__init__ cast shape','signal','明确宽度和符号的常量。'),
        s('hdl._ast','Cat',None,'signal','组合数据总线。'),s('hdl._ast','Mux',None,'signal','条件选择硬件值。'),s('hdl._ast','signed',None,'signal','带符号形状。'),s('hdl._ast','unsigned',None,'signal','无符号形状。'),s('hdl._ast','Assert',None,'verify','构造硬件断言；固定运行采用testbench外部断言。'),
        s('hdl._cd','ClockDomain','__init__ rename','clock','时钟/复位域状态，避免时钟名字错配。'),s('hdl._ast','ClockSignal','__init__','clock','显式引用时钟域输入。'),s('hdl._ast','ResetSignal','__init__','clock','显式引用复位域输入。'),
        s('lib.wiring','Component','__init__','interface','带签名的高层模块接口，与Elaboratable兼容。'),s('lib.wiring','Signature','__init__ flip flatten is_compliant create','interface','输入输出签名与连接合规检查，裁剪注释/元编程。'),s('lib.wiring','Member','__init__ flip array','interface','单端口/数组成员规格，方向和形状为状态。'),
        s('lib.fifo','SyncFIFO','__init__','queue','实际同步FIFO实现，宽度/深度和握手信号契约固定。'),s('lib.fifo','SyncFIFOBuffered','__init__','queue','输出缓冲变体；延迟多一拍明确不与SyncFIFO混同，固定样例未运行此变体。'),
        s('sim.core','Simulator','__init__ add_clock add_testbench run run_until write_vcd reset','simulate','公开事件仿真、时钟、async testbench、波形和重置；排除弃用生成器process接口。'),
        s('sim._async','SimulatorContext','get set tick delay changed edge posedge negedge','simulate','调度器提供的读写/时序控制上下文；输出观察发生在tick完成后。','由Simulator调用已注册testbench时提供，不自行构造。'),
        s('sim._async','TickTrigger','sample until repeat','verify','同步采样与有限周期等待，使验证步骤表达明确。','由SimulatorContext.tick返回。'),
    ]
    c.write(dict(scenario_id='03.05.01',description='以Amaranth统一信号、组合/同步逻辑、接口、FIFO和事件仿真；固定输入序列与外部周期/事务oracle发现功能错误，修改设计后重新构建DUT并复验。PyMTL3、PyRTL、MyHDL为重叠替代候选，不混入多套信号/调度器。',
        packages=[('amaranth','primary','信号/模块/时钟/仿真/FIFO形成一个完整本地闭环。'),('pymtl3','excluded','另一套组件/仿真/翻译体系，与当前逻辑能力重复。'),('pyrtl','excluded','另一套RTL线网/仿真体系，无当前必需独占能力。'),('myhdl','excluded','生成器调度和HDL转换与当前核心重复，不额外引入旧语言兼容边界。')],
        sources=[c.source('https://amaranth-lang.org/docs/amaranth/v0.5.10/start.html','官方固定0.5.10入门用带使能/溢出计数器展示Component、Module条件赋值、async testbench和VCD；明确Verilog转换及FPGA工具链是另一步。',['DUT','signal','clock','waveform'],['Module','Signal','Simulator'],['固定周期计数与错误增量定位']),c.source('https://amaranth-lang.org/docs/amaranth/v0.5.10/simulator.html','官方仿真教程说明add_clock/add_testbench和ctx.get/set/tick，tick等待电路反应后才返回；run/write_vcd记录真实事件模拟。',['testbench','simulator_context','cycle','expected_value'],['Simulator.add_testbench','SimulatorContext.tick','SimulatorContext.get'],['使能暂停、回绕及正常/错误/修复三次独立仿真']),c.source('https://amaranth-lang.org/docs/amaranth/v0.5.10/stdlib/fifo.html','官方FIFO API正文定义w_en/w_rdy、r_en/r_rdy、level和FIFO顺序；满时写无效、空时读无效，Buffered变体读延迟额外一拍。',['FIFO','depth','occupancy','handshake','word_sequence'],['SyncFIFO','SimulatorContext.set','SimulatorContext.get'],['深度错误/背压数据丢失修复、满空与顺序断言'])],
        entities=[dict(name='rtl_design',identity='design_id+revision',attributes=['signals shapes/init','combinational equations','synchronous equations','clock_domains','submodules','interface'],lifecycle='describe -> elaborate -> simulate；逻辑/宽度修改后新建DUT/Simulator，不复用旧结果。'),dict(name='testbench',identity='test_id',attributes=['fixed stimulus','expected cycle table','clock_period','timeout','assertions'],lifecycle='load immutable target -> register -> run -> compare；修复不得改变expected表。'),dict(name='fifo_state',identity='fifo_id',attributes=['width','depth','w_data/w_en/w_rdy','r_data/r_en/r_rdy','level','accepted_words'],lifecycle='empty -> accept writes -> full/backpressure -> ordered drain -> empty；拒绝写不得覆盖旧数据。'),dict(name='simulation_artifact',identity='run_id',attributes=['design_revision','observed trace','first_bad_cycle','VCD path','status'],lifecycle='normal/error/repaired each fresh simulation -> persisted report；不代表门级时延或硬件上板。')],capabilities=['describe','signal','clock','interface','queue','simulate','verify'],symbols=symbols,bridges=[],
        runtime_infrastructure=[dict(reference='python_fixed_cycle_transaction_oracle',kind='infrastructure',reason='手写9周期模8计数表与4个固定数据字；Python只组织testbench和断言，DUT求值由真实Amaranth引擎执行。'),dict(reference='amaranth.public_reexports',kind='public_alias_map',reason='amaranth.Signal/Module/Elaboratable、amaranth.sim.Simulator/SimulatorContext实际定义于hdl._ast/_dsl/_ir与sim.core/_async；保留真实源码模块，未复制别名。')],
        boundaries=['未运行外部Verilog仿真器、综合、FPGA布局布线或上板；仅真正Amaranth事件仿真。','PyMTL3采用最新GitHubReleasev3.1而PyPI3.1.17；MyHDL最高稳定tag0.11而PyPI0.11.52，版本差异证据保留，这些替代不参与运行。','MyHDL候选枚举完整运行库但排除test/tests，含Python2反引号的故意失败测试不是公开API；不更改原源码。','FIFOInterface信号字段和Module.d/submodules是状态/DSL容器，未伪造成普通函数；全部候选未逐一实跑。'],
        tasks=[recipe('repair_counter_increment','构造3bit带使能计数器，按固定9周期输入验证暂停和7→0回绕；发现增量2导致第一周期为2，恢复增量1后重建DUT并通过同一手写表。','enable=[1,1,0,1,1,1,1,1,1]，目标=[1,2,2,3,4,5,6,7,0]。',[('描述信号与同步逻辑',['amaranth.hdl._ir.Elaboratable','amaranth.hdl._dsl.Module.__init__','amaranth.hdl._dsl.Module.If','amaranth.hdl._ast.Signal.__init__','amaranth.hdl._ast.Value.eq']),('建立事件仿真与驱动',['amaranth.sim.core.Simulator.__init__','amaranth.sim.core.Simulator.add_clock','amaranth.sim.core.Simulator.add_testbench','amaranth.sim._async.SimulatorContext.set','amaranth.sim._async.SimulatorContext.tick','amaranth.sim._async.SimulatorContext.get']),('执行并保存波形，修复后重复',['amaranth.sim.core.Simulator.write_vcd','amaranth.sim.core.Simulator.run'])],['正常逐周期等于固定表','错误第一周期2不等于1','修复再次等于固定表'],'verify_rtl.py:counter'),recipe('repair_fifo_depth_and_verify_backpressure','写入17/34/51/68四字，满后额外尝试写99并保持数据；依次读回，验证深度/满空/顺序。将误设深度2导致后两字未接收的配置改为4后按同目标复验。','width8/depth4目标，输入四字固定，额外第五字应被满背压拒绝。',[('构造FIFO及真实仿真',['amaranth.lib.fifo.SyncFIFO.__init__','amaranth.sim.core.Simulator.__init__','amaranth.sim.core.Simulator.add_clock']),('驱动写握手并记录占用',['amaranth.sim._async.SimulatorContext.set','amaranth.sim._async.SimulatorContext.tick','amaranth.sim._async.SimulatorContext.get']),('有序读取和错误修复后复跑',['amaranth.sim.core.Simulator.add_testbench','amaranth.sim.core.Simulator.run','amaranth.sim.core.Simulator.write_vcd'])],['正常accepted4、level=[1,2,3,4]','满后99不覆盖旧字','深度2仅读回17/34，目标失败','修复读回17/34/51/68，最终empty'],'verify_rtl.py:fifo')],
        runtime_report=(BASE/'runtime/rtl/03.05.01.json').as_posix(),runtime_scope='Amaranth0.5.10真实事件仿真，两个固定正常/错误/修复任务和VCD工件。'))


if __name__=='__main__':
    main()
