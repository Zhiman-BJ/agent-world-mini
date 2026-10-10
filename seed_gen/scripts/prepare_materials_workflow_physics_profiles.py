"""Reviewed scene selections for workflow, recovery, Fermi statistics and phonons."""
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe

CORE = 'pymatgen-core'
PREFIX = 'seed_gen/scenario_collection/'


def core(module, name, methods, capability, reason, construction_reason=None):
    return symbol(CORE, 'pymatgen.' + module, name, methods, capability, reason, construction_reason)


def entity(name, attributes, lifecycle):
    return {'name': name, 'identity': name + '_id', 'attributes': attributes.split(), 'lifecycle': lifecycle}


def main():
    c = Collection()
    def source(url, evidence, entities, tools, tasks):
        return c.source(url, evidence, entities.split(), tools.split(), tasks.split())

    wf_symbols = [
        symbol('atomate2', 'atomate2.vasp.flows.core', 'RelaxBandStructureMaker', 'make', 'materials_flow',
               '弛豫、静态与均匀/线性能带的组合主入口；不重复选择单阶段Maker。',
               'dataclass生成构造；实际默认构造及make在固定样例中通过。'),
        symbol('atomate2', 'atomate2.vasp.flows.core', 'DoubleRelaxMaker', 'make from_relax_maker', 'materials_flow',
               '需要独立双弛豫或复用已有relax Maker时的高层入口。', 'dataclass生成构造，默认由组合Maker持有。'),
        symbol('atomate2', 'atomate2.vasp.flows.core', 'BandStructureMaker', 'make', 'materials_flow',
               '从已有结构/前序输出构建静态加能带分支。', 'dataclass构造，组合Maker也会生成此对象。'),
        *[symbol('atomate2', 'atomate2.vasp.powerups', name, None, 'configuration', why) for name, why in [
            ('update_user_incar_settings', '统一修改嵌套Maker的INCAR设置，修复分支参数不一致。'),
            ('update_user_kpoints_settings', '统一设置采样配置，改变配置后旧结果失效。'),
            ('add_metadata_to_flow', '记录材料和运行批次身份以跟踪来源。')]],
        symbol('jobflow', 'jobflow.core.flow', 'Flow',
               '__init__ jobs output job_uuids all_uuids graph iterflow update_kwargs update_maker_kwargs update_metadata update_config add_jobs remove_jobs',
               'graph', '维护DAG、依赖检查、局部修改和结果引用；去除仅可视化的入口。'),
        symbol('jobflow', 'jobflow.core.job', 'Job',
               '__init__ input_references input_uuids maker graph update_kwargs update_maker_kwargs update_metadata update_config as_dict',
               'graph', '检查任务输入依赖和Maker配置，执行后引用已解析，检查应发生在运行之前。'),
        symbol('jobflow', 'jobflow.core.reference', 'OutputReference', '__init__ resolve as_dict',
               'graph', '显式未来输出引用及存储解析；不自行复制结果状态。'),
        symbol('jobflow', 'jobflow.core.store', 'JobStore',
               '__init__ connect close count query query_one get_output from_file from_dict_spec',
               'execution', '持久化接口与本地MemoryStore构造；不暴露任意删库动作。'),
        symbol('jobflow', 'jobflow.core.job', 'job', None, 'execution', '将固定纯函数载荷包装成真实Job。'),
        symbol('jobflow', 'jobflow.managers.local', 'run_locally', None, 'execution', '低成本本地执行并验证依赖与输出。'),
        core('core.lattice', 'Lattice', '__init__ cubic matrix volume as_dict from_dict', 'structure', '材料输入的有单位晶格。'),
        core('core.structure', 'Structure', '__init__ make_supercell apply_strain', 'structure', '构造和修改输入结构。'),
        core('core.structure', 'IStructure', 'from_spacegroup copy as_dict from_dict to from_file', 'structure',
             '公开继承工厂、保存和恢复，结构变更使整条工作流结果失效。', '通过Structure实例和继承工厂获得。'),
    ]
    c.write({
        'scenario_id': '01.03.01',
        'description': '材料计算工作流环境：以结构和计算设置生成弛豫→静态→能带任务图，检查依赖、统一修改分支参数并查询运行结果。atomate2提供材料流程，jobflow负责图与存储，pymatgen-core维护结构。固定样例验证真实工作流构造及解析载荷的本地依赖执行；VASP电子结构计算需要外部程序和赝势。',
        'packages': [('atomate2', 'primary', '提供材料工作流高层Maker。'), ('jobflow', 'complement', 'atomate2硬依赖，补充图检查、本地执行和结果存储。'),
                     (CORE, 'complement', '两包共享的材料输入结构，避免另建原子对象。')],
        'symbols': wf_symbols,
        'sources': [
            source('https://materialsproject.github.io/atomate2/user/codes/vasp.html', '官方说明VASP可执行程序、PAW配置及默认PBEsol设置；不同输入集的总能量不能直接比较。', 'structure calculation_config workflow', 'Maker powerups', '构建分支并核查统一设置'),
            source('https://materialsproject.github.io/jobflow/tutorials/1-quickstart.html', '教程完整演示@job→OutputReference→Flow→run_locally→读取输出，执行顺序由依赖决定。', 'job flow store', 'job Flow run_locally', '反序列表仍按依赖执行'),
            source('https://materialsproject.github.io/jobflow/tutorials/3-defining-jobs.html', '定义作业的教程介绍函数参数、输出Schema、Response以及存储与附加数据。', 'job output', 'job Job', '保持任务输入输出和来源引用'),
        ],
        'entities': [entity('structure', 'lattice species coordinates revision', '构造/导入→修改→快照；变更后重新构造依赖的工作流。'),
                     entity('workflow', 'job_ids dependencies input_structure configuration revision', 'Maker构造→依赖检查→修改设置→执行；修改后旧执行记录保留但不再代表当前版本。'),
                     entity('run', 'job_uuid state output errors store_ref config_revision', '等待→运行→成功/失败→查询；每次重跑使用独立存储以隔离结果。')],
        'capabilities': ['materials_flow', 'configuration', 'graph', 'execution', 'structure'],
        'bridges': [{'from': 'pymatgen.Structure', 'to': 'atomate2 Maker -> jobflow Flow', 'contract': '结构携带晶格Å与元素信息；Maker返回带OutputReference的图，前序输出在执行时从JobStore解析。'}],
        'runtime_infrastructure': [
            {'reference': 'runtime.volume_fixture', 'kind': 'derived_callable', 'derived_from': 'jobflow.core.job.job', 'reason': '@job包装的固定解析体积载荷；非DFT计算。'},
            {'reference': 'runtime.checksum_fixture', 'kind': 'derived_callable', 'derived_from': 'jobflow.core.job.job', 'reason': '@job包装的结果倍率载荷，用于依赖与修复oracle。'},
            {'reference': 'runtime.storage', 'reason': 'JobStore.from_dict_spec实例化内存后端；Python集合检查DAG，JSON仅保存证据。'},
        ],
        'boundaries': ['未执行VASP；未生成POTCAR，也不宣称获得DFT能量、能带或DOS。', 'atomate2 phonons额外依赖要求phonopy<4，与本批phonopy4.5环境隔离。', '当前只验证选集中的任务链，未逐个执行全部参考接口。'],
        'tasks': [
            recipe('repair_materials_flow_settings', '为Si结构构造双弛豫、静态和两种能带分支；定位ENCUT=200的错误配置并统一修为520 eV，核验5个作业名称、所有分支参数和先驱引用均正确。', '固定Si晶格5.431 Å，目标ENCUT=520 eV。', [
                ('构造结构与流程', ['pymatgen.core.lattice.Lattice.cubic', 'pymatgen.core.structure.IStructure.from_spacegroup', 'atomate2.vasp.flows.core.RelaxBandStructureMaker.make']),
                ('修改并检查全部分支', ['atomate2.vasp.powerups.update_user_incar_settings', 'jobflow.core.flow.Flow.iterflow', 'jobflow.core.job.Job.input_uuids'])],
                ['5个作业按依赖闭合；每个ENCUT=520；错误200不能通过同一目标。'], PREFIX+'verify_materials_workflow.py'),
            recipe('execute_dependency_fixture', '创建体积与校验值两个依赖作业，以反序列表执行并查询存储；将错误倍率3修为2，断言两条记录和最终结果2。', '边长2，缩放0.5；校验目标2。', [
                ('构造依赖载荷', ['runtime.volume_fixture', 'runtime.checksum_fixture', 'jobflow.core.flow.Flow.__init__']),
                ('本地执行和取结果', ['jobflow.core.store.JobStore.from_dict_spec', 'jobflow.managers.local.run_locally', 'jobflow.core.store.JobStore.count', 'jobflow.core.store.JobStore.get_output'])],
                ['运行前child只依赖parent；运行后记录数2；错误输出3，修复输出2。'], PREFIX+'verify_materials_workflow.py')],
        'runtime_report': PREFIX+'runtime/materials_workflow/01.03.01.json',
        'runtime_scope': '5阶段atomate2流程构造/配置 + 真实jobflow执行两个解析载荷；没有VASP运行。',
    })

    recovery = [
        symbol('custodian', 'custodian.custodian', 'Custodian', '__init__ from_spec run run_interrupted', 'orchestration', '有界重试与修复记录主入口。'),
        symbol('custodian', 'custodian.custodian', 'Job', 'setup run postprocess terminate name', 'orchestration', '插件生命周期；固定样例使用派生的日志回放Job。', '抽象基类，由具名Job实现生命周期，不直接实例化。'),
        *[symbol('custodian', 'custodian.vasp.handlers', name, '__init__ check correct', 'diagnosis', why) for name, why in [
            ('NonConvergingErrorHandler', '读取OSZICAR和INCAR以发现反复达到NELM，并修改算法。'),
            ('VaspErrorHandler', '常见VASP输出错误的统一入口，不选细分长尾修复器。'),
            ('WalltimeHandler', '时间预算导致的安全停止与恢复配置；未在样例中验证真实调度器。')]],
        symbol('custodian', 'custodian.vasp.interpreter', 'VaspModder', '__init__ apply_actions', 'repair', '应用可审计的文件/字典修复指令。'),
        *[symbol('custodian', 'custodian.vasp.validators', name, '__init__ check', 'validation', '作业完成后的独立产物完整性检查。') for name in ('VaspFilesValidator', 'VasprunXMLValidator')],
        core('io.vasp.inputs', 'Incar', '__init__ get as_dict from_dict copy get_str write_file from_file from_str diff check_params', 'state', '读取、比较、检查及保存输入配置。'),
        core('io.vasp.inputs', 'Poscar', '__init__ from_file from_str write_file as_dict from_dict', 'state', '结构输入与修复后重启文件。'),
        core('io.vasp.outputs', 'Oszicar', '__init__ all_energies final_energy as_dict', 'diagnosis', '显式查看能量轨迹；退出成功不自动等于SCF收敛。'),
        core('core.lattice', 'Lattice', '__init__ cubic matrix volume', 'state', '日志回放fixture的合法结构输入。'),
        core('core.structure', 'Structure', '__init__', 'state', 'Poscar共享输入结构。'),
    ]
    c.write({
        'scenario_id': '01.03.02',
        'description': '材料作业失败诊断与有界恢复环境：维护输入文件、运行尝试、错误处理策略和修复记录，以custodian检测VASP错误并按规则修改配置后重试。pymatgen-core解析输入和日志。固定样例以合成SCF日志回放验证真实handler与重试循环，实际电子结构收敛和集群调度仍需外部验证。',
        'packages': [('custodian', 'primary', '直接管理错误检测、输入修复、有限重试与完成验证。'), (CORE, 'complement', '解析和保存custodian操作的同一套INCAR/POSCAR/OSZICAR。'),
                     ('atomate2', 'excluded', '高层流程另由01.03.01承担；当前恢复闭环直接用custodian，避免重复封装。')],
        'symbols': recovery,
        'sources': [
            source('https://materialsproject.github.io/custodian/', '官方插件案例用Job和ErrorHandler管理运行、检测、修复与重启。', 'job attempt handler', 'Custodian Job', '错误后修复并重新运行'),
            source('https://materialsproject.github.io/custodian/custodian.custodian.html', 'API说明区分监控与结束后检查，并明确max_errors及失败异常、完整性validators。页面签名较旧，以固定发布源码为准。', 'retry_policy correction_log', 'Custodian.run', '预算不足被拒绝'),
            source('https://materialsproject.github.io/custodian/custodian.vasp.html', 'VASP文档索引列出NonConvergingErrorHandler、VaspModder和文件/XML validators；具体行为以发布源码及运行fixture复核。', 'INCAR OSZICAR', 'check correct apply_actions', '修复算法并验证产物'),
        ],
        'entities': [entity('job', 'directory input_revision handler_policy retry_budget status', '准备输入→尝试→检测→修复→重试/终止；重置须恢复输入和日志。'),
                     entity('attempt', 'attempt_index input_snapshot output_log errors corrections', '追加不可变审计记录；旧尝试不覆盖，新输入不能沿用旧收敛结论。')],
        'capabilities': ['orchestration', 'diagnosis', 'repair', 'validation', 'state'],
        'bridges': [{'from': 'custodian handlers/actions', 'to': 'pymatgen INCAR/POSCAR/OSZICAR', 'contract': '同一作业目录内读写；修复记录绑定输入版本，重启后重新检查新日志。'}],
        'runtime_infrastructure': [{'reference': 'runtime.ReplayedSCFJob', 'reason': '自定义Custodian.Job子类仅输出固定SCF轨迹，无电子结构求解；临时目录隔离每次任务。'},
                                   {'reference': 'runtime.mapping', 'reason': 'Incar继承dict的字段读写、Python文件保存与异常断言属于基础设施。'}],
        'boundaries': ['没有VASP/POTCAR；合成日志通过仅证明诊断和恢复控制链。', 'Walltime与完整XML validator列为参考能力，未运行外部调度/真实DFT产物。'],
        'tasks': [
            recipe('repair_scf_log_configuration', '读取反复达到NELM的SCF日志，使用NonConvergingErrorHandler和custodian将ALGO Fast修为Normal并重试；断言两次尝试、一次修复及新日志不再触发原错误。', 'NELM=3；Fast每离子步3次电子步，Normal两次，固定3个离子步。', [
                ('准备和读取输入', ['pymatgen.io.vasp.inputs.Incar.__init__', 'pymatgen.io.vasp.inputs.Incar.write_file', 'pymatgen.io.vasp.inputs.Poscar.__init__', 'pymatgen.io.vasp.inputs.Poscar.write_file']),
                ('检测、修复和重试', ['custodian.vasp.handlers.NonConvergingErrorHandler.__init__', 'custodian.vasp.handlers.NonConvergingErrorHandler.check', 'custodian.vasp.handlers.NonConvergingErrorHandler.correct', 'custodian.custodian.Custodian.__init__', 'custodian.custodian.Custodian.run']),
                ('检查最终设置', ['pymatgen.io.vasp.inputs.Incar.from_file', 'custodian.vasp.handlers.NonConvergingErrorHandler.check'])],
                ['ALGO=Normal；attempts=2；corrections错误为Non-converging job；新日志check=False。'], PREFIX+'verify_materials_workflow.py'),
            recipe('repair_retry_budget', '在同一故障输入上验证max_errors=1不能完成恢复；将预算改为3并从原始输入重置运行，断言预算失败被捕获且修复运行两次后通过检测。', '原始ALGO=Fast，禁止把已修好的INCAR带入第二个任务。', [
                ('重置原始设置', ['pymatgen.io.vasp.inputs.Incar.__init__', 'pymatgen.io.vasp.inputs.Incar.write_file']),
                ('改变预算并运行', ['custodian.custodian.Custodian.__init__', 'custodian.custodian.Custodian.run']),
                ('重新检查输出', ['custodian.vasp.handlers.NonConvergingErrorHandler.check'])],
                ['预算1抛CustodianError且只运行1次；预算3运行2次后check=False。'], PREFIX+'verify_materials_workflow.py')],
        'runtime_report': PREFIX+'runtime/materials_workflow/01.03.02.json', 'runtime_scope': '真实NonConvergingErrorHandler/VaspModder/重试循环，外部程序用固定日志回放。',
    })

    fermi_methods = [
        ('defect_charge_state', 'DefectChargeState', '__init__ energy charge degeneracy name fixed_concentration from_dict as_dict get_formation_energy', 'defect', '定义电荷态、形成能与固定浓度。'),
        ('defect_species', 'DefectSpecies', '__init__ name nsites charge_states charge_state_by_name charges fixed_concentration from_dict as_dict get_formation_energies get_transition_level_and_energy', 'defect', '按种类组织电荷态与位点数；3.0构造输入是序列。'),
        ('dos', 'DOS', '__init__ dos edos bandgap nelect from_dict as_dict normalise_dos emin emax carrier_concentrations scissored', 'dos', '给定DOS的归一、带隙调整和载流子统计，不引入外部DFT解析器。'),
        ('defect_system', 'DefectSystem', '__init__ volume dos temperature label convergence_tolerance defect_species defect_species_names from_dict defect_species_by_name result get_sc_fermi q_tot get_transition_levels site_percentages defect_concentrations_at_fermi_level as_dict', 'solve', '固定温度快照、电中性求解、位点占据和保存恢复。'),
        ('defect_system', 'DefectSystemFactory', '__init__ at', 'sweep', '每个温度新建独立快照，禁止按2.x方式原地修改温度。'),
        ('defect_system_result', 'DefectSystemResult', 'p0 n0 charge_state_concentrations concentrations_per_cell concentrations as_dict', 'observe', '统一每晶胞和cm^-3的结果视图。'),
    ]
    fermi = [symbol('py-sc-fermi', 'py_sc_fermi.'+mod, name, methods, cap, why,
                    '通过DefectSystem.result生成的不可变dataclass；fermi_energy等是数据字段，不能伪造为方法。' if name=='DefectSystemResult' else None)
             for mod, name, methods, cap, why in fermi_methods]
    c.write({
        'scenario_id': '01.04.04',
        'description': '半导体缺陷与载流子浓度环境：给定形成能、电荷态、电子DOS和晶胞体积，在温度与固定掺杂条件下自洽求费米能级并检查电中性。使用py-sc-fermi 3.0.0的固定温度快照和统一浓度视图；低浓度模型DOS样例可独立积分验证，不代表已计算真实材料的缺陷形成能。',
        'packages': [('py-sc-fermi', 'primary', '浓度和电中性主实现，使用最新官方GitHub稳定版3.0.0。'),
                     ('doped', 'excluded', '其热力学包装与本场景浓度主链重叠，缺陷生成及DFT结果解析另场景处理。')],
        'symbols': fermi,
        'sources': [
            source('https://py-sc-fermi.readthedocs.io/en/latest/tutorial.html', '完整教程展示电荷态序列、DOS、体积、DefectSystem以及Factory.at温度快照和冻结浓度。', 'defect DOS system result', 'DefectSystem Factory.at', '温度扫描与固定掺杂'),
            source('https://github.com/bjmorgan/py-sc-fermi/blob/3.0.0/README.md', '固定发布版说明输入为预计算形成能/DOS/体积，以电中性求自洽费米能；3.0支持位点排斥统计，低浓度下回到稀释模型。', 'formation_energy concentration', 'get_sc_fermi', '验证电中性及物理假设'),
            source('https://github.com/bjmorgan/py-sc-fermi/blob/3.0.0/py_sc_fermi/defect_system_result.py', '结果类为frozen dataclass，由system.result构造；规范浓度存为每晶胞，cm^-3视图按1e24/volume换算。', 'result volume units', 'concentrations_per_cell concentrations', '修复错误体积单位'),
        ],
        'entities': [entity('defect_species', 'name charge_states formation_energies degeneracy nsites fixed_concentration', '构造→检查参数；每个系统复制定义，后续改变不能污染已有快照。'),
                     entity('dos', 'energy_grid density bandgap electrons normalization', '导入/构造→归一/剪刀修正→新版本；输入改变需重算。'),
                     entity('system', 'species DOS volume_A3 temperature_K tolerance result', 'Factory创建温度快照→求解→电中性检查→保存；新条件新快照。'),
                     entity('result', 'fermi_energy_eV n0 p0 concentrations_per_cell concentrations_cm3 occupancy', '只读结果，保留条件与单位，禁止混合不同晶胞归一。')],
        'capabilities': ['defect', 'dos', 'solve', 'sweep', 'observe'],
        'bridges': [{'from': 'DOS + DefectSpecies', 'to': 'DefectSystemFactory.at -> DefectSystem.result', 'contract': '形成能和E_F相对VBM，以eV计；体积Å^3；每晶胞浓度乘1e24/体积得到cm^-3。'}],
        'runtime_infrastructure': [{'reference': 'runtime.numeric_oracle', 'reason': 'NumPy构造固定对称DOS及独立Fermi-Dirac积分；SciPy仅提供Boltzmann常数，不隐藏第二套求解器。'}],
        'boundaries': ['PyPI仍为2.2.2；本环境从3.0.0发布源码安装，不混用旧API。', '未运行真实DFT；高占据/复杂位点池和元素池不在当前低浓度fixture内。'],
        'tasks': [
            recipe('intrinsic_fermi_temperature', '建立带隙2 eV的对称DOS，在400和800 K分别求本征载流子浓度；断言费米能为1 eV、电子空穴浓度相等，升温后浓度增加超过100倍。', '能量-5到7 eV，0.01 eV步长；禁带0到2 eV，体积100 Å³，无缺陷。', [
                ('构造DOS和温度工厂', ['py_sc_fermi.dos.DOS.__init__', 'py_sc_fermi.defect_system.DefectSystemFactory.__init__']),
                ('逐温度求解和读取', ['py_sc_fermi.defect_system.DefectSystemFactory.at', 'py_sc_fermi.defect_system.DefectSystem.result', 'py_sc_fermi.defect_system_result.DefectSystemResult.n0', 'py_sc_fermi.defect_system_result.DefectSystemResult.p0'])],
                ['E_F=1±1e-7 eV；n=p相对误差2e-6；n800>100*n400。'], PREFIX+'verify_materials_fermi.py'),
            recipe('repair_concentration_units', '加入每晶胞1e-4的固定单电荷施主，发现体积10 Å³导致浓度偏大；修正为100 Å³，核验约1e18 cm^-3、电中性、独立占据积分及保存恢复一致。', '600 K，沿用对称DOS和固定施主；目标浓度1e18 cm^-3。', [
                ('创建施主和错误/正确系统', ['py_sc_fermi.defect_charge_state.DefectChargeState.__init__', 'py_sc_fermi.defect_species.DefectSpecies.__init__', 'py_sc_fermi.defect_system.DefectSystem.__init__']),
                ('求解并观察单位一致性', ['py_sc_fermi.defect_system.DefectSystem.result', 'py_sc_fermi.defect_system_result.DefectSystemResult.n0', 'py_sc_fermi.defect_system_result.DefectSystemResult.p0']),
                ('保存与恢复', ['py_sc_fermi.defect_system.DefectSystem.as_dict', 'py_sc_fermi.defect_system.DefectSystem.from_dict'])],
                ['错误体积未达到目标；修复n误差<1e-5；电荷不平衡<1e-6；独立积分<1e-6；恢复<1e-12。'], PREFIX+'verify_materials_fermi.py')],
        'runtime_report': PREFIX+'runtime/materials_fermi/01.04.04.json', 'runtime_scope': '对称模型DOS、本征温度扫描及固定施主电中性/单位修复；非材料特定DFT。',
    })

    phonon = [
        symbol('phonopy', 'phonopy.structure.atoms', 'PhonopyAtoms', '__init__ cell positions scaled_positions symbols numbers masses volume copy totuple get_yaml_lines formula', 'structure', '单套结构和质量表示，不重复暴露ASE/pymatgen转换。'),
        symbol('phonopy', 'phonopy.api_phonopy', 'Phonopy', '__init__ primitive unitcell supercell supercell_matrix primitive_matrix dataset displacements force_constants forces nac_params supercells_with_displacements masses generate_displacements produce_force_constants symmetrize_force_constants get_dynamical_matrix_at_q run_band_structure run_mesh run_qpoints run_total_dos run_projected_dos run_thermal_properties save copy', 'solve', '有限位移/给定力常数→声子/DOS/热力学主链；优先4.5结果对象API，不选将弃用的get_*_dict。'),
        symbol('phonopy', 'phonopy.phonon.qpoints', 'QpointsPhonon', 'qpoints frequencies eigenvalues eigenvectors group_velocities write_hdf5 write_yaml', 'observe', 'q点声子结果、模态及保存。', '由Phonopy.run_qpoints返回，无需直接构造。'),
        symbol('phonopy', 'phonopy.phonon.dos', 'Dos', 'frequency_points', 'observe', 'DOS公共频率轴，来源在基类。', '由run_total_dos或run_projected_dos生成的对象继承。'),
        symbol('phonopy', 'phonopy.phonon.dos', 'TotalDos', 'dos write', 'observe', '与频率轴一一对应的总DOS密度及输出。', '由Phonopy.run_total_dos返回。'),
        symbol('phonopy', 'phonopy.phonon.thermal_properties', 'ThermalProperties', 'temperatures free_energy entropy heat_capacity zero_point_energy number_of_integrated_modes number_of_modes write_yaml', 'observe', '带单位和温度条件的热力学输出；不重复暴露内部run。', '由Phonopy.run_thermal_properties返回。'),
        symbol('phonopy', 'phonopy.cui.load', 'load', None, 'persistence', '从包含力常数的YAML恢复可重算模型。'),
    ]
    c.write({
        'scenario_id': '01.05.01',
        'description': '谐波晶格动力学环境：以晶胞、质量、超胞和力常数为状态，计算q点/色散、声子DOS与热力学性质，定位虚频并修正输入，保存可恢复的模型。以phonopy作为主实现；固定弹簧链拥有解析频率与DOS归一oracle，不能据此声称完成真实硅的第一性原理声子计算。',
        'packages': [('phonopy', 'primary', '谐波模型、位移/力常数、声子和热力学高层API。'),
                     ('phono3py', 'excluded', '三阶散射/热输运属于后续非谐场景，本场景无缺口需要其补充。')],
        'symbols': phonon,
        'sources': [
            source('https://phonopy.github.io/phonopy/phonopy-module.html', '4.5文档说明三种晶胞、位移和力常数输入、run_*结果对象，并强调修改输入会清除派生结果。', 'unitcell supercell force_constants result', 'Phonopy run_qpoints run_total_dos', '修改后重算并检查缓存失效'),
            source('https://phonopy.github.io/phonopy/examples.html', '官方Si等案例展示从外部计算原子力到声子计算；页面提醒输出可能比包示例旧，不将示例历史版本当当前发布号。', 'force_data dispersion DOS', 'produce_force_constants run_band_structure', '外部力数据和本地后处理边界'),
            source('https://phonopy.github.io/phonopy/formulation.html', '公式给出力常数为负的力导数、质量加权动力学矩阵和本征频率关系。', 'force_constants mass frequency', 'run_qpoints', '解析弹簧频率及符号错误验收'),
        ],
        'entities': [entity('model', 'unitcell supercell primitive masses force_constants nac_params revision', '构造→提供位移/力或力常数→计算；任何输入变化使旧声子和热学结果失效。'),
                     entity('spectrum', 'model_revision qpoints eigenvalues frequencies eigenvectors', '指定q点/路径→求解→检查虚频与声学模→保存。'),
                     entity('thermodynamics', 'model_revision mesh temperatures DOS free_energy heat_capacity', '配置网格/温度→积分→归一检查；与使用的力常数版本绑定。')],
        'capabilities': ['structure', 'solve', 'observe', 'persistence'],
        'bridges': [{'from': 'PhonopyAtoms + FC(N,N,3,3)', 'to': 'Phonopy -> qpoints/DOS/thermal result', 'contract': '长度Å、质量u、力常数eV/Å²、频率THz；修改force_constants后重跑相应run_*，结果对象只用于其生成版本。'}],
        'runtime_infrastructure': [{'reference': 'runtime.spring_oracle', 'reason': 'NumPy构造周期弹簧矩阵和解析色散，SciPy提供单位常数；不是另一套隐式物理后端。'}],
        'boundaries': ['固定模型为三胞周期弹簧链，Si仅为元素标签，质量人为指定28 u，非真实硅声子谱。', '未运行外部DFT原子力；非谐散射、电子声子耦合不在本场景。', 'phonopy4.5与atomate2 phonons可选依赖隔离。'],
        'tasks': [
            recipe('harmonic_band_dos', '建立三胞周期弹簧模型并计算q=0、0.25、0.5的频率、DOS和热容；检查解析色散、声学和规则、DOS积分3以及保存恢复后的频率一致。', 'K=2 eV/Å²，质量28 u，晶格5.43 Å，超胞diag(3,1,1)。', [
                ('构造结构与力常数模型', ['phonopy.structure.atoms.PhonopyAtoms.__init__', 'phonopy.api_phonopy.Phonopy.__init__', 'phonopy.api_phonopy.Phonopy.force_constants']),
                ('求频率和DOS/热力学', ['phonopy.api_phonopy.Phonopy.run_qpoints', 'phonopy.phonon.qpoints.QpointsPhonon.frequencies', 'phonopy.api_phonopy.Phonopy.run_band_structure', 'phonopy.api_phonopy.Phonopy.run_mesh', 'phonopy.api_phonopy.Phonopy.run_total_dos', 'phonopy.phonon.dos.Dos.frequency_points', 'phonopy.phonon.dos.TotalDos.dos', 'phonopy.api_phonopy.Phonopy.run_thermal_properties', 'phonopy.phonon.thermal_properties.ThermalProperties.free_energy', 'phonopy.phonon.thermal_properties.ThermalProperties.heat_capacity']),
                ('保存并恢复', ['phonopy.api_phonopy.Phonopy.save', 'phonopy.cui.load.load'])],
                ['三支简并频率与解析式相符rtol3e-6；DOS积分3±1e-6；热容为正；恢复频率误差<1e-8 THz。'], PREFIX+'verify_materials_phonon.py'),
            recipe('repair_unstable_force_constants', '将符号错误的力常数导入同一模型并发现虚频；恢复正确弹簧常数后重新求解，断言虚频消失并回到固定解析频率。', '同一弹簧模型与q=(0.25,0,0)，错误FC=-FC_target。', [
                ('修改输入并重新求解', ['phonopy.api_phonopy.Phonopy.force_constants', 'phonopy.api_phonopy.Phonopy.run_qpoints']),
                ('比较错误与修复频率', ['phonopy.phonon.qpoints.QpointsPhonon.frequencies'])],
                ['错误频率<-1 THz；恢复>0且与5.9088328653 THz相符rtol3e-6。'], PREFIX+'verify_materials_phonon.py')],
        'runtime_report': PREFIX+'runtime/materials_phonon/01.05.01.json', 'runtime_scope': '解析弹簧链频率、DOS、热学及YAML往返；非DFT。',
    })


if __name__ == '__main__':
    main()
