"""Actual parameterized hierarchy/routing with fixed coordinates and KLayout oracle."""
from importlib.metadata import version
import json
from pathlib import Path
import gdsfactory as gf
import klayout.db as k

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/gdsfactory'


def report(sid,tasks):
    data=dict(scenario_id=sid,status='passed',versions={p:version(p) for p in ['gdsfactory','kfactory','klayout']},tasks=tasks,
              oracle_independence='固定矩形length×width、端点坐标和拓扑数量；GDS重读由原生KLayout核对，不从被测输出自适应目标。',
              boundaries=['真实generic PDK几何生成和路由，不是代工厂PDK signoff或光学/电气传播仿真。','gdsfactory9.51.0要求kfactory>=3.0.4,<3.1.dev0，固定实际兼容3.0.4；最新3.2.1未混用。'])
    (OUT/f'{sid}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(sid,tasks,flush=True)


def chain(length=20,width=0.5):
    c=gf.Component()
    first=c.add_ref(gf.components.straight(length=10,width=0.5),name='first')
    second=c.add_ref(gf.components.straight(length=length,width=width),name='second')
    second.connect('o1',first.ports['o2'])
    c.add_port('o1',port=first.ports['o1'])
    c.add_port('o2',port=second.ports['o2'])
    return c


def pcell():
    unit=gf.components.straight(length=10,width=0.5)
    cached=gf.components.straight(length=10,width=0.5)
    changed=gf.components.straight(length=11,width=0.5)
    assert unit.cell_index()==cached.cell_index() and unit.cell_index()!=changed.cell_index()
    normal=chain()
    assert normal.area((1,0))==15.0
    assert tuple(normal.ports['o2'].center)==(30.0,0.0)
    netlist=normal.get_netlist()
    assert len(netlist['instances'])==2 and len(netlist['nets'])==1 and len(netlist['ports'])==2
    gds=normal.write_gds(OUT/'chain.gds',with_metadata=True)
    layout=k.Layout()
    layout.read(str(gds))
    region=k.Region(layout.top_cell().begin_shapes_rec(layout.layer(1,0)))
    assert region.area()*layout.dbu**2==15.0
    assert [region.bbox().left,region.bbox().bottom,region.bbox().right,region.bbox().top]==[0,-250,30000,250]
    bad=chain(length=25)
    assert tuple(bad.ports['o2'].center)==(35.0,0.0) and bad.area((1,0))==17.5
    fixed=chain(length=20)
    assert tuple(fixed.ports['o2'].center)==(30.0,0.0) and fixed.area((1,0))==15.0
    try:
        chain(width=1.0)
    except Exception as exc:
        error_type=type(exc).__name__
        error_text=str(exc)
        assert 'width' in error_text.lower(),error_text
    else:
        raise AssertionError('Width mismatch not rejected')
    repaired=chain(width=0.5)
    assert len(repaired.get_netlist()['nets'])==1 and repaired.area((1,0))==15.0
    report('03.01.01',[
        dict(id='parameterized_hierarchy_and_dimension_repair',status='passed',cache_same_parameters_same_cell=True,parameter_change_new_cell=True,normal_instances=2,normal_internal_nets=1,normal_ports=2,normal_area_um2=15,normal_end_um=[30,0],wrong_area_um2=17.5,wrong_end_um=[35,0],repaired_area_um2=15,gds_bbox_dbu=[0,-250,30000,250]),
        dict(id='repair_port_width_mismatch',status='passed',wrong_width_um=1.0,error_type=error_type,error=error_text,repaired_width_um=0.5,repaired_net_count=1),
    ])


def single(destination_x=110):
    c=gf.Component()
    first=c.add_ref(gf.components.straight(length=10,width=0.5))
    second=c.add_ref(gf.components.straight(length=10,width=0.5))
    second.dmove((destination_x,0))
    route=gf.routing.route_single(c,first.ports['o2'],second.ports['o1'],cross_section='strip',auto_taper=False,on_collision='error',on_placer_error='error')
    return c,route


def bundle(target_count=3):
    c=gf.Component()
    left=[gf.Port(name=f'L{i}',center=(0,y),width=0.5,orientation=0,layer=gf.get_layer((1,0))) for i,y in enumerate([0,10,20])]
    right=[gf.Port(name=f'R{i}',center=(100,y),width=0.5,orientation=180,layer=gf.get_layer((1,0))) for i,y in enumerate([0,10,20][:target_count])]
    routes=gf.routing.route_bundle(c,left,right,cross_section='strip',auto_taper=False,sort_ports=True,on_collision='error',on_placer_error='error')
    return c,routes


def routing():
    c,route=single()
    assert abs(route.length*c.kcl.dbu-100.0)<1e-12
    assert c.area((1,0))==60.0
    wrong,wrong_route=single(destination_x=100)
    assert wrong_route.length*wrong.kcl.dbu==90 and wrong.area((1,0))==55
    fixed,fixed_route=single(destination_x=110)
    assert fixed_route.length*fixed.kcl.dbu==100 and fixed.area((1,0))==60
    bc,routes=bundle()
    lengths=[r.length*bc.kcl.dbu for r in routes]
    assert lengths==[100.0]*3 and bc.area((1,0))==150.0
    assert bc.get_region((1,0)).merged().count()==3
    try:
        bundle(target_count=2)
    except Exception as exc:
        count_error=f'{type(exc).__name__}: {exc}'
    else:
        raise AssertionError('Unequal port group count not rejected')
    repaired,repaired_routes=bundle(3)
    assert len(repaired_routes)==3 and repaired.area((1,0))==150.0
    path=repaired.write_gds(OUT/'bundle.gds')
    layout=k.Layout()
    layout.read(str(path))
    region=k.Region(layout.top_cell().begin_shapes_rec(layout.layer(1,0)))
    assert region.merged().count()==3 and region.area()*layout.dbu**2==150.0
    report('03.01.02',[
        dict(id='repair_single_route_placement',status='passed',normal_route_length_um=100,normal_total_area_um2=60,wrong_destination_x_um=100,wrong_route_length_um=90,wrong_total_area_um2=55,repaired_destination_x_um=110,repaired_route_length_um=100,repaired_area_um2=60),
        dict(id='repair_bundle_port_count',status='passed',route_count=3,lengths_um=lengths,area_um2=150,error=count_error,repaired_route_count=3,gds_disconnected_paths=3,gds_area_um2=150),
    ])


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('gdsfactory')=='9.51.0' and version('kfactory')=='3.0.4'
    gf.gpdk.PDK.activate()
    pcell()
    routing()


if __name__=='__main__':
    main()
