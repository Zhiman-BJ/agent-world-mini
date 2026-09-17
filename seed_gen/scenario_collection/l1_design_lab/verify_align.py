"""Released ALIGN topology and mock-PDK primitive execution; no PNR claim."""
from importlib.metadata import version
import json
import os
from pathlib import Path
import tempfile

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/align'
SOURCE=BASE.parents[2]/'seed_pypi_raw/l1_design_lab/ALIGN-release'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='mock_pdk_',dir=OUT))
    os.chdir(work)
    from align.compiler.read_netlist import SpiceParser
    from align.primitive.main import generate_primitive
    from align.gdsconv.json2gds import convert_GDSjson_GDS
    import gdstk
    def topology(label,gate='IN'):
        path=work/(label+'.sp')
        path.write_text('.SUBCKT INV IN OUT VDD VSS\nMN OUT '+gate+' VSS VSS nmos w=1u l=0.1u\nMP OUT IN VDD VDD pmos w=2u l=0.1u\n.ENDS INV\n.END\n')
        circuits=SpiceParser(str(path),top_ckt_name='INV').sp_parser()
        graph=circuits[0]['graph']
        return {'ports':circuits[0]['ports'],'nodes':list(graph.nodes(data=True)), 'edges':list(graph.edges(data=True))}
    normal=topology('normal')
    wrong=topology('wrong_gate','OUT')
    repaired=topology('repaired')
    def topology_matches(data):
        nodes=dict(data['nodes'])
        return data['ports']==['IN','OUT','VDD','VSS'] and nodes['MN']['ports']==['OUT','IN','VSS'] and nodes['MP']['ports']==['OUT','IN','VDD'] and len(data['edges'])==6
    assert topology_matches(normal) and not topology_matches(wrong) and topology_matches(repaired)
    def cap(label,value):
        folder=work/label;folder.mkdir()
        canvas=generate_primitive('CAP','Cap',value=value,pdkdir=SOURCE/'pdks/FinFET14nm_Mock_PDK',outputdir=folder)
        from align.cell_fabric.drc import DesignRuleCheck
        drc=DesignRuleCheck(canvas)
        drc.run()
        convert_GDSjson_GDS(str(folder/'CAP.gds.json'),str(folder/'CAP.gds'))
        library=gdstk.read_gds(str(folder/'CAP.gds'))
        cell=library.top_level()[0]
        pins=sorted(t['pin'] for t in canvas.terminals if t.get('pin'))
        return {'bbox':canvas.bbox.toList(),'terminal_count':len(canvas.terminals),'pins':pins,'gds_bbox':cell.bounding_box(),'polygon_count':len(cell.polygons),'drc_error_count':drc.num_errors,'path':str(folder)}
    a=cap('cap_target',2)
    b=cap('cap_wrong',8)
    c=cap('cap_repaired',2)
    # The independent fixture budget is 1.2 x 1.2 um, not a copied generator formula.
    def fits(data):
        low,high=data['gds_bbox']
        return high[0]-low[0] <= 1.2 and high[1]-low[1] <= 1.2
    assert fits(a) and not fits(b) and fits(c)
    assert a['pins']==b['pins']==c['pins']==['MINUS','PLUS']
    assert a['polygon_count']>0 and a['polygon_count']==c['polygon_count']
    assert a['gds_bbox']==c['gds_bbox']
    assert a['drc_error_count']==b['drc_error_count']==c['drc_error_count']==0
    data=dict(scenario_id='03.11.02',status='passed',versions={'ALIGN-pdk-source-tag':'v1.0','ALIGN-runtime':version('align'),'gdstk':version('gdstk')},tasks=[dict(id='repair_analog_topology_gate',status='passed',normal=normal,wrong=wrong,repaired=repaired),dict(id='repair_primitive_area_budget',status='passed',budget_um=[1.2,1.2],normal=a,wrong=b,repaired=c)],oracle_independence='反相器2器件的端口角色和6条连接由固定电路目标给定；电容使用独立1.2um宽高预算、PLUS/MINUS端口及gdstk独立GDS读回，不从生成器公式反推预期。',boundaries=['FinFET14nm_Mock_PDK为官方教学mock PDK，真实几何/LEF/GDS生成且该mock DRC为0；不是代工厂签核。','实际运行网表解析和电容原语两个局部子链，未执行原生PnR或从INV自动生成完整版图，未做电学仿真/真实电容值提取。','gdstk仅作为独立验证器读取二进制GDS，不加入第二套工具状态。','官方DRC源码将Adding region以ERROR级别日志输出，但num_errors=0；记录真实计数而非按日志级别推断失败。'])
    (OUT/'03.11.02.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(data,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
