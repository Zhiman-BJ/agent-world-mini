"""True Amaranth event simulation with fixed cycle and FIFO transaction oracles."""
from importlib.metadata import version
import json
from pathlib import Path
from amaranth import Elaboratable,Module,Signal
from amaranth.sim import Simulator
from amaranth.lib.fifo import SyncFIFO

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/rtl'
ENABLES=[1,1,0,1,1,1,1,1,1]
COUNTS=[1,2,2,3,4,5,6,7,0]
WORDS=[17,34,51,68]


class Counter(Elaboratable):
    def __init__(self,increment):
        self.en=Signal()
        self.count=Signal(3)
        self.increment=increment

    def elaborate(self,platform):
        m=Module()
        with m.If(self.en):
            m.d.sync += self.count.eq(self.count+self.increment)
        return m


def counter(increment,label):
    dut=Counter(increment)
    observed=[]
    async def bench(ctx):
        for en in ENABLES:
            ctx.set(dut.en,en)
            await ctx.tick()
            observed.append(ctx.get(dut.count))
    sim=Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    with sim.write_vcd(str(OUT/f'counter_{label}.vcd')):
        sim.run()
    return observed


def fifo(depth,label):
    dut=SyncFIFO(width=8,depth=depth)
    observed=[]
    accepted=[]
    levels=[]
    full=None
    empty=None
    async def bench(ctx):
        nonlocal full,empty
        ctx.set(dut.r_en,0)
        for word in WORDS:
            accepted.append(bool(ctx.get(dut.w_rdy)))
            ctx.set(dut.w_en,1)
            ctx.set(dut.w_data,word)
            await ctx.tick()
            levels.append(ctx.get(dut.level))
        full=not bool(ctx.get(dut.w_rdy))
        # An attempted fifth word must not overwrite the four queued words.
        ctx.set(dut.w_data,99)
        await ctx.tick()
        ctx.set(dut.w_en,0)
        ctx.set(dut.r_en,1)
        for _ in range(4):
            if ctx.get(dut.r_rdy):
                observed.append(ctx.get(dut.r_data))
            await ctx.tick()
        empty=not bool(ctx.get(dut.r_rdy))
        assert ctx.get(dut.level)==0
    sim=Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    with sim.write_vcd(str(OUT/f'fifo_{label}.vcd')):
        sim.run()
    return dict(observed=observed,accepted=accepted,levels=levels,full=full,empty=empty)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('amaranth')=='0.5.10'
    normal=counter(1,'normal')
    wrong=counter(2,'wrong')
    repaired=counter(1,'repaired')
    assert normal==COUNTS and repaired==COUNTS
    assert wrong!=COUNTS and wrong[0]==2
    queue=fifo(4,'normal')
    small=fifo(2,'wrong')
    restored=fifo(4,'repaired')
    assert queue['observed']==WORDS and restored==queue
    assert queue['accepted']==[True]*4 and queue['levels']==[1,2,3,4]
    assert queue['full'] and queue['empty']
    assert small['observed']==[17,34] and small['accepted']==[True,True,False,False]
    tasks=[dict(id='repair_counter_increment',status='passed',enable_sequence=ENABLES,expected=COUNTS,normal=normal,wrong=wrong,repaired=repaired,first_bad_cycle=1),dict(id='repair_fifo_depth_and_verify_backpressure',status='passed',expected=WORDS,normal=queue,wrong_depth=2,wrong=small,repaired_depth=4,repaired=restored)]
    data=dict(scenario_id='03.05.01',status='passed',versions={'amaranth':version('amaranth')},tasks=tasks,oracle_independence='预先手写9周期模8计数表、4个固定数据字、FIFO满空/接收握手与深度4约束；不从实现结果派生目标。',boundaries=['实际Amaranth事件仿真，不是Python替代逻辑模型；未执行FPGA布局布线、门级延时或外部HDL仿真器。','VCD由Simulator.write_vcd真实输出，未运行GTKWave GUI。'])
    (OUT/'03.05.01.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(data)


if __name__=='__main__':
    main()
