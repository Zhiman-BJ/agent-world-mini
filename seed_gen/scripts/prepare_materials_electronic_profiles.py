"""Reviewed joint selections for diffusion and electronic postprocessing."""
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe
from seed_gen.scripts.prepare_materials_workflow_physics_profiles import PREFIX, core, entity

FIXTURE = PREFIX+'verify_materials_electronic.py'
RUNTIME = PREFIX+'runtime/materials_electronic/'


def main():
    c = Collection()
    def source(url, evidence, entities, tools, tasks):
        return c.source(url, evidence, entities.split(), tools.split(), tasks.split())
    def op(package, module, name, methods, capability, reason, construction=None):
        return symbol(package, module, name, methods, capability, reason, construction)
    def task(identifier, description, initial, steps, assertions):
        return recipe(identifier, description, initial, steps, assertions, FIXTURE)
    def write(design):
        sid=design['scenario_id']
        design.update(runtime_report=RUNTIME+sid+'.json')
        c.write(design)

    da='pymatgen.analysis.diffusion.analyzer.'
    df='pymatgen-analysis-diffusion'
    write({
        'scenario_id':'01.05.03',
        'description':'原子扩散后处理环境：管理固定结构、轨迹、移动物种、时间步长和温度；校正框架漂移，分析MSD、扩散张量与电导，拟合Arrhenius关系，并可观察概率密度与占位。pymatgen-analysis-diffusion承担分析，pymatgen-core提供共享结构和轨迹。固定解析轨迹验证单位/采样步修复，人工Arrhenius数据验证温度与外推；不运行MD或声称得到真实材料迁移能垒。',
        'packages':[(df,'primary','MSD、漂移、扩散/电导和温度外推主实现。'),('pymatgen-core','complement','共享结构与轨迹输入、单位转换及保存恢复；不再增加ASE状态副本。')],
        'symbols':[
            op(df,da[:-1],'DiffusionAnalyzer','__init__ get_drift_corrected_structures get_summary_dict get_framework_rms_plot get_msd_plot export_msdt from_structures from_vaspruns from_files as_dict from_dict','diffusion','从结构或固定MD输出建分析器，保留漂移诊断、观察和导出；排除重复plot_msd包装。'),
            *[op(df,da[:-1],name,None,'temperature','温度/单位转换及独立MSD或Arrhenius分析链。') for name in ('get_conversion_factor','fit_arrhenius','get_diffusivity_from_msd','get_extrapolated_diffusivity','get_extrapolated_conductivity','get_arrhenius_plot')],
            op(df,'pymatgen.analysis.diffusion.aimd.pathway','ProbabilityDensityAnalysis','__init__ from_diffusion_analyzer generate_stable_sites get_full_structure to_chgcar','pathway','对同一漂移校正轨迹作概率密度与稳定站点观察，不引入NEB外部能量求解。'),
            op(df,'pymatgen.analysis.diffusion.aimd.pathway','SiteOccupancyAnalyzer','__init__ get_average_site_occupancy from_diffusion_analyzer','pathway','从共享分析器读取站点占位，辅助定位轨迹路径。'),
            core('core.trajectory','Trajectory','__init__ get_structure to_positions to_displacements write_Xdatcar as_dict from_structures from_file','trajectory','持久化位置/位移表示和轨迹帧；不暴露无关分子功能。'),
            core('core.structure','Structure','__init__ sites frac_coords cart_coords','structure','显式结构输入及坐标观察，修改输入后重新分析。'),
            core('core.structure','IStructure','lattice volume pbc copy as_dict from_dict from_file to','structure','输入晶胞、周期和结构恢复，由Structure继承。','通过Structure或from_file/from_dict获得具体对象。'),
            core('core.structure','SiteCollection','species num_sites composition get_distance is_valid','structure','核对移动/框架原子身份、计数与结构有效性。','来自Structure继承，不直接构造基类。'),
            core('core.lattice','Lattice','__init__ cubic matrix volume pbc get_cartesian_coords get_fractional_coords as_dict from_dict','structure','实空间单位、周期和晶胞变换输入。'),
        ],
        'sources':[
            source('https://github.com/materialsvirtuallab/pymatgen-analysis-diffusion/blob/v2025.11.14/README.rst','官方说明依赖pymatgen结构/I/O，并列出概率密度、Van-Hove和迁移路径；用于界定轨迹后处理与外部能量计算。','structure trajectory pathway','DiffusionAnalyzer ProbabilityDensityAnalysis','固定轨迹分析'),
            source('https://github.com/materialsvirtuallab/pymatgen-analysis-diffusion/blob/v2025.11.14/src/pymatgen/analysis/diffusion/analyzer.py','发布源码明确位移Å、dt=time_step*step_skip fs、框架漂移扣除以及Arrhenius温度K、D单位cm²/s。','time_axis displacement temperature diffusivity','get_summary_dict fit_arrhenius','时间步和温度修复'),
            source('https://github.com/materialsvirtuallab/pymatgen-analysis-diffusion/blob/v2025.11.14/tests/pymatgen/analysis/diffusion/test_analyzer.py','官方测试覆盖固定扩散系数、分量、NPT和Arrhenius误差模式，提供保持输入/单位/参考值一致的回归范式；本样例采用独立解析轨迹。','diffusion_result activation_energy','DiffusionAnalyzer fit_arrhenius','固定参考断言'),
        ],
        'entities':[entity('trajectory','structure_id positions displacements time_step_fs step_skip mobile_species source_hash','加载→核对物种/单位→选择位置或位移→漂移校正；采样或输入改变使分析失效。'),entity('diffusion_result','trajectory_revision temperature_K msd diffusivity_cm2_s components conductivity','分析→有限性/参考检查→导出/恢复。'),entity('arrhenius_fit','temperatures_K diffusivities_cm2_s activation_eV prefactor extrapolation','汇集多个温度→拟合→固定温度外推；单位变化需重拟合。')],
        'capabilities':['structure','trajectory','diffusion','temperature','pathway'],
        'bridges':[{'from':'pymatgen Structure/Trajectory','to':'DiffusionAnalyzer and pathway observers','contract':'保持原子排序、移动/框架物种和晶胞一致；位移(site,time,axis)为Å，time_step*step_skip为fs。'}],
        'runtime_infrastructure':[{'reference':'runtime.diffusion_oracle','reason':'NumPy生成指定sqrt(t)位移和公共漂移，SciPy常数用于独立Arrhenius式；JSON存储和断言不代替库分析。'}],
        'boundaries':['解析制造轨迹不是平衡MD；未验证真实材料采样、相关误差或区域扩散。','概率密度和占位保留为相关观察接口，本批未实跑全部路径API；未包含NEB能量或势垒求解。','官方文档站404；仓库重定向到materialyzeai，所选commit身份保持不变。'],
        'tasks':[
            task('repair_trajectory_timestep','给定含框架漂移的Li/Si解析轨迹，计算扩散系数并定位错误step_skip；修复为5后重新分析，断言D及三个分量均为2e-5 cm²/s，MSD满足6Dt、框架不再漂移，并保存恢复结果。','3原子、101帧、time_step=2fs、正确step_skip=5；目标固定。',[
                ('构造结构和轨迹分析器',['pymatgen.core.lattice.Lattice.cubic','pymatgen.core.structure.Structure.__init__',da+'DiffusionAnalyzer.__init__']),
                ('校正与检查输出',[da+'DiffusionAnalyzer.get_drift_corrected_structures',da+'DiffusionAnalyzer.get_summary_dict']),
                ('保存恢复',[da+'DiffusionAnalyzer.as_dict',da+'DiffusionAnalyzer.from_dict',da+'DiffusionAnalyzer.export_msdt'])],['错误step_skip=1得到1e-4；修复后2e-5，rtol1e-10；框架坐标误差<1e-12。']),
            task('repair_arrhenius_temperature','给定500–950K的固定扩散数据，发现将摄氏值直接输入导致活化能错误；转换为K后拟合并外推到400K，断言Ea=0.3eV、D0=0.01cm²/s，外推符合独立指数关系。','人工D=D0 exp(-Ea/kT)，四温度数据。',[
                ('拟合错误和修复温度',[da+'fit_arrhenius']),('外推并验收',[da+'get_extrapolated_diffusivity'])],['摄氏输入不满足1%活化能目标；修复参数和外推rtol1e-10。'])],
        'runtime_scope':'真实扩散/Arrhenius接口作用于制造数据；时间单位、漂移、保存和独立数值验收，无MD。',
    })

    bs='pymatgen.electronic_structure.bandstructure.'
    ds='pymatgen.electronic_structure.dos.'
    sb='sumo.electronic_structure.bandstructure.'
    sd='sumo.electronic_structure.dos.'
    write({
        'scenario_id':'01.06.01',
        'description':'半导体能带与DOS后处理环境：以Sumo为主合并分段能带、管理能量参考、读取总态/投影态密度并导出绘图数据；pymatgen-core承担共同的BandStructure、Dos与VASP文件对象。围绕VBM/CBM、直接/间接带隙和投影守恒构造可验证任务。PyProcar作为替代候选排除，避免重复对象及NumPy版本冲突；不运行新的DFT。',
        'packages':[('sumo','primary','读取/合并/投影/导出与展示主链，直接复用pymatgen对象。'),('pymatgen-core','complement','Sumo硬依赖的能带/DOS/VASP数据模型和带边查询；不重复暴露通用绘图器。'),('pyprocar','excluded','能带/DOS常用能力重叠且6.5要求NumPy<2、Sumo3要求NumPy>=2；自旋纹理/费米面不属本场景任务。')],
        'symbols':[
            *[op('sumo',sb[:-1],n,None,'bands','合并分段数据、明确自旋并按元素/轨道观察权重。') for n in ('get_reconstructed_band_structure','get_projections','get_projections_by_branches','string_to_spin')],
            *[op('sumo',sd[:-1],n,None,'dos','总态/投影态提取及带能量参考的数据导出。') for n in ('load_dos','get_pdos','get_element_pdos','write_files')],
            op('sumo','sumo.plotting.bs_plotter','SBSPlotter','__init__ get_plot get_projected_plot','presentation','高层能带/投影图，排除底层颜色/坐标轴辅助。'),
            op('sumo','sumo.plotting.dos_plotter','SDOSPlotter','__init__ dos_plot_data get_plot','presentation','DOS数据与图共用同一状态。'),
            core('electronic_structure.bandstructure','BandStructure','__init__ is_metal get_vbm get_cbm get_band_gap get_direct_band_gap get_projection_on_elements get_projections_on_elements_and_orbitals as_dict from_dict','bands','数值带边与判定及恢复；不暴露旧格式兼容方法。'),
            core('electronic_structure.bandstructure','BandStructureSymmLine','__init__ get_equivalent_kpoints get_branch apply_scissor as_dict','bands','高对称线和显式剪刀修正；修改后需重新计算带隙。'),
            core('electronic_structure.bandstructure','Kpoint','lattice label frac_coords cart_coords','bands','观察采样身份和单位。','由BandStructure及其子类构造并持有。'),
            core('electronic_structure.dos','Dos','__init__ get_densities get_smeared_densities get_interpolated_value get_interpolated_gap get_cbm_vbm get_gap from_dict as_dict','dos','DOS能量、有限网格插值、带隙观察和保存。'),
            core('electronic_structure.dos','CompleteDos','__init__ get_site_orbital_dos get_site_dos get_spd_dos get_element_dos get_element_spd_dos from_dict as_dict','dos','投影数据逐站点/元素/轨道汇总及来源追踪。'),
            core('io.vasp.outputs','Vasprun','__init__ complete_dos get_band_structure eigenvalue_band_properties calculate_efermi as_dict','load','固定VASP输出读取与能量参照，不调用VASP程序或POTCAR下载。'),
            core('core.lattice','Lattice','__init__ cubic reciprocal_lattice matrix get_cartesian_coords get_fractional_coords','load','共享倒易晶格和坐标约定，保证k路径单位明确。'),
        ],
        'sources':[
            source('https://smtg-bham.github.io/sumo/sumo-bandstats.html','官方说明读取分段vasprun、报告带隙与有效质量，采样点数影响拟合；本场景只承担带边分析，专门质量拟合留给01.06.02。','bandstructure edge fermi','get_band_gap get_vbm get_cbm','直接/间接带隙与能量参照'),
            source('https://smtg-bham.github.io/sumo/sumo-bandplot.html','支持分段目录合并、轨道投影以及能带/DOS同图，证明共用能量和k路径状态的重要性。','band_chunks projections energy_reference','get_reconstructed_band_structure SBSPlotter','分段合并和参考修复'),
            source('https://smtg-bham.github.io/sumo/sumo-dosplot.html','官方说明从VASP抽取总DOS并按元素、轨道和站点分解；默认包括所有原子，图例阈值不应误当数据过滤。','dos pdos site_selection','load_dos get_pdos write_files','投影求和和导出能量验收'),
        ],
        'entities':[entity('band_dataset','source_hash chunks reciprocal_lattice kpoints energies_eV fermi_eV spin','载入→合并→校验→求带边→可选剪刀变换；改变EF/能量后旧分类失效。'),entity('dos_dataset','source_hash energies_eV fermi_eV densities projections units','载入→选择投影/展宽→观察→按指定参考导出；记录每次变换。'),entity('analysis_artifact','dataset_revision gap direct_gap vbm cbm reference output_files','计算→独立验收→保存恢复；文件保留绝对/相对能量约定。')],
        'capabilities':['load','bands','dos','presentation'],
        'bridges':[{'from':'pymatgen Vasprun/BandStructure/CompleteDos','to':'sumo merge/load/projection/export','contract':'直接传同一对象模型；能量eV，倒易晶格含2π；分段必须采用共同能量零点和自旋通道。'}],
        'runtime_infrastructure':[{'reference':'runtime.band_dos_oracle','reason':'NumPy解析双能带定义间接1.2/直接1.7eV目标；发布XML的原始站点/轨道数据独立求和；JSON和DAT回读验收。'}],
        'boundaries':['未计算新DFT或应变前后的物理能带；制造双能带只验证后处理。','DOS取Sumo v3.0.0官方Cs2SnBr6 SOC预计算文件；NEDOS=2000、单自旋、投影求和/能量导出已验，未声称投影总和等于含间隙区域的总DOS。','候选PyProcar大小写相同字节模块已补齐索引，但未安装运行；不把不兼容版本混入环境。'],
        'tasks':[
            task('repair_band_reference','合并两段给定能带，定位错误EF导致的金属判定；修复EF到禁带内后输出带边和直接/间接带隙，断言VBM0、CBM1.2、间接隙1.2eV、最小直接隙1.7eV，保存恢复保持结论。','21个k点的固定解析双带；错误EF=2eV，正确0.6eV。',[
                ('构造并合并分段',[bs+'BandStructureSymmLine.__init__',sb+'get_reconstructed_band_structure']),
                ('查询带边和带隙',[bs+'BandStructure.is_metal',bs+'BandStructure.get_vbm',bs+'BandStructure.get_cbm',bs+'BandStructure.get_band_gap',bs+'BandStructure.get_direct_band_gap']),
                ('保存恢复',[bs+'BandStructureSymmLine.as_dict',bs+'BandStructure.from_dict'])],['错误结果metal=True；修复为间接半导体；带边和带隙atol1e-12。']),
            task('repair_dos_export_reference','读取官方Cs2SnBr6 SOC输出，检查投影聚合与通道维度，发现导出的绝对能量不符合E-EF目标；用正确参考重新导出，回读断言2000点能量及DOS逐点一致。','Sumo发布测试vasprun.xml.gz，不运行VASP；导出目标固定为E-EF。',[
                ('读取和汇总',['pymatgen.io.vasp.outputs.Vasprun.__init__',sd+'load_dos','pymatgen.io.vasp.outputs.Vasprun.complete_dos']),
                ('修复导出参数并回读',[sd+'write_files'])],['单Spin.up、2000点；元素/轨道求和等于原始站点投影；错误参考失败、正确能量误差<1e-10。'])],
        'runtime_scope':'Sumo真实分段合并和SOC DOS读取/投影/导出；解析与发布固定数据，无新DFT。',
    })

    em='effmass.analysis.Segment.'
    write({
        'scenario_id':'01.06.02',
        'description':'有效质量分析环境：载入或构造带有晶格、k路径和费米能的能带，使用effmass选择极值片段、拟合曲率/非抛物色散并诊断拟合区间。ASE仅提供数据容器与真实DataASE桥接，不承担额外电子结构求解。固定抛物线验证方向相关电子/空穴曲率质量，四次项样例验证缩小拟合窗口后的误差修复。',
        'packages':[('effmass','primary','极值定位、分段、有效质量定义及拟合诊断。'),('ase','complement','BandPath/BandStructure/Cell和Atoms作为明确单位的数据桥，不安装或暴露DFT计算器。')],
        'symbols':[
            op('effmass','effmass.analysis','Segment','__init__ explosion_index weighting poly_derivatives poly_fit inertial_effmass transport_effmass alpha kane_mass_band_edge kane_fit finite_difference_effmass finite_difference_fit weighted_leastsq_effmass weighted_leastsq_fit five_point_leastsq_effmass five_point_leastsq_fit','fit','片段曲率与Kane拟合、多个近带边估计和有效区间诊断；裁去积分型光学质量扩展。'),
            op('effmass','effmass.inputs','Settings','__init__ check_settings','segments','控制极值深度、能量窗口和方向，修改后重新生成片段。'),
            op('effmass','effmass.inputs','Data','check_data find_cbm_vbm','input','检查输入和费米能推断带边；由具体数据适配类继承。','通过DataASE/DataVasprun/DataAims构造。'),
            op('effmass','effmass.inputs','DataASE','__init__','input','ASE电子结构对象→effmass真实桥接，显式验证EF在带隙。'),
            op('effmass','effmass.inputs','DataVasprun','__init__','input','外部预计算VASP数据读取入口，非调用VASP。'),
            op('effmass','effmass.inputs','DataAims','__init__','input','官方教程FHI-aims预计算输出入口。'),
            *[op('effmass','effmass.extrema',n,None,'segments','高层极值/片段发现及方向筛选，不重复暴露邻点扫描内部辅助。') for n in ('generate_segments','filter_segments_by_direction','find_CB_indices','find_VB_indices','calculate_direction')],
            *[op('effmass','effmass.outputs',n,None,'observe','将拟合片段和参数形成可审查图表/报告。') for n in ('plot_segments','make_table','print_summary_file')],
            op('ase','ase.atoms','Atoms','__init__','input','明确实空间晶胞和原子身份，不暴露计算器接口。'),
            op('ase','ase.cell','Cell','__init__ new reciprocal lengths angles volume cartesian_positions scaled_positions','input','实/倒空间单位核对；ASE reciprocal不含2π，DataASE内部补齐。'),
            op('ase','ase.dft.kpoints','BandPath','__init__ cell kpts special_points path interpolate cartesian_kpts todict','input','记录路径、采样与分数k点；重采样后须匹配新能带。'),
            op('ase','ase.spectrum.band_structure','BandStructure','__init__ energies path reference subtract_reference todict get_labels','input','以自旋/k点/能带数组和能量参考封装输入。'),
        ],
        'sources':[
            source('https://effmass.readthedocs.io/en/latest/','官方定义从选定能带片段计算多种有效质量，并提供极值定位和近似色散图；所选发布为2.3.0，站点版本2.3.2.dev3。','band_data segment mass_result','Segment generate_segments','方向相关质量'),
            source('https://effmass.readthedocs.io/en/latest/analysis.html','分析接口说明有限差分、五点拟合、加权拟合和Kane模型；电子质量为单位m0，拟合能量转换为Hartree。','fit_window curvature','finite_difference_effmass five_point_leastsq_effmass','区间敏感性和固定曲率目标'),
            source('https://github.com/lucydot/effmass/blob/v2.3.0/README.md','发布README推荐检查片段是否合理并保存CLI选项，提供VASP/FHI-aims数据流程；本试点通过ASE容器固定输入以建立独立oracle。','settings segments report','DataASE plot_segments','记录采样和拟合设置'),
        ],
        'entities':[entity('band_input','cell_A kpoints_fractional energies_eV fermi_eV spin occupancy source_hash','加载→检查晶格/能量约定和EF→建Data；上游变化使片段失效。'),entity('band_segment','input_revision band_index kpoint_indices direction window','定位极值→选方向/窗口→拟合；禁止混合转折路径。'),entity('mass_fit','segment_revision method curvature_mass_m0 coefficients residual validity','拟合→单位/符号/参考验收→导出；窗口或采样改变需重算。')],
        'capabilities':['input','segments','fit','observe'],
        'bridges':[{'from':'ASE BandPath/BandStructure/Atoms','to':'effmass.inputs.DataASE -> Segment','contract':'ASE energies(spin,kpoint,band)转换为effmass(band,kpoint)；reciprocal乘2π，EF为同一能量参考；无occupancy时必须额外确认禁带。'}],
        'runtime_infrastructure':[{'reference':'runtime.mass_oracle','reason':'SciPy物理常数生成E=hbar²k²/(2m)与已知四次项，独立质量目标；数组构造/导出不替代effmass拟合。'}],
        'boundaries':['effmass2.3极值代码将零能量当false，零VBM触发max(empty)；样例将所有能量与EF同时平移-1eV，保留失败证据，曲率目标不变。','库换算常数精度有限，抛物质量rtol2e-5；非抛物窗口任务固定2%，不以放宽容差修复。','负价带曲率不是负物理空穴质量；空穴质量取其幅值。','模型色散不属于某个真实材料；保留的其他数据适配器和Kane接口未全部实跑。'],
        'tasks':[
            task('directional_effective_mass','从给定两个方向的ASE能带构建effmass数据与带边片段，验证晶格和EF约定，计算电子方向质量及价带曲率质量，断言分别为0.2、0.4和-0.5m0。','解析抛物色散；共同能量偏移-1eV，EF=-0.25eV，晶胞5Å。',[
                ('构造路径和桥接',['ase.atoms.Atoms.__init__','ase.dft.kpoints.BandPath.__init__','ase.spectrum.band_structure.BandStructure.__init__','effmass.inputs.DataASE.__init__']),
                ('选择片段计算曲率',[em+'__init__',em+'five_point_leastsq_effmass',em+'finite_difference_effmass'])],['EF严格位于禁带；倒易基=2π/5；三个质量rtol2e-5。']),
            task('repair_mass_fit_window','对含四次项的固定色散使用宽拟合窗口，发现质量未达到0.2m0±2%目标；保留同一物理输入，缩小到k≤0.01Å⁻¹的窗口，重新拟合并通过原容差。','21个0–0.1Å⁻¹采样，已知2000eVÅ⁴四次项。',[
                ('选择宽/窄片段',[em+'__init__']),('拟合与比较',[em+'five_point_leastsq_effmass'])],['宽窗口约0.09982m0失败；窄窗口约0.19801m0满足相同2%容差。'])],
        'runtime_scope':'ASE→effmass实际桥接和有限差分/五点质量，含采样与零能量问题诊断；非DFT。',
    })

    ob='openbandparams.'
    alloy=ob+'iii_v_zinc_blende_alloy.IIIVZincBlendeAlloy.'
    ternary=ob+'iii_v_zinc_blende_ternary.IIIVZincBlendeTernary.'
    write({
        'scenario_id':'01.06.03',
        'description':'III-V半导体材料参数环境：以openbandparams的版本化经验参数与文献为唯一数据源，管理材料、温度、合金组分和可选(001)应变配置；查询带隙/带偏移/有效质量、晶格及Kane/Luttinger参数，按目标晶格匹配合金并导出可追溯参数记录。固定GaAs温度修复与GaInAs/InP晶格匹配任务验证单位和组分，不将参数查询等同于TCAD或k·p求解。',
        'packages':[('openbandparams','primary','同一套III-V经验表、参数引用和温度/组分插值，无需另一个重复材料数据库。')],
        'symbols':[
            op('openbandparams',ob+'alloy','Alloy','__init__ add_parameter set_parameter has_parameter get_parameter get_unique_parameters','metadata','参数身份/单位/引用与受控自定义覆盖；修改后旧计算失效。'),
            op('openbandparams',ob+'parameter','Parameter','get_references','metadata','读取原始参数文献；保留空引用而不编造。','ValueParameter/FunctionParameter/MethodParameter实例继承此接口。'),
            op('openbandparams',ob+'parameter','ValueParameter','__init__ __call__','metadata','数值参数与公开调用协议；协议从发布源码显式提取。'),
            op('openbandparams',ob+'parameter','FunctionParameter','__init__ __call__','metadata','函数参数的求值与定义，不能当作静态数值。'),
            op('openbandparams',ob+'parameter','MethodParameter','bind __call__ get_references','metadata','装饰器产生的可调用参数绑定与递归文献依赖查询。','发布版@method_parameter创建，并由Alloy.get_parameter返回已绑定实例。'),
            op('openbandparams',ob+'iii_v_zinc_blende_binary','IIIVZincBlendeBinary','element_fraction','composition','二元端点元素身份。','发布版iii_v_zinc_blende_binaries中的GaAs/InAs/InP是此类预置数据对象，继承Alloy构造。'),
            op('openbandparams',ob+'iii_v_zinc_blende_ternary','IIIVZincBlendeTernary','__init__ __call__ element_fraction','composition','真实合金工厂：组分x/元素分数或a,T匹配，并验证范围；不是伪造GaInAs顶层函数。'),
            op('openbandparams',ob+'iii_v_zinc_blende_quaternary','IIIVZincBlendeQuaternary','__init__ __call__ element_fraction','composition','复杂异质结构参数准备所需四元组分/晶格匹配入口。'),
            op('openbandparams',ob+'iii_v_zinc_blende_alloy','IIIVZincBlendeAlloy','strained_001 CBO CBO_Gamma CBO_L CBO_X Eg Eg_Gamma Eg_L Eg_X F P luttinger4 a luttinger32 meff_SO meff_e_Gamma meff_e_L_DOS meff_e_X_DOS meff_hh_100 meff_hh_110 meff_hh_111 meff_lh_100 meff_lh_110 meff_lh_111 nonparabolicity','properties','高层物性与各方向/能谷有效质量，服务下游TCAD/k·p输入；省略光学折射率扩展。','由二元/三元/四元预置材料继承，不直接构造基类。'),
            op('openbandparams',ob+'iii_v_zinc_blende_mixed_alloy','IIIVZincBlendeMixedAlloy','Eg_Gamma Eg_L Eg_X Delta_SO Ep F VBO a_300K a_c a_v b c11 c12 c44 luttinger1 luttinger2 luttinger3 thermal_expansion','properties','混合物重写的插值实现必须保留，不能错误引用二元参数方法；与基类不同的数据角色。','由IIIVZincBlendeTernary/Quaternary继承并绑定组分。'),
            op('openbandparams',ob+'iii_v_zinc_blende_strained','IIIVZincBlendeStrained001','__init__ strain_in_plane strain_out_of_plane substrate_a CBO Eg Eg_hh Eg_lh Eg_strain_shift VBO_hh VBO_lh VBO VBO_strain_shift','strain','给定衬底或应变的高层参数修正，不暴露内部所有分解式。'),
        ],
        'sources':[
            source('https://duarte-jfs.github.io/openbandparams/1.0/','1.0官方列出III-V二元/三元/四元材料及带隙、有效质量、晶格、Kane/Luttinger与带偏移参数。','material parameter literature','get_parameter get_unique_parameters','模型输入参数选择'),
            source('https://duarte-jfs.github.io/openbandparams/1.0/tutorial.html','教程显示默认300K、GaAs带隙1.422482eV、合金元素分数和指定a,T晶格匹配。','temperature composition target_lattice','Eg a IIIVZincBlendeTernary.__call__','温度与晶格匹配修复'),
            source('https://github.com/duarte-jfs/openbandparams/blob/v1.0/src/openbandparams/examples/Plot_Bandgap_vs_Composition_of_Ternary.py','发布脚本遍历AlGaAs(x)并计算Gamma/X/L三个能谷带隙，T显式为300K；说明材料预置对象的调用协议。','alloy_sweep valleys temperature','__call__ Eg_Gamma Eg_X Eg_L','组分扫描参数记录'),
            source('https://github.com/duarte-jfs/openbandparams/blob/v1.0/src/openbandparams/examples/Parameters.py','发布脚本通过get_unique_parameters遍历材料参数并读取name/description，不把所有动态数据名字视为独立函数。','parameter_identity metadata','get_unique_parameters','完整且去重的参数说明'),
        ],
        'entities':[entity('material_config','preset_id composition temperature_K substrate strain source_version','选择发布表材料→指定温度/组分→可选晶格匹配；配置变化使所有导出参数失效。'),entity('parameter_record','material_revision name value units description references dependencies','求值→检查单位/依赖引用→导出；自定义覆盖需记版本与原来源。'),entity('lattice_match','substrate temperature_K alloy_fraction lattice_A tolerance','设置目标→求组分→同温度复核→保存和恢复。')],
        'capabilities':['metadata','composition','properties','strain'],
        'bridges':[{'from':'versioned openbandparams presets and bound Parameter objects','to':'serializable material parameter record','contract':'预置对象为数据实体，引用实际类方法；T为K、a为Å、Eg/CBO/VBO为eV、质量为m0；III/V子晶格组分分别归一，不当作全原子摩尔分数。'}],
        'runtime_infrastructure':[{'reference':'runtime.openbandparams_presets','reason':'只加载所选发布版GaAs/InAs/InP/GaInAs预置数据对象；其__call__已纳入原始候选和选集，不添加伪造顶层函数。'}, {'reference':'runtime.parameters_oracle','reason':'固定Varshni系数、二元晶格的Vegard公式与JSON回读独立验收；数据字段序列化由基础设施完成。'}],
        'boundaries':['原始源码的__call__只对此包显式补入索引，其他包默认边界保持不变；装饰器返回MethodParameter仍按源码说明保留空值。','总Eg在1.0的直接文献为空；Gamma分支会递归给出依赖文献，不能虚构总Eg引用。','仅GaAs温度和GaInAs/InP无应变晶格匹配已运行；四元/应变能力未全部验证。','经验表及插值有材料/温度适用限制，本批没有量化外推不确定度或求解TCAD/k·p。'],
        'tasks':[
            task('repair_parameter_temperature','查询GaAs带隙及来源，发现T=0结果不符合300K需求；显式改为300K，断言带隙符合固定Varshni公式约1.422482142857eV，保留eV单位与Gamma分支文献。','GaAs发布表Eg0=1.519eV、alpha=0.0005405eV/K、beta=204K；目标300K。',[
                ('查询物性',[alloy+'Eg',alloy+'Eg_Gamma']),
                ('追踪单位和引用',[ob+'alloy.Alloy.get_parameter',ob+'alloy.Alloy.get_unique_parameters',ob+'parameter.MethodParameter.get_references'])],['0K失败、300K与独立公式atol1e-12；总Eg空引用如实保留。']),
            task('repair_alloy_matching_temperature','为800K的InP衬底选择晶格匹配GaInAs，发现使用默认300K匹配产生偏差；在同一800K温度重算组分，断言晶格差<1e-10Å、组分符合独立Vegard关系，拒绝x=1.1并保存恢复材料配置。','使用官方GaAs/InAs/InP晶格表，固定T=800K目标。',[
                ('查询目标并匹配',[alloy+'a',ternary+'__call__']),
                ('核对组分并导出',[ternary+'element_fraction',alloy+'a',alloy+'Eg',alloy+'CBO',ob+'iii_v_zinc_blende_mixed_alloy.IIIVZincBlendeMixedAlloy.VBO'])],['默认温度偏差约0.01616Å失败；修复Ga分数约0.4716414173；JSON恢复通过。'])],
        'runtime_scope':'版本化参数查询、真实可调用合金匹配与单位/引用/配置恢复；无下游物理求解。',
    })


if __name__=='__main__':
    main()
