"""Parameterized cells and routing use one high-level facade plus actual base methods."""
from seed_gen.scripts.scenario_collection_support import Collection,symbol,recipe
from seed_gen.scripts.prepare_design_lab_sources import BASE,RAW


def gf(module,name,methods,cap,why,construction=None):
    return symbol('gdsfactory','gdsfactory'+('.'+module if module else ''),name,methods,cap,why,construction)


def kf(module,name,methods,cap,why,construction=None):
    return symbol('kfactory','kfactory.'+module,name,methods,cap,why,construction)


def k(name,methods,why,construction=None):
    return symbol('klayout','klayout.db',name,methods,'verify',why,construction)


def main():
    c=Collection(base=BASE,raw=RAW)
    geometry=c.source('https://gdsfactory.github.io/gdsfactory/notebooks/00_geometry/','官方正文展示Component多边形、引用、端口、变换、布尔、标签和GDS导出；强调metadata与外部EDA兼容、micrometer与DBU区分。',['component','polygon','port','metadata'],['Component.add_ref','Component.area','ComponentBase.write_gds'],['固定面积/端口目标与GDS重读核对'])
    refs=c.source('https://gdsfactory.github.io/gdsfactory/notebooks/01_references/','官方Instances and ports教程通过多个引用和端口连接组合直波导；端口位置/朝向/宽度是层次连接契约，实例变换不复制子单元。',['cell','instance','port','transform'],['DInstance.connect','ComponentBase.add_port','DInstance.dmove'],['层次组合后核对端点和网表连接'])
    cells=c.source('https://gdsfactory.github.io/gdsfactory/notebooks/03_cells_autoname_and_cache/','官方cell教程说明同参数复用缓存单元、参数变化生成不同名称；settings记录输入而info记录派生量，避免手写重复单元名。',['parametric_cell','settings','cache_identity','info'],['cell','components.straight','ComponentBase.get_netlist'],['参数错误修复和缓存身份不变量'])
    routing=c.source('https://gdsfactory.github.io/gdsfactory/notebooks/04_routing/','官方路由教程说明river/bundle两组端口的朝向、pitch、排序、radius与collision约束；正常几何生成并不自动保证用户目标长度。',['port_group','route','cross_section','route_error'],['route_single','route_bundle','get_min_spacing'],['修复放置产生的错误路长及端口组数量错误'])
    native=c.source('https://www.klayout.org/klayout-pypi/overview/layout/','原生官方Layout教程明确GDS读写、1nm默认DBU及整数坐标乘DBU得到微米；作为跨层持久化核对依据。',['gds_artifact','dbu','native_region'],['Layout.read','Layout.top_cell','Region.area'],['独立固定单位/面积/连通路径验收'])
    common=[
        gf('component','Component','add_ref area get_region get_polygons get_boxes get_labels extract remap_layers dup write','geometry','高层版图主对象和几何观察；裁剪GUI、三维渲染和复杂工艺修复。','实际继承DKCell.__init__；gf.Component()运行成功，源码不伪造构造函数。'),
        gf('component','ComponentBase','add_port get_ports_list add_label write_gds get_netlist write_netlist to_dict copy','hierarchy','真实继承定义提供端口、元数据、网表和GDS持久化。','由Component构造返回，绑定其继承方法。'),
        gf('pdk','Pdk','activate get_component get_cross_section get_layer get_layer_name get_layer_stack','technology','显式激活generic PDK并统一组件/截面/层解释；不加载未验证商业PDK。','使用随gdsfactory发布的gf.gpdk.PDK对象，经activate设置；未伪造新PDK。'),
        gf('pdk','get_active_pdk',None,'technology','读取当前工艺状态，避免隐式未激活。'),
        gf('pdk','get_layer',None,'technology','把层号或名称解析为统一底层索引。'),
        gf('cross_section.presets','strip',None,'technology','常用strip截面，明示宽度和弯曲半径。'),
        gf('cross_section.base','CrossSection','validate_radius copy get_xmin_xmax','technology','验证半径并观察截面边界，保留复制参数变体。','由strip/cross_section工厂返回的Pydantic数据对象。'),
        gf('components.waveguides.straight','straight',None,'geometry','固定直线PCell，输入length/width与面积/端口可独立验证。'),
        kf('kcell','DKCell','__init__','hierarchy','Component实际继承构造入口，统一微米表示。'),
        kf('kcell','ProtoTKCell','cell_index each_inst flatten dbbox','hierarchy','实际单元索引、实例遍历和边界框；只保留必要继承接口。','由Component/DKCell构造返回。'),
        kf('instance','ProtoTInstance','connect to_itype to_dtype','hierarchy','引用端口连接及单位对象转换；不重复暴露另一套底层路由器。','由Component.add_ref返回DInstance，使用实际ProtoTInstance继承定义。'),
        kf('geometry','GeometricObject','dmove dmovex dmovey drotate dmirror_x dmirror_y','placement','微米单位实例变换，避免同时纳入所有i/d/泛型别名。','由Component.add_ref返回的DInstance继承；port/box等属性作为状态记录。'),
        kf('port','DPort','__init__ copy copy_polar','ports','高层端口值对象，位置/宽度明确为µm。'),
        kf('port','ProtoPort','transform to_itype to_dtype','ports','端口几何变换/单位转换；name/center/width/orientation为状态。','由DPort或组件ports返回。'),
        kf('ports','DPorts','filter get_all_named','ports','按类型/名称选端口组，支撑连接和路由。','由Component.ports返回。'),
        k('Layout','__init__ read layer top_cell','导出GDS重新读取并获取原生图层，避免只验生成器内存值。'),
        k('Cell','begin_shapes_rec','按导出层次递归读取几何。','由Layout.top_cell返回。'),
        k('Region','__init__ area bbox merged count','原生几何核对固定面积/边界/连通图形数。'),
    ]
    packages=[('gdsfactory','primary','统一PCell、端口、PDK、层次与路由的高层入口。'),('kfactory','complement','仅选gdsfactory真实继承的实例/端口/变换和构造，固定兼容3.0.4；排除重复低层路由/独立单元体系。'),('klayout','complement','仅GDS重读与原生几何量测，作为边界核验；不复制完整数据库工具。'),('gdstk','excluded','与KLayout/GDSFactory的几何和GDS能力重叠，完整候选已存，当前无需第三套状态。')]
    for sid in ['03.01.01','03.01.02']:
        d=dict(scenario_id=sid,packages=packages,symbols=list(common),sources=[geometry,refs,cells,native],capabilities=['geometry','hierarchy','technology','placement','ports','verify'],
            entities=[dict(name='pdk_context',identity='pdk_name+version',attributes=['active','layer_map','cross_sections','dbu'],lifecycle='activate -> resolve factories/layers；切换PDK后原任务组件/规则须重新验证。'),dict(name='parametric_component',identity='cell_index+settings',attributes=['settings','info','ports','instances','geometry_revision','locked_cache'],lifecycle='factory -> cached cell -> add_ref -> connect/transform -> export；改变参数生成新cell，不原地污染共享缓存。'),dict(name='layout_artifact',identity='export_id',attributes=['source_revision','GDS path','dbu','area','bbox','netlist'],lifecycle='write_gds -> native read -> oracle；几何变化使旧导出/网表失效。')],
            bridges=[{'from':'gdsfactory.ComponentBase.write_gds','to':'klayout.db.Layout.read -> Region', 'tool':'gdsfactory.component.ComponentBase.write_gds + klayout.db.Layout.read','contract':'GDS使用1nm DBU；从原生递归层几何恢复面积/边界，固定目标按µm单位比较；metadata不冒充工艺签核。'}],
            runtime_infrastructure=[dict(reference='gdsfactory.generic_pdk',kind='infrastructure',reason='gf.gpdk.PDK为包内发布的固定generic PDK实例，经已选Pdk.activate启用；端口属性、缓存settings是状态，不人为编造方法。'),dict(reference='fixed_rectangular_geometry_oracle',kind='infrastructure',reason='面积length×width、手写端点/路数不变量和固定阈值；实际布线/导出由已选真实库完成。')],
            boundaries=['gf9.51.0硬依赖kfactory>=3.0.4,<3.1.dev0；最新3.2.1不能同时安装，其候选池/commit保留但不混入选集，见research/kfactory_compatibility.json。','仅generic PDK固定直线/端口组合；不声称代工厂DRC签核、器件光学/电学仿真或任意复杂布线已验证。','gdsfactory旧.html和无结尾/网址只返回跳转占位，未计研究；实际读取带/结尾的官方正文。','所有精选API未逐一实跑；后续Agent封装须限定本地工件路径并实现reset/版本失效。'],
            runtime_report=(BASE/f'runtime/gdsfactory/{sid}.json').as_posix(),runtime_scope='gdsfactory9.51/kfactory3.0.4真实参数组件/路由，KLayout0.30.12重读GDS；固定独立几何oracle。')
        if sid=='03.01.01':
            d.update(description='以GDSFactory参数单元、实例端口和generic PDK组织层次版图，kfactory仅补充其真实继承操作，KLayout负责导出边界核对；根据固定端点、面积、缓存身份和网表连通要求修复参数或端口宽度错误。')
            d['symbols'] += [gf('_cell','cell',None,'hierarchy','参数化命名/缓存入口，避免重复手写名称。'),gf('components.shapes.rectangle','rectangle',None,'geometry','常用基础矩形构造。'),gf('boolean','boolean',None,'geometry','单一高层AND/OR/XOR入口，避免重复暴露GDSTK。'),gf('read.import_gds','import_gds',None,'hierarchy','导入既有GDS并参与层次组合。')]
            d['tasks']=[
                recipe('parameterized_hierarchy_and_dimension_repair','构造10µm与20µm、宽0.5µm直波导并端口连接，验证重复参数复用cell、改变参数创建新cell；以30µm端点、15µm²面积和一条内部网为目标，修复误设25µm的第二段并导出GDS复核。','generic PDK/1nm DBU，两个子单元与两个顶层端口。',[('激活工艺并生成参数组件',['gdsfactory.pdk.Pdk.activate','gdsfactory.components.waveguides.straight.straight','gdsfactory.component.Component','kfactory.kcell.ProtoTKCell.cell_index']),('实例化和连接',['gdsfactory.component.Component.add_ref','kfactory.instance.ProtoTInstance.connect','gdsfactory.component.ComponentBase.add_port']),('固定目标与导出核验',['gdsfactory.component.Component.area','gdsfactory.component.ComponentBase.get_netlist','gdsfactory.component.ComponentBase.write_gds','klayout.db.Layout.__init__','klayout.db.Layout.read','klayout.db.Cell.begin_shapes_rec','klayout.db.Region.__init__','klayout.db.Region.area','klayout.db.Region.bbox'])],['同参数同cell/不同参数不同cell','2实例2顶层port1内部net','错误35µm/17.5µm²→30µm/15µm²','GDSbbox=[0,-250,30000,250]DBU'],'verify_gdsfactory.py:pcell dimensions'),
                recipe('repair_port_width_mismatch','在同一两段层次中把第二段宽度误设1µm，观察实际PortWidthMismatchError；恢复0.5µm后重新连接并提取网表，保持15µm²和一个内部网络。','目标端口宽度0.5µm固定，不允许通过allow_width_mismatch绕过。',[('生成不匹配端口',['gdsfactory.components.waveguides.straight.straight','gdsfactory.component.Component.add_ref']),('尝试连接并定位错误',['kfactory.instance.ProtoTInstance.connect']),('修改参数后重连验证',['gdsfactory.components.waveguides.straight.straight','kfactory.instance.ProtoTInstance.connect','gdsfactory.component.ComponentBase.get_netlist','gdsfactory.component.Component.area'])],['1µm与0.5µm连接拒绝','恢复0.5µm后1内部net','固定面积15µm²'],'verify_gdsfactory.py:pcell width'),
            ]
        else:
            d.update(description='以GDSFactory单路由和bundle高层入口、kfactory端口/实例状态组织布线任务；按固定端点距离、路数与面积验收，并在错误放置或端口数量不一致时修复状态重新生成路线。原生KLayout检查导出几何。')
            d['sources']=[geometry,refs,routing,native]
            d['capabilities'] += ['routing']
            d['symbols'] += [gf('routing.route_single','route_single',None,'routing','端口对的Manhattan高层路由，不直接暴露重复低层router。'),gf('routing.route_bundle','route_bundle',None,'routing','端口组联合路由，统一数量/排序/方向/间距约束。'),gf('routing.route_bundle','get_min_spacing',None,'routing','在路由前估算扇出空间需求。'),gf('routing.sort_ports','sort_ports',None,'routing','按几何顺序组织端口组。'),gf('components.bends.bend_euler','bend_euler',None,'geometry','常用连续曲率弯曲PCell作为路由组件。'),gf('components.tapers.taper','taper',None,'geometry','显式宽度转换器，避免静默绕过端口契约。'),gf('routing.route_bundle_sbend','route_bundle_sbend',None,'routing','空间紧张时S弯替代策略，未在固定直线任务实跑。')]
            d['entities'] += [dict(name='route_plan',identity='route_id+source_revision',attributes=['source_ports','destination_ports','cross_section','radius','sort_ports','placement','expected_length','expected_count'],lifecycle='select groups -> validate -> generate -> measure/export；改端口/放置后丢弃旧路线重新生成。')]
            d['tasks']=[
                recipe('repair_single_route_placement','连接两段10µm直波导的相向端口，目标空隙100µm、总面积60µm²；发现目标器件误放x=100µm导致路线90µm/面积55µm²，将其恢复x=110µm并重建路线。','源端位于x=10µm，目标端应位于x=110µm，宽0.5µm。',[('创建组件和实例定位',['gdsfactory.pdk.Pdk.activate','gdsfactory.components.waveguides.straight.straight','gdsfactory.component.Component.add_ref','kfactory.geometry.GeometricObject.dmove']),('生成路线并测量',['gdsfactory.routing.route_single.route_single','gdsfactory.component.Component.area']),('修复放置重新生成',['kfactory.geometry.GeometricObject.dmove','gdsfactory.routing.route_single.route_single','gdsfactory.component.Component.area'])],['错误路线90µm/总面积55µm²拒绝','固定目标路线100µm','修复总面积60µm²'],'verify_gdsfactory.py:routing single'),
                recipe('repair_bundle_port_count','以y=0/10/20µm的三对相向端口生成100µm平行bundle；删除一个终点触发真实端口数不等异常，恢复终点后重布线，并核对3条路线、150µm²面积及GDS中3个独立图形。','x=0到100µm、宽0.5µm的三路线固定目标。',[('创建端口组与工艺层',['kfactory.port.DPort.__init__','gdsfactory.pdk.get_layer']),('执行bundle并观察数量错误',['gdsfactory.routing.route_bundle.route_bundle']),('修复目标组重生成',['kfactory.port.DPort.__init__','gdsfactory.routing.route_bundle.route_bundle','gdsfactory.component.Component.area']),('导出复核',['gdsfactory.component.ComponentBase.write_gds','klayout.db.Layout.read','klayout.db.Cell.begin_shapes_rec','klayout.db.Region.__init__','klayout.db.Region.merged','klayout.db.Region.count','klayout.db.Region.area'])],['3源/2目标拒绝','修复3×100µm','固定面积150µm²','GDS重读3独立路径'],'verify_gdsfactory.py:routing bundle'),
            ]
        c.write(d)


if __name__=='__main__':
    main()
