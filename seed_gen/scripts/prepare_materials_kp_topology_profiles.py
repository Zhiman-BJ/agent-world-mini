"""Reviewed k.p and Berry/topology environments from joint full candidates."""
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe

BASE = 'seed_gen/scenario_collection/'


def entity(name, attributes, lifecycle):
    return dict(name=name, attributes=attributes.split(), lifecycle=lifecycle)


def kp(c):
    def op(module, name, methods, cap, reason, construction=None):
        return symbol('kdotpy', 'kdotpy.'+module, name, methods, cap, reason, construction)
    script=BASE+'verify_materials_kdotpy.py'
    c.write({
        'scenario_id': '01.08.01',
        'description': '用发布版Kane多带k·p模型组织材料参数、温度、层厚和离散步长，求解体能带与量子阱子带；定位温度与meV/eV错误，借助解析Gamma谱、均匀势平移及本征对残差验收。当前实跑是CdTe/HgTe体系，分类中的III-V/应变推广保留为未验证边界。',
        'packages': [('kdotpy','primary','独立专项主包，提供材料表、PhysParams、完整Hamiltonian和高层对角化；选直接Python链，避免CLI全局路径/个人配置状态。')],
        'symbols': [
            op('materials.materials','MaterialsList','__init__ clear get_from_string load_from_file parse_dict linearmix dump','materials','从明确材料文件读取，温度/组分求值，禁止默认扫描用户材料目录。'),
            op('materials.materials','Material','evaluate get_composition get_compound check_complete check_numeric get_undefined_variables copy update dump','materials','材料变量、完整性、显式求值与复制；错误温度需重建参数。','由MaterialsList.get_from_string产生，避免手造不完整材料。'),
            op('physparams','PhysParams','__init__ to_dict diff check_equal clear_param_cache z zvalues_nm interface_z_nm well_z_nm format_materials','configure','唯一物理参数/层栈状态，明确nm、meV、K及缓存失效。'),
            op('layerstack','LayerStack','layer_index mparam_layer get_strain_matrix get_density has_exchange','configure','只访问PhysParams创建的层栈几何/密度/应变诊断；不再暴露第二套构造。','PhysParams.layerstack运行时产生，源码属性不是额外工具。'),
            op('vector.vector','Vector','__init__ x y z xy xyz len to_dict to_tuple','sampling','动量nm^-1与磁场向量的显式表示；使用真实类而非types协议重复。'),
            op('hamiltonian.full','hbulk',None,'hamiltonian','构造完整体Hamiltonian用于Hermiticity和残差核验，不选内部块函数。'),
            op('hamiltonian.hamiltonian','hz_sparse',None,'hamiltonian','从层栈生成真实量子阱稀疏矩阵。'),
            op('hamiltonian.hamiltonian','hz_sparse_pot',None,'hamiltonian','势分布映射为矩阵，均匀20meV必须为20I。'),
            op('diagonalization.diagonalization','hbulk',None,'solve','高层体谱与本征态，区分同短名矩阵构造器。'),
            op('diagonalization.diagonalization','hz',None,'solve','高层量子阱目标能窗口和本征态求解，不隐藏SciPy直接求解任务。'),
            op('diagonalization.diagdata','DiagDataPoint','get_eival get_observable get_eivec_coeff select_eival sort_by_eival to_binary_file from_binary_file','observe','查询与裁剪求解点、排序和可信本地结果存档。','由hbulk/hz产生，固定任务读取eival/eivec字段；不手造成功结果。'),
        ],
        'sources': [c.source('https://kdotpy.physik.uni-wuerzburg.de/','官方介绍Kane模型、闪锌矿体/层/条带、光学和Hartree能力，明确专项物理边界。','material geometry spectrum','PhysParams hbulk hz','体谱/层结构任务'),
                    c.source('https://gitpages.physik.uni-wuerzburg.de/kdotpy/web/gallery/pictures','官方图库给出7nm HgTe夹在HgCdTe之间的完整命令，含层厚、zres、温度、动量范围与输出观察量；本轮降低规模并关闭应变/磁场。','layers grid temperature subbands','PhysParams hz','层结构求谱及配置诊断'),
                    c.source('https://gitpages.physik.uni-wuerzburg.de/kdotpy/web/docs/start','官方说明发布安装、隔离环境、标准测试及输出目录；据此固定本地验证与版本，而非运行默认全测试。','version run_directory fixture','MaterialsList.load_from_file','可重放来源和运行配置'),
                    c.source('https://scipost.org/SciPostPhysCodeb.47','方法论文摘要描述Kane模型晶格离散、有限结构以及HgTe量子阱等应用；用于校准场景范围，不把当前粗网格视为论文复现。','Kane_model lattice heterostructure','hz_sparse hbulk','单位/离散/本征态检查')],
        'entities': [entity('material_database','id path hash version','明确加载发布材料表→求值→检查；不读取个人材料覆盖。'),
                     entity('device_parameters','id materials temperature_K lengths_nm zres_nm orbitals revision','配置→派生层栈/缓存→修改使旧谱失效→归档重建。'),
                     entity('hamiltonian','id parameters_revision momentum_nm_inverse potential_meV shape','构造→叠加势→验厄米→求解；不复用旧参数矩阵。'),
                     entity('spectrum','id eigenvalues_meV eigenvectors units residual','求解→排序→核对谱/守恒→导出；简并本征矢允许换基。')],
        'capabilities':['materials','configure','sampling','hamiltonian','solve','observe'],
        'bridges':[{'from':'Material -> PhysParams.layerstack','to':'Hamiltonian -> DiagDataPoint','contract':'T需提前代入材料表达式；长度nm、能量meV、动量nm^-1。8轨道×15网格点为120维；势数组长度nz，排序比较简并谱。'}],
        'runtime_infrastructure':[{'reference':'runtime.kp_oracle','reason':'NumPy独立Varshni温度公式、Gamma对角谱、20I、Hermiticity与残差；标准JSON/NPZ配置归档，任务求解均用已选kdotpy高层函数。'},
                                  {'reference':'runtime.result_fields','reason':'读取DiagDataPoint.eival/eivec、PhysParams.nz与稀疏矩阵toarray，均为返回状态/数值容器基础操作。'}],
        'boundaries':['真实CdTe/HgTe/CdTe 2/3/2nm夹层，zres=.5nm，仅固定样例门槛，未做离散收敛/实验精度、III-V、BIA、磁场、应变或Hartree自洽验证。',
                      '初次未给T的材料仍含AstParameter，发布LayerStack诊断路径错误调用get_undefined_variables而抛AttributeError；改用真实get_from_string(...,{T:0})求值并check_numeric，不改上游源码。',
                      'GitLab wiki正文为空、README页只有Loading，因此不计已读来源；使用成功读取的官网图库/方法文章。',
                      '默认initialize_config/initialize_materials会写个人目录，不在选集或任务中；本地显式MaterialsList足以闭合任务。'],
        'tasks':[recipe('repair_temperature_and_bulk_spectrum','从官方CdTe材料表建立8带体模型，诊断300K配置无法满足0K带隙目标；显式重新求值温度并求谱，验证Gamma解析能级、±k对称、零场简并和本征残差。','目标T=0K、Gamma带隙1606meV；错误T=300K。',[
            ('读取并求值材料',['kdotpy.materials.materials.MaterialsList.__init__','kdotpy.materials.materials.MaterialsList.load_from_file','kdotpy.materials.materials.MaterialsList.get_from_string','kdotpy.materials.materials.Material.check_numeric']),
            ('配置并求解',['kdotpy.physparams.PhysParams.__init__','kdotpy.vector.vector.Vector.__init__','kdotpy.diagonalization.diagonalization.hbulk']),
            ('修温度并核残差',['kdotpy.materials.materials.MaterialsList.get_from_string','kdotpy.physparams.PhysParams.__init__','kdotpy.hamiltonian.full.hbulk','kdotpy.diagonalization.diagonalization.hbulk'])],['错误gap1528.7620808meV，修复1606；Gamma[-1480×2,-570×4,1036×2]，残差<1e-8。'],script),
                 recipe('repair_qw_potential_units_and_restore','建立CdTe/HgTe/CdTe量子阱并提取近零8条子带，识别将20meV误输为.02的单位错误；修复后确认所有子带平移20meV、势矩阵为20I且残差通过，归档参数再重建核对。','层厚2/3/2nm，zres=.5nm，8带，无应变；误势.02meV，目标20meV。',[
                     ('建层栈与求初谱',['kdotpy.physparams.PhysParams.__init__','kdotpy.hamiltonian.hamiltonian.hz_sparse','kdotpy.diagonalization.diagonalization.hz']),
                     ('修单位并验均匀势',['kdotpy.hamiltonian.hamiltonian.hz_sparse_pot','kdotpy.diagonalization.diagonalization.hz']),
                     ('以标准JSON归档显式配置后重建并求谱',['kdotpy.materials.materials.MaterialsList.get_from_string','kdotpy.physparams.PhysParams.__init__','kdotpy.diagonalization.diagonalization.hz'])],['120×120厄米矩阵，8子带平移20±1e-8meV，残差<1e-8，两次重复谱在1e-8内一致。'],script)],
        'runtime_report':BASE+'runtime/materials_kdotpy/01.08.01.json','runtime_scope':'真实Kane体和量子阱求解，独立Gamma/温度/均匀势oracle、错误修复与参数恢复，两次运行。',
    })


