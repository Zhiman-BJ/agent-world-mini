"""HDL21/VLSIR transistor structure and serialization, without analog solving."""
from copy import deepcopy
from importlib.metadata import version
import io
import json
from pathlib import Path
import hdl21 as h
import vlsir.circuit_pb2 as circuit
from vlsirtools import SpiceType
from vlsirtools.netlist import netlist

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/schematic'
EXPECTED={'ref':{'d':'iin','g':'iin','s':'vss','b':'vss'},'output':{'d':'out','g':'iin','s':'vss','b':'vss'}}


def mirror(gate='iin',output_w=2e-6,generic=False):
    m=h.Module(name='Mirror')
    m.iin=h.Port()
    m.out=h.Port()
    m.vss=h.Port()
    if generic:
        transistor=h.Nmos
    else:
        transistor=h.ExternalModule(name='NMOS',domain='fixed_fixture',port_list=deepcopy(h.MosPorts),paramtype=dict,spicetype=SpiceType.MOS)
    m.ref=transistor(w=1e-6,l=180e-9)(d=m.iin,g=m.iin,s=m.vss,b=m.vss)
    m.output=transistor(w=output_w,l=180e-9)(d=m.out,g=getattr(m,gate),s=m.vss,b=m.vss)
    return m


def connections(pkg):
    return {i.name:{c.portname:c.target.sig for c in i.connections} for i in pkg.modules[0].instances}


def write_netlist(pkg,label):
    stream=io.StringIO()
    netlist(pkg,stream,fmt='spice')
    text=stream.getvalue()
    (OUT/f'{label}.sp').write_text(text,encoding='utf-8')
    return text


def widths(pkg):
    result={}
    for instance in pkg.modules[0].instances:
        parameter=next(p.value for p in instance.parameters if p.name=='w')
        # dict parameters serialize floating-point values as the native proto number.
        result[instance.name]=parameter.double_value
    return result


def save(sid,tasks):
    data=dict(scenario_id=sid,status='passed',versions={p:version(p) for p in ['hdl21','vlsir','vlsirtools']},tasks=tasks,oracle_independence='手写两管NMOS电流镜端口连接表、输出/参考W比2、端口顺序和预期SPICE语句；验证仅结构/参数/Schema，不宣称实际电流比。',boundaries=['MOS外部模型名NMOS仅是显式fixture接口；未提供代工厂模型或运行SPICE，不能据此推断镜像精度、OTA增益或工艺可制造性。','采用可核对官方稳定源码tag6.0.0组合，最新PyPI7.0.0与Git标签不同；Python3.11用于匹配numpy1.x/pandas1.x依赖。'])
    (OUT/f'{sid}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(data,flush=True)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('hdl21')==version('vlsirtools')==version('vlsir')=='6.0.0'
    normal=h.to_proto(mirror())
    assert connections(normal)==EXPECTED
    text=write_netlist(normal,'normal')
    print(text,flush=True)
    assert len(normal.modules[0].instances)==2 and len(normal.modules[0].ports)==3
    normal_widths=widths(normal)
    assert normal_widths=={'ref':1e-6,'output':2e-6}
    wrong_gate=h.to_proto(mirror(gate='out'))
    assert connections(wrong_gate)['output']['g']=='out' and connections(wrong_gate)!=EXPECTED
    repaired_gate=h.to_proto(mirror(gate='iin'))
    assert connections(repaired_gate)==EXPECTED
    assert write_netlist(repaired_gate,'repaired_gate')==text
    wrong_ratio=h.to_proto(mirror(output_w=3e-6))
    assert abs(widths(wrong_ratio)['output']/widths(wrong_ratio)['ref']-3)<1e-12
    repaired_ratio=h.to_proto(mirror(output_w=2e-6))
    assert abs(widths(repaired_ratio)['output']/widths(repaired_ratio)['ref']-2)<1e-12
    assert write_netlist(repaired_ratio,'repaired_ratio')==text
    save('03.04.01',[
        dict(id='repair_current_mirror_gate_connection',status='passed',expected=EXPECTED,wrong=connections(wrong_gate),repaired=connections(repaired_gate),instances=2,ports=3),
        dict(id='repair_transistor_sizing_ratio',status='passed',expected_width_ratio=2,wrong_width_ratio=3,repaired_width_ratio=2,reference_w_m=1e-6,output_w_m=2e-6,length_m=180e-9,analog_current_not_simulated=True),
    ])
    try:
        write_netlist(h.to_proto(mirror(generic=True)),'generic_invalid')
    except RuntimeError as exc:
        generic_error=str(exc)
        assert 'physical' in generic_error and 'ExternalModule' in generic_error
    else:
        raise AssertionError('Unmapped physical primitive accepted')
    assert write_netlist(h.to_proto(mirror()),'external_repaired')==text
    wire=normal.SerializeToString(deterministic=True)
    (OUT/'mirror.pb').write_bytes(wire)
    decoded=circuit.Package()
    decoded.ParseFromString(wire)
    assert connections(decoded)==EXPECTED and write_netlist(decoded,'decoded')==text
    broken=circuit.Package()
    broken.CopyFrom(decoded)
    target=next(c for c in broken.modules[0].instances[1].connections if c.portname=='g')
    target.target.sig='missing_gate_net'
    try:
        write_netlist(broken,'broken_reference')
    except (KeyError,RuntimeError,ValueError) as exc:
        reference_error=f'{type(exc).__name__}: {exc}'
    else:
        raise AssertionError('Missing signal reference accepted')
    target.target.sig='iin'
    assert write_netlist(broken,'repaired_reference')==text
    save('03.04.02',[
        dict(id='repair_unmapped_physical_primitive',status='passed',error=generic_error,repaired_external_model='NMOS',repaired_connection_table=connections(decoded)),
        dict(id='repair_schema_signal_reference',status='passed',protobuf_roundtrip=True,error=reference_error,wrong_reference='missing_gate_net',repaired_reference='iin',repaired_netlist_identical=True),
    ])


if __name__=='__main__':
    main()
