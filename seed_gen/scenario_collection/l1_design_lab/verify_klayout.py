"""Fixed KLayout geometry, DRC and physical connectivity verification."""
from importlib.metadata import version
import json
from pathlib import Path
import klayout.db as k

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/klayout'


def save_report(sid,tasks,boundaries):
    report=dict(scenario_id=sid,status='passed',versions={'klayout':version('klayout')},tasks=tasks,
                oracle_independence='固定矩形边长/面积、DBU换算和手写连接关系；错误与修复按同一几何/拓扑判据验收。',boundaries=boundaries)
    (OUT/f'{sid}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(sid,[(t['id'],t['status']) for t in tasks])


def geometry():
    layout=k.Layout()
    layout.dbu=0.001
    layer=layout.layer(1,0)
    unit=layout.create_cell('UNIT')
    unit.shapes(layer).insert(k.Box(0,0,1000,500))
    top=layout.create_cell('TOP')
    top.insert(k.CellInstArray(unit.cell_index(),k.Trans(2000,1000)))
    top.insert(k.CellInstArray(unit.cell_index(),k.Trans(4000,1000)))
    r=k.Region(top.begin_shapes_rec(layer))
    assert r.area()==1000000 and abs(r.area()*layout.dbu**2-1.0)<1e-14
    box=r.bbox()
    assert [box.left,box.bottom,box.right,box.top]==[2000,1000,5000,1500]
    for suffix in ['gds','oas']:
        path=OUT/f'hierarchy.{suffix}'
        layout.write(str(path))
        loaded=k.Layout()
        loaded.read(str(path))
        cells=list(loaded.each_cell())
        assert len(cells)==2
        recovered=k.Region(loaded.top_cell().begin_shapes_rec(loaded.layer(1,0)))
        assert recovered.area()==r.area() and recovered.bbox()==r.bbox() and loaded.dbu==0.001
    layout.dbu=0.01
    bad_area=r.area()*layout.dbu**2
    assert bad_area==100.0 and bad_area!=1.0
    layout.dbu=0.001
    assert r.area()*layout.dbu**2==1.0
    # Union/overlap computed from axis-aligned geometry known independently.
    first=k.Region(k.Box(0,0,1000,500))
    second=k.Region(k.Box(500,0,1500,500))
    union=first.or_(second).merged()
    overlap=first.and_(second)
    assert union.area()==750000 and overlap.area()==250000
    save_report('03.02.01',[
        dict(id='hierarchical_layout_roundtrip',status='passed',cells=2,instances=2,area_um2=1.0,bbox_dbu=[2000,1000,5000,1500],formats=['gds','oas']),
        dict(id='repair_layout_units_and_overlap',status='passed',wrong_area_um2=bad_area,repaired_area_um2=1.0,union_area_dbu2=union.area(),overlap_area_dbu2=overlap.area()),
    ],['仅固定整数矩形、层次变换、GDS/OASIS往返；不包含真实PDK完整工艺。','DBU错误只修改物理单位解释，按固定面积目标修复，不重新缩放oracle。'])


def region_wire(width=120,gap=150):
    r=k.Region()
    r.insert(k.Box(0,0,1000,width))
    r.insert(k.Box(0,width+gap,1000,2*width+gap))
    return r


def drc():
    normal=region_wire()
    assert normal.width_check(100).count()==0 and normal.space_check(100).count()==0
    narrow=region_wire(width=80)
    width_errors=narrow.width_check(100).count()
    assert width_errors>0
    fixed=region_wire(width=120)
    assert fixed.width_check(100).count()==0
    close=region_wire(gap=80)
    spacing_errors=close.space_check(100).count()
    assert spacing_errors>0
    fixed_gap=region_wire(gap=150)
    assert fixed_gap.space_check(100).count()==0
    save_report('03.02.02',[
        dict(id='repair_minimum_width',status='passed',min_width_nm=100,wrong_width_nm=80,wrong_marker_count=width_errors,repaired_width_nm=120,repaired_marker_count=0),
        dict(id='repair_minimum_spacing',status='passed',min_space_nm=100,wrong_gap_nm=80,wrong_marker_count=spacing_errors,repaired_gap_nm=150,repaired_marker_count=0),
    ],['仅1nm DBU下两条固定矩形线的100nm width/space规则，不是代工厂完整signoff DRC。','固定几何边长/间距是独立oracle；错误标记数只用于发现实际违规，不用规则豁免修复。'])


def extract(mode):
    metal1=k.Region()
    for left in [0,2000,4000]:
        metal1.insert(k.Box(left,0,left+1000,100))
    metal2=k.Region(k.Box(900,40,2100,60))
    via=k.Region(k.Box(900,40,1000,60))
    if mode!='open':
        via.insert(k.Box(2000,40,2100,60))
    if mode=='short':
        metal2.insert(k.Box(2050,40,4100,60))
        via.insert(k.Box(4000,40,4100,60))
    extractor=k.LayoutToNetlist('TOP',0.001)
    extractor.register(metal1,'metal1')
    extractor.register(metal2,'metal2')
    extractor.register(via,'via')
    extractor.connect(metal1)
    extractor.connect(metal2)
    extractor.connect(via)
    extractor.connect(metal1,via)
    extractor.connect(metal2,via)
    extractor.extract_netlist()
    circuit=extractor.netlist().circuit_by_name('TOP')
    nets=list(circuit.each_net())
    point_nets=[extractor.probe_net(metal1,k.Point(x,50)) for x in [500,2500,4500]]
    identities=[net.cluster_id for net in point_nets]
    relation=(identities[0]==identities[1],identities[0]==identities[2])
    extractor.write(str(OUT/f'{mode}.l2n'))
    return len(nets),relation,identities


def connectivity():
    normal=extract('normal')
    assert normal[:2]==(2,(True,False)),normal
    wrong_open=extract('open')
    assert wrong_open[0]==3 and wrong_open[1]==(False,False),wrong_open
    fixed_open=extract('normal')
    assert fixed_open[:2]==normal[:2]
    wrong_short=extract('short')
    assert wrong_short[0]==1 and wrong_short[1]==(True,True),wrong_short
    fixed_short=extract('normal')
    assert fixed_short[:2]==normal[:2]
    save_report('03.02.03',[
        dict(id='repair_missing_via_open',status='passed',expected_net_count=2,expected_AB_connected=True,expected_AC_connected=False,wrong_net_count=wrong_open[0],wrong_relation=wrong_open[1],repaired_net_count=fixed_open[0],repaired_relation=fixed_open[1]),
        dict(id='repair_extra_bridge_short',status='passed',wrong_net_count=wrong_short[0],wrong_relation=wrong_short[1],repaired_net_count=fixed_short[0],repaired_relation=fixed_short[1]),
    ],['真实LayoutToNetlist两层金属/via连通提取与probe_net，未提取MOS器件或运行完整晶体管级LVS。','手写目标拓扑A-B连通、C独立；删除via造成断路、额外跨层桥造成短路，修复后重提取按同一目标验收。'])


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('klayout')=='0.30.12'
    geometry()
    drc()
    connectivity()


if __name__=='__main__':
    main()
