"""FuseSoC dependencies -> Edalize Sim flow -> actual Icarus execution."""
from importlib.metadata import version
import json
import os
from pathlib import Path
import tempfile
from fusesoc.config import Config
from fusesoc.coremanager import CoreManager,DependencyError
from fusesoc.edalizer import Edalizer
from fusesoc.librarymanager import Library
from fusesoc.vlnv import Vlnv
from edalize.flows.sim import Sim

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/fusesoc'
PREFIX=Path('/home/zjs32/.local/share/semiconductor-design-lab-20260917')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    os.environ['PATH']=str(PREFIX/'iverilog/usr/bin')+os.pathsep+str(PREFIX/'make/usr/bin')+os.pathsep+os.environ['PATH']
    root=Path(tempfile.mkdtemp(prefix='ip_fixture_',dir=OUT))
    cores=root/'cores';cores.mkdir()
    (root/'fusesoc.conf').write_text('[main]\ncache_root = '+str(root/'cache')+'\n')
    (cores/'adder.v').write_text('module adder(input [7:0] a,b,output [8:0] y);assign y={1\'b0,a}+{1\'b0,b};endmodule\n')
    (cores/'tb.v').write_text('module tb;parameter WIDTH=9;reg [7:0] a,b;wire [WIDTH-1:0] y;integer fd;adder dut(a,b,y);initial begin fd=$fopen("observed.txt","w");a=0;b=0;#1;$fdisplay(fd,"%d",y);a=1;b=2;#1;$fdisplay(fd,"%d",y);a=255;b=1;#1;$fdisplay(fd,"%d",y);a=255;b=255;#1;$fdisplay(fd,"%d",y);$fclose(fd);$finish;end endmodule\n')
    (cores/'adder.core').write_text('CAPI=2:\nname: seed:lib:adder:1.0\nfilesets:\n  rtl:\n    files: [adder.v]\n    file_type: verilogSource\ntargets:\n  default:\n    filesets: [rtl]\n')
    def topcore(dependency):
        (cores/'tb.core').write_text('CAPI=2:\nname: seed:test:tb:1.0\nfilesets:\n  sim:\n    files: [tb.v]\n    file_type: verilogSource\n    depend: ['+dependency+']\nparameters:\n  WIDTH:\n    datatype: int\n    paramtype: vlogparam\n    default: 9\ntargets:\n  sim:\n    flow: sim\n    flow_options:\n      tool: icarus\n    filesets: [sim]\n    parameters: [WIDTH]\n    toplevel: tb\n')
    def resolve(label,dependency='seed:lib:adder:1.0'):
        topcore(dependency)
        manager=CoreManager(Config(str(root/'fusesoc.conf')))
        manager.add_library(Library('fixed',str(cores)),[])
        top=manager.get_core(Vlnv('seed:test:tb:1.0'))
        work=root/label;work.mkdir()
        edalizer=Edalizer(toplevel=top.name,flags={'target':'sim'},core_manager=manager,work_root=str(work),export_root=str(work/'src'))
        edam=edalizer.run()
        edalizer.export()
        return edam,work
    expected=[0,3,256,510]
    def execute(label,width):
        edam,work=resolve(label)
        assert list(edam['dependencies'])==['seed:lib:adder:1.0','seed:test:tb:1.0']
        edam['parameters']['WIDTH']['default']=width
        edam['flow_options']['iverilog_options']=['-B',str(PREFIX/'iverilog/usr/lib/x86_64-linux-gnu/ivl'),'-g2012']
        (work/'edam.json').write_text(json.dumps(edam,indent=2))
        backend=Sim(edam=edam,work_root=str(work))
        backend.configure();backend.build();backend.run()
        return [int(v) for v in (work/'observed.txt').read_text().splitlines()]
    normal=execute('normal',9)
    assert normal==expected
    try:
        resolve('missing','seed:lib:adder:2.0')
    except DependencyError as exc:
        error=f'{type(exc).__name__}: {exc}; {exc.msg}'
        assert 'adder' in exc.msg
    else:
        raise AssertionError('Unavailable dependency version accepted')
    assert execute('dependency_repaired',9)==expected
    wrong=execute('truncated',8)
    assert wrong==[0,3,0,254] and wrong!=expected
    assert execute('width_repaired',9)==expected
    data=dict(scenario_id='03.10.01',status='passed',versions={p:version(p) for p in ['fusesoc','edalize']},tasks=[dict(id='repair_ip_dependency_version',status='passed',wrong='seed:lib:adder:2.0',error=error,repaired='seed:lib:adder:1.0',actual_outputs=normal),dict(id='repair_backend_width_parameter',status='passed',wrong_width=8,wrong=wrong,repaired_width=9,repaired=expected)],oracle_independence='固定两个本地CAPI2 core、依赖顺序和四组手写9bit加法结果；FuseSoC实际解析与解依赖，Edalize flow API真正调用make/Icarus/vvp产生输出文件。',boundaries=['仅本地IP组合与Icarus仿真，不宣称综合或RTL到GDS完成；使用新Sim flow API，未依赖已弃用的旧Icarus tool API。','无外部library fetch/update；工作、cache、source export均在新建fixture目录。'],fixture_root=str(root))
    (OUT/'03.10.01.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(data)


if __name__=='__main__':
    main()
