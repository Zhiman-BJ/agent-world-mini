"""Fixed stimuli + hand-written oracles; wrong hardware remains observable."""
from importlib.metadata import version
import itertools
import json
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer,RisingEdge,ReadOnly,with_timeout
from cocotbext.axi import AxiStreamBus,AxiStreamSource,AxiStreamSink,AxiStreamMonitor,AxiStreamFrame
from pyuvm import uvm_root,uvm_test,uvm_env,uvm_driver,uvm_sequence,uvm_sequence_item,uvm_sequencer,uvm_subscriber,uvm_analysis_port,ConfigDB

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/cocotb'
VECTORS=[(0,0),(1,2),(255,1),(255,255)]
EXPECTED=[0,3,256,510]


def save(sid,tasks,oracle,boundaries):
    data=dict(scenario_id=sid,status='passed',versions={p:version(p) for p in ['cocotb','pyuvm','cocotbext-axi','cocotb-bus']},tasks=tasks,oracle_independence=oracle,boundaries=boundaries,backend='Real Icarus Verilog12.0 via cocotb GPI')
    (OUT/f'{sid}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


async def init(dut):
    for name in ['clk','rst','a','b','drop_b','truncate_carry','s_axis_tdata','s_axis_tvalid','s_axis_tlast','m_axis_tready','corrupt_data','block_ready']:
        getattr(dut,name).value=0
    await Timer(2,unit='ns')


async def vector_run(dut,drop=0,truncate=0):
    dut.drop_b.value=drop
    dut.truncate_carry.value=truncate
    result=[]
    for a,b in VECTORS:
        dut.a.value=a;dut.b.value=b
        await Timer(2,unit='ns')
        await ReadOnly()
        result.append(int(dut.y.value))
        await Timer(1,unit='ns')
    return result


@cocotb.test()
async def functional(dut):
    await init(dut)
    normal=await vector_run(dut)
    assert normal==EXPECTED
    missing=await vector_run(dut,drop=1)
    assert missing==[0,1,255,255] and missing!=EXPECTED
    assert await vector_run(dut)==EXPECTED
    truncated=await vector_run(dut,truncate=1)
    assert truncated==[0,3,0,254] and truncated!=EXPECTED
    assert await vector_run(dut)==EXPECTED
    save('03.06.01',[dict(id='repair_functional_operand_path',status='passed',expected=EXPECTED,wrong=missing,repaired=EXPECTED),dict(id='repair_functional_carry_path',status='passed',expected=EXPECTED,wrong=truncated,repaired=EXPECTED)],'固定4组输入与手写9bit输出表，通过真实DUT句柄写入、Timer/ReadOnly稳定采样；硬件计算由Icarus执行。',['固定组合DUT由显式错误控制输入切换数据路径；未验证全设计覆盖率、CDC或门级时序。'])


class AddItem(uvm_sequence_item):
    def __init__(self,name,a,b):
        super().__init__(name);self.a=a;self.b=b


class AddSequence(uvm_sequence):
    async def body(self):
        count=ConfigDB().get(None,'','vector_count')
        for index,(a,b) in enumerate(VECTORS[:count]):
            item=AddItem(f'item{index}',a,b)
            await self.start_item(item)
            await self.finish_item(item)


class AddDriver(uvm_driver):
    def build_phase(self):
        self.ap=uvm_analysis_port('observed',self)
    async def run_phase(self):
        dut=cocotb.top
        while True:
            item=await self.seq_item_port.get_next_item()
            dut.a.value=item.a;dut.b.value=item.b
            await Timer(2,unit='ns')
            await ReadOnly()
            result=int(dut.y.value)
            self.ap.write((item.a,item.b,result))
            await Timer(1,unit='ns')
            self.seq_item_port.item_done()


class AddScoreboard(uvm_subscriber):
    def build_phase(self):
        self.seen=[]
    def write(self,item):
        self.seen.append(item)
    def check_phase(self):
        self.outputs=[item[2] for item in self.seen]
        self.coverage_complete=[item[:2] for item in self.seen]==VECTORS
        self.functional_match=self.outputs==EXPECTED


class AddEnvironment(uvm_env):
    def build_phase(self):
        self.seqr=uvm_sequencer('seqr',self)
        self.driver=AddDriver('driver',self)
        self.scoreboard=AddScoreboard('scoreboard',self)
    def connect_phase(self):
        self.driver.seq_item_port.connect(self.seqr.seq_item_export)
        self.driver.ap.connect(self.scoreboard.analysis_export)


class FixedAddTest(uvm_test):
    COUNT=4
    def build_phase(self):
        ConfigDB().set(None,'','vector_count',type(self).COUNT)
        self.env=AddEnvironment('env',self)
    async def run_phase(self):
        self.raise_objection()
        await AddSequence('fixed').start(self.env.seqr)
        self.drop_objection()


@cocotb.test()
async def uvm_transactions(dut):
    await init(dut)
    async def run(drop=0,count=4):
        dut.drop_b.value=drop
        FixedAddTest.COUNT=count
        await uvm_root().run_test('FixedAddTest')
        sb=uvm_root().uvm_test_top.env.scoreboard
        return {'outputs':sb.outputs,'coverage_complete':sb.coverage_complete,'functional_match':sb.functional_match}
    normal=await run()
    assert normal=={'outputs':EXPECTED,'coverage_complete':True,'functional_match':True}
    wrong=await run(drop=1)
    assert wrong['outputs']==[0,1,255,255] and not wrong['functional_match']
    assert await run()==normal
    short=await run(count=3)
    assert short['outputs']==EXPECTED[:3] and not short['coverage_complete']
    assert await run()==normal
    save('03.06.02',[dict(id='repair_uvm_observed_function',status='passed',normal=normal,wrong=wrong,repaired=normal),dict(id='repair_uvm_sequence_coverage',status='passed',normal=normal,wrong=short,repaired=normal)],'实际pyuvm phases、sequence/sequencer-driver握手与analysis subscriber接收DUT结果；手写输入/输出和恰好四事务目标独立于driver；每次run_test创建新组件/清除singleton。',['仅小型事务级环境，monitor角色由driver的实际DUT结果analysis端口承担；未宣称独立总线monitor或寄存器模型全覆盖。'])


@cocotb.test()
async def axis_protocol(dut):
    await init(dut)
    clock=Clock(dut.clk,10,unit='ns')
    cocotb.start_soon(clock.start())
    source=AxiStreamSource(AxiStreamBus.from_prefix(dut,'s_axis'),dut.clk,dut.rst)
    sink=AxiStreamSink(AxiStreamBus.from_prefix(dut,'m_axis'),dut.clk,dut.rst)
    monitor=AxiStreamMonitor(AxiStreamBus.from_prefix(dut,'m_axis'),dut.clk,dut.rst)
    async def reset():
        dut.rst.value=1
        await Timer(30,unit='ns')
        dut.rst.value=0
        await Timer(30,unit='ns')
    await reset()
    payload=bytes([0,1,127,128,255])
    sink.set_pause_generator(itertools.cycle([1,1,0,0]))
    async def transfer(corrupt):
        dut.corrupt_data.value=corrupt
        await source.send(AxiStreamFrame(payload))
        frame=await with_timeout(sink.recv(),5000,'ns')
        observed=await with_timeout(monitor.recv(),5000,'ns')
        assert bytes(observed.tdata)==bytes(frame.tdata)
        return list(frame.tdata)
    normal=await transfer(0)
    assert normal==list(payload)
    wrong=await transfer(1)
    assert wrong==[1,0,126,129,254] and wrong!=normal
    assert await transfer(0)==normal
    dut.block_ready.value=1
    await source.send(AxiStreamFrame(payload))
    await Timer(200,unit='ns')
    assert not source.idle() and sink.empty() and monitor.empty()
    dut.block_ready.value=0
    resumed=await with_timeout(sink.recv(),5000,'ns')
    monitored=await with_timeout(monitor.recv(),5000,'ns')
    assert list(resumed.tdata)==list(monitored.tdata)==normal
    await with_timeout(source.wait(),5000,'ns')
    assert source.idle() and sink.empty() and monitor.empty()
    save('03.07.01',[dict(id='repair_axis_data_corruption',status='passed',expected=normal,wrong=wrong,repaired=normal,backpressure_pattern=[1,1,0,0]),dict(id='repair_axis_stalled_handshake',status='passed',blocked_ns=200,blocked_received_frames=0,source_was_pending=True,repaired=normal,source_idle_after=True)],'固定五字节和手写异或错误表；专用source/sink与独立stream monitor实际采样Icarus信号，背压期间无传输，解除阻塞后原帧完整。',['实际验证AXI-Stream valid/ready、tlast和数据完整；AXI memory-mapped突发/APB未在此固定例运行。','DUT为明确的组合stream桥，非商用DMA核；未验证时钟跨域或吞吐量综合指标。'])