def topology(c):
    def z(module,name,methods,cap,reason,construction=None):
        return symbol('z2pack','z2pack.'+module,name,methods,cap,reason,construction)
    script=BASE+'verify_materials_topology.py'
    c.write({
        'scenario_id':'01.09.03',
        'description':'用TBmodels保存单一紧束缚模型，Z2Pack计算闭合k空间Wilson环与Chern数；通过独立Dirac质量公式、离散Berry相位和带隙门槛判定拓扑相、定位相变，并拒绝未收敛或带隙闭合下的无效结论。',
        'packages':[('z2pack','primary','以Wilson环/表面收敛和拓扑不变量为高层主工作流。PyPI2.2.1与固定Git提交全部Python源码逐字节相等。'),
                    ('tbmodels','complement','真实z2pack.tb.System适配入口需要其Model.hamilton/pos/occ；保留构造、变换、谱与HDF5，避免额外Wannier建模。'),
                    ('wannierberri','excluded','Berry曲率/积分是相邻替代路线，当前两带Chern任务由Wilson环闭合；不加入未验证Wannier输入、积分后台与重复谱状态。')],
        'symbols':[
            symbol('tbmodels','tbmodels._tb_model','Model','__init__ from_hop_list hamilton eigenval add_hop add_on_site remove_small_hop remove_long_range_hop set_sparse slice_orbitals change_unit_cell supercell reciprocal_lattice to_hdf5 from_hdf5 from_hdf5_file to_hr_file to_hr','model','单一模型、跃迁约定、参数修改/清理/晶胞变换与实际存储接口；裁掉CLI、Kwant转换和无关Wannier目录导入。'),
            symbol('tbmodels','tbmodels.helpers','matrix_to_hop',None,'model','矩阵到跃迁列表的明确转换。'),
            z('tb','System','__init__','bridge','官方TBmodels适配器，自动传轨道位置/维数/占据数和Hamiltonian convention2。'),
            z('hm','System','get_eig','bridge','TB System继承的本征态计算入口，可诊断占据带和闭回路。','由已选tb.System构造后继承；不额外公开任意外部Hamiltonian主状态。'),
            z('surface._run','run_surface',None,'solve','公开z2pack.surface.run真实别名来源，自适应表面Wilson环并记录收敛。'),
            z('line._run','run_line',None,'solve','公开z2pack.line.run来源，用于单条闭环采样诊断。'),
            z('invariant','chern',None,'invariant','对已验证的gapped表面结果计算Chern。'),
            z('surface._result','SurfaceResult','convergence_report','observe','完整线/表面收敛报告；返回结果不代表收敛。','由run_surface返回。'),
            z('line._result','LineResult','convergence_report','observe','单条线迭代门槛诊断。','由run_line或SurfaceResult.lines条目返回。'),
            z('surface._data','SurfaceData','t nearest_neighbour_dist','observe','采样位置和邻间距，用于分辨率诊断。','SurfaceResult.data由求解产生；不手工制造已收敛结果。'),
            z('line._data','WccLineData','pol gap_pos gap_size','observe','电荷中心极化和最大间隙，供错误相位/收敛诊断。','由line结果的data继承获得。'),
            z('line._data','OverlapLineData','wilson wcc wilson_eigenstates','observe','Wilson矩阵、电荷中心及本征态。','由EigenstateLineData继承；求解产生实例。'),
            z('line._data','EigenstateLineData','overlaps','observe','相邻本征态重叠，定位采样/规范错误。','由run_line内部产生并存于结果。'),
            z('plot','wcc',None,'visualize','可视化中心流，仅辅助观察，数值验收不依赖图片。'),
            z('plot','chern',None,'visualize','可视化Chern演化，保留与核心计算不同的诊断用途。'),
        ],
        'sources':[c.source('https://z2pack.greschd.ch/en/latest/examples/tb/tb.html','官方完整例子用TBmodels建立两子晶格模型并传给z2pack.tb.System/surface.run，指出少量num_lines影响收敛。','hoppings model occupied_bands surface','Model System run_surface','跨包建模到拓扑验收'),
                   c.source('https://z2pack.greschd.ch/en/latest/examples/hm/haldane.html','Haldane示例展示参数改变、Wilson中心流及Chern计算；本轮采用可手算方格两带模型作独立fixture，未冒称复现同一材料。','mass topological_phase','chern run_surface','参数错误相修复'),
                   c.source('https://z2pack.greschd.ch/en/latest/tutorial/invariants.html','教程明确chern/z2使用表面result；不变量必须在计算及收敛之后判定。','result invariant','chern','先验带隙/收敛再接受拓扑结论'),
                   c.source('https://z2pack.greschd.ch/en/latest/reference/tb.html','TB接口继承hm.System，从Model确定pos/bands/dim，说明真实数据桥接约定。','orbital_positions occupancy hamiltonian','tb.System hm.System.get_eig','矩阵相位约定与占据带检查')],
        'entities':[entity('tb_model','id lattice orbital_positions onsite hopping occupancy revision','构造→改质量/跃迁→旧拓扑结果失效→HDF5恢复。'),
                    entity('surface_run','id model_revision surface_orientation line_budget tolerances convergence_report','配置闭面→求Wilson环→检查每条线和相邻线门槛→保存结果。'),
                    entity('phase_result','id chern gap_min oracle residual valid','带隙和收敛通过才接受；闭隙点为未定义，不能硬四舍五入。'),
                    entity('parameter_scan','id masses results transition_bracket','扫描相邻质量→找闭隙位置→用独立公式验证变号。')],
        'capabilities':['model','bridge','solve','invariant','observe','visualize'],
        'bridges':[{'from':'TBmodels.Model','to':'z2pack.tb.System -> surface.run','contract':'fractional k；轨道都在原点，occ=1；只给正向跃迁时contains_cc=False由Model补共轭，实际H(k)逐点等于sin(kx)sx+sin(ky)sy+(m+cos(kx)+cos(ky))sz。'}],
        'runtime_infrastructure':[{'reference':'runtime.topology_oracle','reason':'NumPy只用于独立两带公式、四Dirac质量符号、31×31本征矢plaquette及带隙；不暴露为第二套环境工具。'},
                                  {'reference':'runtime.hdf5_archive','reason':'h5py提供已选Model.to_hdf5的文件句柄，包自身保存/恢复；JSON保存数值摘要，logging只抑制库输出。'}],
        'boundaries':['显式合成两带方格模型，能量以跃迁1为单位；没有DFT/Wannier90文件或真实材料拓扑结论。未验证时间反演Z2，因此不保留该不变量工具。',
                      'Z2Pack公开surface.run/line.run对应来源run_surface/run_line；继承数据字段实际存在，但不捏造缺失存储函数。',
                      'PyPI2.2.1无同名tag，全部发布Python文件与a82c83afbdac8e43a593c22c06de19cfe752d354相同；Windows非法测试文件名用稀疏checkout避开，源码保持clean。',
                      '某些参考模型变换/绘图未实跑；最终Agent封装仍需独立reset、结果失效与文件权限。'],
        'count_exception':'场景只保留单一模型、官方桥接、Wilson环/收敛、Chern和存储诊断；低于50不加入内部收敛控制器、重复求解器或未验证Z2/三维拓扑以凑数。',
        'tasks':[recipe('repair_mass_and_validate_chern','对给定二轨道模型求Wilson环，识别m=3产生平庸相不能满足目标C=-1；改为m=-1后核验收敛、四Dirac质量公式与独立plaquette整数，HDF5保存恢复模型后重复计算。','跃迁定义固定，occ=1，目标C=-1；surface(s,t)=[s,t]。',[
            ('构造与桥接',['tbmodels._tb_model.Model.__init__','tbmodels._tb_model.Model.hamilton','z2pack.tb.System.__init__']),
            ('计算并诊断质量',['z2pack.surface._run.run_surface','z2pack.surface._result.SurfaceResult.convergence_report','z2pack.invariant.chern']),
            ('修复与模型归档',['tbmodels._tb_model.Model.__init__','tbmodels._tb_model.Model.to_hdf5','tbmodels._tb_model.Model.from_hdf5_file','z2pack.surface._run.run_surface','z2pack.invariant.chern'])],['错误C≈0，修复C=-1；独立plaquette误差<1e-10，逐点Hamiltonian误差<1e-12；恢复结果一致。'],script),
                 recipe('repair_convergence_and_locate_transition','拒绝只允许一次k点迭代的未收敛拓扑结果，修复预算后核验所有收敛检查；扫描m=±.5确认C=-1/+1，在m=0与k=(.5,0)验证闭隙，标记该点不能赋予隔离占据带不变量。','错误iterator=range(4,5)，修复range(8,49,8)；pos_tol=.01。',[
                     ('计算错误预算并拒绝',['z2pack.surface._run.run_surface','z2pack.surface._result.SurfaceResult.convergence_report']),
                     ('修复并比较相邻相',['tbmodels._tb_model.Model.__init__','z2pack.surface._run.run_surface','z2pack.invariant.chern']),
                     ('定位闭隙并核验',['tbmodels._tb_model.Model.eigenval','tbmodels._tb_model.Model.hamilton'])],['所有修复收敛检查通过；m=0 gap<1e-10，m=±.5的C与质量公式及独立plaquette一致。'],script)],
        'runtime_report':BASE+'runtime/materials_topology/01.09.03.json','runtime_scope':'真实TBmodels/Z2Pack桥接和HDF5、故障/修复/相变，两次重复，独立质量与plaquette oracle。',
    })


if __name__=='__main__':
    collection=Collection()
    kp(collection)
    topology(collection)
