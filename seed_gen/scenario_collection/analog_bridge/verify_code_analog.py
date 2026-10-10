"""Code -> geometry/device extraction -> SPICE -> independent circuit oracle."""
import hashlib
from importlib.metadata import version
import io
import json
import math
from pathlib import Path
import subprocess

import hdl21 as h
import hdl21.sim as hs
import klayout.db as k
import vlsirtools
import vlsirtools.spice as vsp
import vlsirtools.spice.ngspice as ng

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/code_analog'
BACKEND='/home/zjs32/.local/share/semiconductor-analog-bridge-20260917/ngspice-deb/root'
BACKEND_CMD=['wsl.exe','-e','env','LD_LIBRARY_PATH='+BACKEND+'/usr/lib/x86_64-linux-gnu',BACKEND+'/usr/bin/ngspice']
# Explicit subprocess command supported by the fixed VLSIRtools backend setting.
ng.NGSPICE_EXECUTABLE=' '.join(BACKEND_CMD)
GOLDEN_EDGES={('OUT','VDD'):1000.,('OUT','VSS'):2000.}

def generate_and_extract(length2_um, label):
    layout=k.Layout();layout.dbu=.001;top=layout.create_cell('DIVIDER')
    li={name:layout.layer(i,0) for i,name in enumerate(['R','C','M'],1)}
    for y,length in [(0,10000),(5000,int(round(length2_um*1000)))]:
        top.shapes(li['R']).insert(k.Box(0,y,length,y+1000))
        top.shapes(li['C']).insert(k.Box(-1000,y,0,y+1000))
        top.shapes(li['C']).insert(k.Box(length,y,length+1000,y+1000))
    top.shapes(li['M']).insert(k.Path([k.Point(10500,500),k.Point(22000,500),k.Point(22000,4000),k.Point(-500,4000),k.Point(-500,5500)],200))
    path=OUT/(label+'.gds');layout.write(str(path))
    recovered=k.Layout();recovered.read(str(path));assert recovered.dbu==.001
    cell=recovered.top_cell()
    r,c,m=[k.Region(cell.begin_shapes_rec(recovered.layer(i,0))) for i in [1,2,3]]
    ext=k.LayoutToNetlist('DIVIDER',recovered.dbu)
    for region,name in [(r,'resistor'),(c,'contacts'),(m,'metal')]:ext.register(region,name)
    ext.extract_devices(k.DeviceExtractorResistor('R',100),{'R':r,'C':c,'tA':c,'tB':c})
    ext.connect(c);ext.connect(m);ext.connect(c,m);ext.extract_netlist()
    circuit=ext.netlist().circuit_by_name('DIVIDER')
    points={'VDD':(-500,500),'OUT':(10500,500),'VSS':(int(length2_um*1000)+500,5500)}
    for name,xy in points.items():ext.probe_net(c,k.Point(*xy)).name=name
    # The bottom resistor left terminal must be electrically identical to OUT.
    assert ext.probe_net(c,k.Point(-500,5500)).name=='OUT'
    edges={};dimensions=[]
    for device in circuit.each_device():
        terminals=tuple(sorted(device.net_for_terminal(i).name for i in [0,1]))
        assert terminals not in edges
        edges[terminals]=device.parameter('R')
        dimensions.append({p:device.parameter(p) for p in ['R','L','W','A','P']})
    assert len(list(circuit.each_net()))==3 and len(edges)==2
    assert set(edges)==set(GOLDEN_EDGES)
    ext.write(str(OUT/(label+'.l2n')))
    (OUT/(label+'.extracted.txt')).write_text(ext.netlist().to_s(),encoding='utf-8')
    return edges,dimensions

def make_tb(edges,name):
    tb=h.Module(name=name)
    tb.add(h.Port(name='VSS'))
    for net in ['VDD','OUT']:tb.add(h.Signal(name=net))
    tb.add(h.Vdc(dc=1)(p=tb.VDD,n=tb.VSS),name='supply')
    for index,((a,b),value) in enumerate(sorted(edges.items()),1):
        tb.add(h.Resistor(r=value)(p=tb.get(a),n=tb.get(b)),name=f'r{index}')
    return tb

