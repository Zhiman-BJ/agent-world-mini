"""PyVerilog AST edits checked by fixed topology and real Icarus simulation."""
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
from pyverilog.vparser.parser import VerilogParser
from pyverilog.vparser import ast as v
from pyverilog.ast_code_generator.codegen import ASTCodeGenerator

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/ast'
IVL='/home/zjs32/.local/share/semiconductor-design-lab-20260917/iverilog/usr'
VECTORS=[(0,0),(1,2),(255,1),(255,255)]
EXPECTED=[0,3,256,510]


def linux_path(path):
    absolute=path.resolve().as_posix()
    assert absolute.startswith('D:/Desktop/agent-world-mini/seed_gen/scenario_collection/l1_design_lab/')
    return '/mnt/d/'+absolute.split(':',1)[1].lstrip('/')


def walk(node):
    yield node
    for child in node.children():
        yield from walk(child)


def parse(code):
    return VerilogParser(outputdir=str(OUT),debug=False).parse(code)


def code(width,expression):
    return f'module adder(input [7:0] a, input [7:0] b, output [{width-1}:0] y); assign y = {expression}; endmodule\n'


def simulate(tree,label):
    rendered=ASTCodeGenerator().visit(tree)
    design=OUT/f'{label}.v'
    design.write_text(rendered,encoding='utf-8')
    tb=OUT/'tb.v'
    tb.write_text('module tb; reg [7:0] a,b; wire [8:0] y; adder dut(a,b,y); initial begin\n'+''.join(f'a=8\'d{a};b=8\'d{b};#1;$display("RESULT %d",y);\n' for a,b in VECTORS)+'$finish;end endmodule\n',encoding='utf-8')
    binary=OUT/f'{label}.vvp'
    cmd=['wsl','-d','Ubuntu-24.04','--exec',IVL+'/bin/iverilog','-B',IVL+'/lib/x86_64-linux-gnu/ivl','-g2012','-s','tb','-o',linux_path(binary),linux_path(design),linux_path(tb)]
    compile_result=subprocess.run(cmd,check=True,capture_output=True,timeout=60)
    execution=subprocess.run(['wsl','-d','Ubuntu-24.04','--exec',IVL+'/bin/vvp',linux_path(binary)],check=True,capture_output=True,timeout=60)
    output=execution.stdout.decode('utf-8')
    observed=[int(line.split()[1]) for line in output.splitlines() if line.startswith('RESULT ')]
    assert len(observed)==len(EXPECTED),output
    (OUT/f'{label}.log').write_text(output,encoding='utf-8')
    return observed


def identifiers(expr):
    return sorted({n.name for n in walk(expr) if isinstance(n,v.Identifier)})


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('pyverilog')=='1.3.0'
    normal=parse(code(9,'a+b'))
    assert simulate(normal,'normal')==EXPECTED
    dependency=parse(code(9,'a'))
    assignment=next(n for n in walk(dependency) if isinstance(n,v.Assign))
    assert identifiers(assignment.right)==['a']
    wrong_dependency=simulate(dependency,'missing_b')
    assert wrong_dependency==[0,1,255,255] and wrong_dependency!=EXPECTED
    assignment.right=v.Rvalue(v.Plus(v.Identifier('a'),v.Identifier('b')))
    repaired_dependency=parse(ASTCodeGenerator().visit(dependency))
    fixed_assignment=next(n for n in walk(repaired_dependency) if isinstance(n,v.Assign))
    assert identifiers(fixed_assignment.right)==['a','b']
    assert simulate(repaired_dependency,'restored_b')==EXPECTED
    width=parse(code(8,'a+b'))
    output=next(n for n in walk(width) if isinstance(n,v.Output) and n.name=='y')
    assert int(output.width.msb.value)-int(output.width.lsb.value)+1==8
    wrong_width=simulate(width,'truncated_carry')
    assert wrong_width==[0,3,0,254] and wrong_width!=EXPECTED
    output.width=v.Width(v.IntConst('8'),v.IntConst('0'))
    repaired_width=parse(ASTCodeGenerator().visit(width))
    assert simulate(repaired_width,'restored_carry')==EXPECTED
    tasks=[dict(id='repair_missing_data_dependency',status='passed',expected_dependencies=['a','b'],wrong_dependencies=['a'],vectors=VECTORS,expected=EXPECTED,wrong=wrong_dependency,repaired=EXPECTED),dict(id='repair_output_width_truncation',status='passed',wrong_width=8,repaired_width=9,expected=EXPECTED,wrong=wrong_width,repaired=EXPECTED)]
    data=dict(scenario_id='03.08.02',status='passed',versions={'pyverilog':version('pyverilog'),'iverilog':'12.0 Ubuntu noble 12.0-2build2'},tasks=tasks,oracle_independence='固定4组8bit输入与手算9bit和[0,3,256,510]；AST依赖/位宽先检查，生成代码再交实际Icarus编译仿真，不用AST解释器冒充仿真。',boundaries=['PyVerilog直接解析无宏文本，未使用外部预处理包装入口；Icarus单独实际执行生成的代码。','仅组合加法器AST修复；非完整SystemVerilog支持、形式等价证明或大规模数据流分析。'],infrastructure={'iverilog_prefix':IVL,'install':'apt-get download iverilog; dpkg-deb -x private_path; explicit -B private ivl lib','system_packages_modified':False})
    (OUT/'03.08.02.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(data)


if __name__=='__main__':
    main()
