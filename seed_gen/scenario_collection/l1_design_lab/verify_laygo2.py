"""Real gridded placement, template pins and YAML roundtrip; no foundry DRC."""
from importlib.metadata import version
import json
from pathlib import Path
import sys
import os
import numpy as np
SOURCE=Path(__file__).resolve().parents[3]/'seed_pypi_raw/l1_design_lab/laygo2'
sys.path.insert(0,str(SOURCE/'laygo2/examples'))
previous=os.getcwd()
os.chdir(SOURCE)
from laygo2.object.grid import OneDimGrid,PlacementGrid
from laygo2.object.template import NativeInstanceTemplate
from laygo2.object.physical import Pin,Rect
from laygo2.object.database import Design,Library
from laygo2.interface.yaml import export_template,import_template
from laygo2.interface.skill import export as export_skill
os.chdir(previous)

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/laygo2'


def build(pitch=20,pin_net='GATE'):
    grid=PlacementGrid(name='placement',vgrid=OneDimGrid(name='x',scope=[0,pitch],elements=[0]),hgrid=OneDimGrid(name='y',scope=[0,100],elements=[0]))
    pins={'G':Pin(xy=[[0,40],[20,60]],layer=['M1','pin'],netname='G')}
    template=NativeInstanceTemplate(libname='fixture_devices',cellname='unit',bbox=[[0,0],[100,100]],pins=pins)
    first=template.generate(name='REF',netmap={'G':'GATE'})
    second=template.generate(name='OUT',netmap={'G':pin_net})
    grid.place(first,[0,0]);grid.place(second,[6,0])
    design=Design(name='pair',libname='fixture_layout')
    design.append(first);design.append(second)
    design.append(Rect(xy=[[0,45],[140,55]],layer=['M1','drawing'],netname='GATE',name='gate_metal'))
    design.append(Pin(xy=[[0,45],[10,55]],layer=['M1','pin'],netname='GATE',name='GATE'))
    return design,first,second,grid


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('laygo2')=='0.5.5'
    normal,a,b,g=build()
    assert a.bbox.tolist()==[[0,0],[100,100]]
    assert b.bbox.tolist()==[[120,0],[220,100]]
    assert b.pins['G'].xy.tolist()==[[120,40],[140,60]]
    assert b.pins['G'].netname=='GATE'
    bad,a_bad,b_bad,g_bad=build(pitch=15)
    assert b_bad.xy.tolist()==[90,0]
    overlap=max(0,min(a_bad.bbox[1,0],b_bad.bbox[1,0])-max(a_bad.bbox[0,0],b_bad.bbox[0,0]))
    assert overlap==10
    repaired,a_fix,b_fix,g_fix=build(pitch=20)
    assert b_fix.bbox.tolist()==[[120,0],[220,100]]
    assert b_fix.bbox[0,0]-a_fix.bbox[1,0]==20
    misnet,_,wrong_pin,_=build(pin_net='WRONG')
    assert wrong_pin.pins['G'].netname!='GATE'
    restored,_,right_pin,_=build()
    assert right_pin.pins['G'].netname=='GATE'
    exported=restored.export_to_template()
    yaml=OUT/'pair.yaml'
    export_template(exported,str(yaml),mode='overwrite')
    loaded=import_template(str(yaml))
    recovered=loaded['pair'].generate(name='RECOVERED')
    assert recovered.bbox.tolist()==[[0,0],[220,100]]
    assert recovered.pins['GATE'].netname=='GATE'
    library=Library(name='fixture_layout');library.append(restored)
    skill=export_skill(library,filename=str(OUT/'pair.il'),scale=1e-3)
    assert 'fixture_layout' in skill and 'REF' in skill and 'OUT' in skill
    data=dict(scenario_id='03.11.03',status='passed',versions={'laygo2':version('laygo2'),'numpy':version('numpy')},tasks=[dict(id='repair_grid_pitch_overlap',status='passed',wrong_pitch_nm=15,wrong_second_x_nm=90,wrong_overlap_nm=10,repaired_pitch_nm=20,repaired_second_x_nm=120,repaired_gap_nm=20),dict(id='repair_pin_net_mapping_and_template_roundtrip',status='passed',wrong_net='WRONG',repaired_net='GATE',template_bbox_nm=[[0,0],[220,100]],recovered_gate_net='GATE',yaml_roundtrip=True)],oracle_independence='固定100×100nm单元，两实例目标x=0/120nm、间隔20nm和GATE标签；手写矩形区间算重叠，不调用待测grid反推expected。真实模板生成/placement/实例pin变换/YAML重载。',boundaries=['unit是明确的抽象版图模板，不是实际MOS工艺器件；间隔目标是fixture规则，不是代工厂DRC。','生成了真实SKILL文本但未在Cadence执行；没有声称SKILL导出即物理数据库建成。','网名映射验证是pin标签/几何状态，不是电气提取LVS。','最新官方Release stable-230804版本0.5.5，PyPI0.6.9差异已记录。'])
    (OUT/'03.11.03.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(data)


if __name__=='__main__':
    main()