def simulate(edges,label):
    tb=make_tb(edges,'Divider_'+label)
    proto=h.to_proto(tb);dest=io.StringIO();vlsirtools.netlist(proto,dest=dest,fmt='ngspice')
    (OUT/(label+'.schematic.sp')).write_text(dest.getvalue(),encoding='utf-8')
    sim=hs.Sim(tb=tb,attrs=[hs.Op(name='op')],name='DividerDC')
    value=sim.run(vsp.SimOptions(simulator=vsp.SupportedSimulators.NGSPICE,fmt=vsp.ResultFormat.SIM_DATA,rundir=OUT/label))
    data=value.get('op').data
    return {'vout':float(data['v(xtop.out)']),'supply_current':float(data['i(v.xtop.vsupply)'])}

def assert_golden(value):
    assert math.isclose(value['vout'],2/3,rel_tol=0,abs_tol=1e-12)
    assert math.isclose(value['supply_current'],-1/3000,rel_tol=0,abs_tol=1e-12)

def trial(index):
    reference=simulate(GOLDEN_EDGES,f'reference_{index}');assert_golden(reference)
    extracted,dims=generate_and_extract(20,f'normal_{index}')
    assert extracted==GOLDEN_EDGES
    assert sorted(d['L'] for d in dims)==[10,20] and all(d['W']==1 for d in dims)
    normal=simulate(extracted,f'extracted_{index}');assert_golden(normal);assert normal==reference
    wrong,_=generate_and_extract(15,f'wrong_{index}')
    assert wrong[('OUT','VSS')]==1500 and wrong!=GOLDEN_EDGES
    bad=simulate(wrong,f'bad_{index}');assert math.isclose(bad['vout'],.6,abs_tol=1e-12)
    assert abs(bad['vout']-2/3)>.06
    repaired,_=generate_and_extract(20,f'repaired_{index}');assert repaired==GOLDEN_EDGES
    fixed=simulate(repaired,f'fixed_{index}');assert_golden(fixed)
    return dict(reference=reference,normal=normal,wrong=bad,repaired=fixed,extracted_dimensions=dims)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    info=subprocess.run(BACKEND_CMD+['-v'],capture_output=True,text=True,errors='replace',check=True).stdout
    assert 'ngspice-42' in info
    trials=[trial(i) for i in [1,2]];assert trials[0]==trials[1]
    result=dict(scenario_id='03.11.01',status='passed',versions={p:version(p) for p in ['hdl21','vlsir','vlsirtools','klayout','numpy']},
      backend={'name':'ngspice','version':'42','distribution':'Ubuntu noble 42+ds-3build1','command':BACKEND_CMD,'version_output':info},
      tasks=[dict(id='generate_extract_simulate_analog_divider',status='passed',values=trials[0]['normal'],extracted_dimensions=trials[0]['extracted_dimensions']),
        dict(id='repair_layout_schematic_parameter_drift',status='passed',wrong=trials[0]['wrong'],repaired=trials[0]['repaired'])],
      repeats=2,repeat_equal=True,oracle='Independent fixed1V circuit R1=1000Ohm/R2=2000Ohm gives Vout2/3V and Isupply=-1/3000A. Geometryoracle uses100Ohm/square,L10/20um,W1um; actualKLayoutdeviceextraction and ngspiceoutputs must satisfyboth.',
      boundaries=['Manufactured sheet-resistor process; code-first analog subchain, not a fabricated OTA or commercial-PDK signoff.','Actual GDS reload, resistor device/net extraction and independent ngspiceDC run; no parasitic/temperature/noise validation.','ngspice42 is a pinned Ubuntu binary unpacked in a user-owned directory, not installed globally; Python packages follow pinned releases.'],
      executed_script=Path(__file__).as_posix(),script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (OUT/'03.11.01.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(result['tasks']);print('Two independent repetitions matched.')

if __name__=='__main__':main()
