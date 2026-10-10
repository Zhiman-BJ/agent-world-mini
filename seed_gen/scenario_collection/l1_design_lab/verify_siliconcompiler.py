"""Real front-end flow execution and parameter propagation; no full GDS claim."""
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import tempfile
from siliconcompiler import Design,Project,Flowgraph
from siliconcompiler.tools.slang.elaborate import Elaborate

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/siliconcompiler'
PREFIX=Path('/home/zjs32/.local/share/semiconductor-design-lab-20260917/iverilog/usr')
EXPECTED=[0,3,256,510]


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='flow_',dir=OUT))
    source=root/'adder.v'
    source.write_text('module adder #(parameter WIDTH=9)(input [7:0] a,b,output [WIDTH-1:0] y);assign y={1\'b0,a}+{1\'b0,b};endmodule\nmodule unused(input a,output b);assign b=a;endmodule\n')
    tb=root/'tb.v'
    tb.write_text('module tb;reg [7:0] a,b;wire [8:0] y;adder dut(a,b,y);initial begin a=0;b=0;#1;$display("RESULT %d",y);a=1;b=2;#1;$display("RESULT %d",y);a=255;b=1;#1;$display("RESULT %d",y);a=255;b=255;#1;$display("RESULT %d",y);$finish;end endmodule\n')
    def run(label,width=9,top='adder'):
        work=root/label;work.mkdir()
        design=Design('adder')
        design.set_dataroot('fixture',str(root))
        with design.active_dataroot('fixture'),design.active_fileset('rtl'):
            design.set_topmodule(top)
            design.add_file('adder.v')
            design.set_param('WIDTH',str(width))
        assert design.check_filepaths()
        project=Project(design)
        project.option.set_builddir(str(work/'build'))
        project.add_fileset('rtl')
        flow=Flowgraph('frontend')
        flow.node('elaborate',Elaborate())
        project.set_flow(flow)
        previous=os.getcwd();os.chdir(work)
        try:
            succeeded=project.run()
            history=project.history('job0')
            errors=history.get('metric','errors',step='elaborate',index='0')
            result=project.find_result('v',step='elaborate')
        finally:
            os.chdir(previous)
        if not succeeded:return dict(succeeded=False,errors=errors,result=result)
        result_path=Path(result)
        generated=result_path.read_text()
        assert 'module unused' not in generated
        binary=work/'sim.vvp'
        subprocess.run([str(PREFIX/'bin/iverilog'),'-B',str(PREFIX/'lib/x86_64-linux-gnu/ivl'),'-g2012','-s','tb','-o',str(binary),str(result_path),str(tb)],check=True,capture_output=True)
        process=subprocess.run([str(PREFIX/'bin/vvp'),str(binary)],check=True,capture_output=True,text=True)
        (work/'simulation.log').write_text(process.stdout)
        observed=[int(line.split()[1]) for line in process.stdout.splitlines() if line.startswith('RESULT ')]
        return dict(succeeded=True,errors=errors,result=str(result_path),observed=observed,unused_module_pruned=True)
    normal=run('normal')
    assert normal['observed']==EXPECTED and normal['errors']==0
    try:
        wrong_top=run('missing_top',top='missing_top')
    except Exception as exc:
        wrong_top=dict(succeeded=False,error=f'{type(exc).__name__}: {exc}')
    assert not wrong_top['succeeded']
    fixed_top=run('top_repaired')
    assert fixed_top['observed']==EXPECTED
    wrong_width=run('width8',width=8)
    assert wrong_width['observed']==[0,3,0,254]
    fixed_width=run('width9',width=9)
    assert fixed_width['observed']==EXPECTED
    result=dict(scenario_id='03.09.01',status='passed',versions={'siliconcompiler':version('siliconcompiler'),'pyslang':version('pyslang')},tasks=[dict(id='repair_flow_topmodule',status='passed',normal=normal,wrong=wrong_top,repaired=fixed_top),dict(id='repair_flow_parameter_propagation',status='passed',wrong=wrong_width,repaired=fixed_width)],oracle_independence='四组手写9bit和与可达模块名单固定；SiliconCompiler实际调度Slang原生前端输出Verilog，随后独立Icarus执行输出而非只检查配置文本。',boundaries=['已运行RTL到GDS流程的前端配置/展开局部链；未运行Yosys/OpenROAD/CTS/布线/GDS生成，不能称整条tapeout已通过。','未加载真实代工厂PDK或签核约束；候选工具保留完整主流程框架便于后续有后端时扩展。','Slang忽略unknown模块的一般选项不用于判定缺失top；错误top实际调度失败。'])
    (OUT/'03.09.01.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(result)


if __name__=='__main__':
    main()
