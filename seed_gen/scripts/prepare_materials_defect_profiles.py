"""Jointly reviewed point-defect scenes; source APIs remain unchanged subsets."""
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe
from seed_gen.scripts.prepare_materials_workflow_physics_profiles import CORE, PREFIX, core, entity

PAD = 'pymatgen-analysis-defects'


def main():
    c = Collection()
    def source(url, evidence, entities, tools, tasks):
        return c.source(url, evidence, entities.split(), tools.split(), tasks.split())
    def pad(module, name, methods, capability, reason, construction_reason=None):
        return symbol(PAD, 'pymatgen.analysis.defects.'+module, name, methods, capability, reason, construction_reason)
    def doped(module, name, methods, capability, reason, construction_reason=None):
        return symbol('doped', 'doped.'+module, name, methods, capability, reason, construction_reason)
    def snb(module, name, methods, capability, reason):
        return symbol('shakenbreak', 'shakenbreak.'+module, name, methods, capability, reason)
    def geometry():
        return [
            core('core.lattice', 'Lattice', '__init__ cubic matrix volume get_distance_and_image as_dict from_dict', 'structure', '有单位晶格、周期最短距离和保存。'),
            core('core.structure', 'Structure', '__init__ make_supercell remove_sites replace translate_sites', 'structure', '受控修改宿主或候选，原始结构快照独立保留。'),
            core('core.structure', 'IStructure', 'from_spacegroup copy lattice frac_coords volume as_dict from_dict to from_file', 'structure', '共享宿主/缺陷结构，公开继承工厂及持久化。', '由Structure实例与继承工厂获得，不额外构造不可变状态。'),
            core('core.structure', 'SiteCollection', 'composition num_sites is_valid distance_matrix', 'structure', '结构数量、组成及重叠检查。', '抽象基类查询由Structure继承，不直接构造。'),
            core('core.composition', 'Composition', '__init__ get_el_amt_dict reduced_formula almost_equals', 'structure', '以精确组成差与基体核验缺陷类型。'),
            core('core.sites', 'PeriodicSite', '__init__ frac_coords coords distance as_dict from_dict', 'structure', '缺陷位点有独立身份，保留晶胞坐标约定。'),
        ]
    enum_symbols = [
        *[pad('generators', name, '__init__ generate', 'enumeration', reason) for name, reason in [
            ('VacancyGenerator', '按对称不等价位点枚举空位，并支持限定移除元素。'),
            ('SubstitutionGenerator', '按宿主元素到掺杂元素映射生成对称不等价替位。'),
            ('InterstitialGenerator', '从已知分数坐标生成间隙并排除与宿主碰撞点。'),
            ('VoronoiInterstitialGenerator', '缺少先验坐标时的几何候选生成，不据此声明穷尽全部稳定间隙。')]],
        pad('core', 'Defect', 'get_charge_states get_supercell_structure defect_type', 'supercell', '统一缺陷身份与模拟超胞分离，约束电荷列表和超胞大小。', '由具体generator生成Vacancy/Substitution/Interstitial，继承公共方法。'),
        pad('core', 'Vacancy', 'get_multiplicity name defect_site defect_structure element_changes', 'observe', '按来源提供空位等价数、移除位点和组成变化。', 'VacancyGenerator生成；继承Defect构造，不伪造子类__init__。'),
        pad('core', 'Substitution', 'get_multiplicity name defect_site defect_structure element_changes', 'observe', '替位身份、等价数和宿主/掺杂元素变化。', '由SubstitutionGenerator生成，无需同时暴露重复构造。'),
        pad('core', 'Interstitial', 'get_multiplicity name defect_structure element_changes', 'observe', '间隙候选的身份与加原子计数。', '由InterstitialGenerator或VoronoiInterstitialGenerator生成。'),
        *geometry(),
    ]
    c.write({
        'scenario_id': '01.04.01',
        'description': '半导体点缺陷几何枚举环境：从宿主晶胞生成对称不等价空位、替位及间隙候选，记录缺陷身份、位点等价数、电荷列表和模拟超胞，按组成和周期距离检查错误并保存。pymatgen-analysis-defects负责枚举，pymatgen-core承载结构；固定样例不评估形成能或电荷态稳定性。',
        'packages': [(PAD, 'primary', '统一缺陷定义、对称枚举和可控超胞。'), (CORE, 'complement', '原子结构、位点、组成、距离和保存接口。'),
                     ('doped', 'excluded', '其高层全缺陷生成与当前基础枚举重叠；当前选择更小的按类型生成接口，doped留给重构/形成能场景。')],
        'symbols': enum_symbols,
        'sources': [
            source('https://materialsproject.github.io/pymatgen-analysis-defects/content/defining-defects.html', 'GaN替位/空位示例强调缺陷定义独立于模拟超胞，以结构和位点追踪同一缺陷，并比较等价替位。', 'bulk defect supercell', 'Vacancy Substitution get_supercell_structure', '按身份和对称性去重'),
            source('https://github.com/materialsproject/pymatgen-analysis-defects/blob/v2026.3.20/pymatgen/analysis/defects/generators.py', '发布源码按对称等价组生成空位/替位，间隙生成器按min_dist过滤碰撞坐标。', 'site equivalence insertion', 'VacancyGenerator SubstitutionGenerator InterstitialGenerator', '修复错误元素和碰撞坐标'),
            source('https://doped.readthedocs.io/en/latest/generation_tutorial.html', 'CdTe完整生成示例显示空位/替位/间隙、猜测电荷和Wyckoff等价数，并提醒超胞/电荷默认值需按体系调整。', 'charge_states supercell multiplicity', 'DefectsGenerator', '作为候选包比较依据，避免重复枚举状态'),
        ],
        'entities': [entity('host', 'lattice species coordinates revision', '构造/导入→校验→锁定宿主版本，修改后重做缺陷枚举。'),
                     entity('defect', 'type site species_change multiplicity charge_states host_revision', '按类型生成→对称去重→过滤碰撞；缺陷身份不随超胞大小改变。'),
                     entity('supercell', 'defect_id matrix coordinates composition volume revision', '从缺陷生成→核验原子计数/距离→保存；变化后旧能量输入失效。')],
        'capabilities': ['enumeration', 'supercell', 'observe', 'structure'],
        'bridges': [{'from': 'pymatgen-core Structure', 'to': 'defect generator -> Defect -> supercell Structure', 'contract': '宿主与缺陷共享元素/晶格定义；超胞diag(2,2,2)体积×8，只改变一个缺陷的元素计数。'}],
        'runtime_infrastructure': [{'reference': 'runtime.geometry_oracle', 'reason': 'Python迭代generator、字典比较组成、NumPy矩阵和坐标相等断言；没有隐藏的缺陷后端。'}],
        'boundaries': ['固定样例只验证显式间隙位置；Voronoi候选入口已纳入参考但未实跑。', '电荷态列表是输入/启发信息，不能据此断言热力学稳定。', '氧化态在fixture显式设0以隔离几何任务；不是GaAs物理氧化态结论。'],
        'tasks': [
            recipe('enumerate_and_repair_vacancy', '枚举GaAs常规胞的两类不等价空位，核验各等价数4；发现选错As空位后改为Ga空位并生成2×2×2超胞，断言Ga31As32、63原子和体积8倍，保存恢复结构。', '空间群216、a=5.653 Å，原胞8原子；目标单Ga空位。', [
                ('创建宿主并枚举', ['pymatgen.core.lattice.Lattice.cubic', 'pymatgen.core.structure.IStructure.from_spacegroup', 'pymatgen.analysis.defects.generators.VacancyGenerator.__init__', 'pymatgen.analysis.defects.generators.VacancyGenerator.generate', 'pymatgen.analysis.defects.core.Vacancy.get_multiplicity']),
                ('选择缺陷并生成超胞', ['pymatgen.analysis.defects.core.Defect.get_supercell_structure', 'pymatgen.core.structure.SiteCollection.composition', 'pymatgen.core.composition.Composition.get_el_amt_dict']),
                ('保存恢复', ['pymatgen.core.structure.IStructure.as_dict', 'pymatgen.core.structure.IStructure.from_dict', 'pymatgen.core.structure.IStructure.to'])],
                ['两类空位且multiplicity=4；错误Ga32As31被拒绝；目标Ga31As32，体积×8。'], PREFIX+'verify_materials_defects.py'),
            recipe('substitution_and_interstitial', '将Ga替为Al并生成Ga31Al1As32超胞；验证与宿主重合的H间隙被过滤，再用(0.5,0.5,0.5)生成H间隙并断言Ga32As32H。', '同一GaAs宿主，min_dist=1 Å，超胞2×2×2。', [
                ('生成对称不等价替位', ['pymatgen.analysis.defects.generators.SubstitutionGenerator.__init__', 'pymatgen.analysis.defects.generators.SubstitutionGenerator.generate', 'pymatgen.analysis.defects.core.Substitution.get_multiplicity']),
                ('过滤及修复间隙坐标', ['pymatgen.analysis.defects.generators.InterstitialGenerator.__init__', 'pymatgen.analysis.defects.generators.InterstitialGenerator.generate']),
                ('检查超胞组成', ['pymatgen.analysis.defects.core.Defect.get_supercell_structure', 'pymatgen.core.composition.Composition.get_el_amt_dict'])],
                ['一个替位等价类、等价数4；碰撞候选0，修复后1；两种超胞组成准确。'], PREFIX+'verify_materials_defects.py')],
        'runtime_report': PREFIX+'runtime/materials_defects/01.04.01.json', 'runtime_scope': '对称枚举、单点组成变化、显式间隙碰撞排除和结构恢复。',
    })

    formation = [
        doped('core', 'Vacancy', '__init__', 'defect', '给定宿主与位点定义同一空位的不同电荷计算。'),
        doped('core', 'Defect', 'as_dict to_json from_json get_charge_states element_changes', 'defect', '继承的缺陷身份、元素变化与保存，避免重复选择底层构造。', '由doped.Vacancy构造。'),
        pad('core', 'Vacancy', 'defect_structure', 'defect', 'doped.Vacancy直接继承的结构属性；只补此数据出口，不选择重复形成能分析器。', '由doped.Vacancy构造并通过多继承获得。'),
        doped('core', 'DefectEntry', 'to_json from_json as_dict from_dict get_ediff corrected_energy formation_energy bulk_entry_energy sc_entry_energy', 'energy', '统一形成能、修正项和持久化；不混入本场景不需要的另一套载流子求解。', 'dataclass构造接受defect、charge_state及ComputedStructureEntry，固定样例已实跑。'),
        doped('thermodynamics', 'DefectThermodynamics', '__init__ as_dict from_dict to_json from_json add_entries defect_entries chempots el_refs defect_names all_stable_entries all_unstable_entries get_formation_energies get_formation_energy get_transition_levels', 'transitions', '多电荷态形成能下包络、稳定态和转变能级主实现；改变输入后须重新构建分析对象。'),
        core('core.entries', 'ComputedStructureEntry', '__init__ structure as_dict from_dict copy', 'state', '明确能量来源和超胞结构，禁用不同基准直接混比。'),
        core('core.entries', 'ComputedEntry', 'energy uncorrected_energy correction', 'state', '结构能量条目继承的能量和修正查询。', '由ComputedStructureEntry构造，基类不重复实例化。'),
        core('core.lattice', 'Lattice', '__init__ cubic matrix volume', 'state', '固定输入结构与体积。'),
        core('core.structure', 'Structure', '__init__ remove_sites', 'state', '缺陷结构的可控输入。'),
        core('core.structure', 'IStructure', 'from_spacegroup copy as_dict from_dict lattice', 'state', '构造宿主、保存和恢复。', 'Structure实例继承。'),
        core('core.sites', 'PeriodicSite', 'frac_coords', 'state', '传递缺陷分数位置，防止不同位点的电荷态混组。', '从宿主Structure位点索引取得。'),
    ]
    c.write({
        'scenario_id': '01.04.03',
        'description': '缺陷形成能和电荷转变环境：维护宿主与缺陷总能量、缺陷身份、电荷、修正项、化学势和VBM参考，计算形成能随费米能的下包络并识别稳定态。doped作为热力学主实现，pymatgen-core提供能量/结构条目；固定输入能量拥有解析直线oracle，未运行DFT或实际有限尺寸修正。',
        'packages': [('doped', 'primary', '形成能、稳定电荷态、转变能级和可序列化结果。'), (CORE, 'complement', '宿主/缺陷共享结构及带能量条目。'),
                     (PAD, 'complement', 'doped硬依赖，只保留被直接调用的继承属性Vacancy.defect_structure；其重复形成能分析器不选。')],
        'symbols': formation,
        'sources': [
            source('https://materialsproject.github.io/pymatgen-analysis-defects/content/formation-energy.html', '形成能公式明确缺陷与bulk总能差、原子数变化化学势、qEF和有限尺寸修正，强调一致超胞。', 'energy_entry chemical_potential charge', 'formation_energy', '独立线性公式检查'),
            source('https://doped.readthedocs.io/en/latest/thermodynamics_tutorial.html', '教程使用DefectThermodynamics并强调VBM和band_gap参考、稳定及亚稳态转变能级。在线页面含新版plot_transition_levels，而固定3.2.1未定义它，故未选择。', 'thermodynamics band_edges stable_state', 'get_transition_levels get_formation_energy', '识别转变前后的稳定电荷'),
            source('https://doped.readthedocs.io/en/latest/parsing_tutorial.html', '实际CdTe解析需要vasprun和OUTCAR/LOCPOT，分别对应eFNV/FNV；还检查INCAR、KPOINTS、POTCAR一致性。', 'calculation_metadata corrections', 'DefectEntry', '将DFT解析边界和已提供能量分析分开'),
        ],
        'entities': [entity('defect_entry', 'defect_id charge_state bulk_energy defect_energy corrections VBM source', '从能量/结构创建→核查基准→保存；不同电荷保持相同缺陷身份。'),
                     entity('chemical_condition', 'chemical_potentials elemental_references band_gap VBM revision', '设定条件→构造分析；化学势或参考改变使形成能和稳定态结果失效。'),
                     entity('formation_diagram', 'entry_ids condition_revision fermi_grid formation_energies transitions stable_entries', '形成能→下包络→转变能级→查询/保存；只读绑定当前输入版本。')],
        'capabilities': ['defect', 'energy', 'transitions', 'state'],
        'bridges': [{'from': 'ComputedStructureEntry + doped Defect', 'to': 'DefectEntry -> DefectThermodynamics', 'contract': '总能量/化学势/EF均eV，缺陷与bulk同晶胞；EF相对VBM，明确绝对化学势或元素参考，不重复添加修正。'}],
        'runtime_infrastructure': [{'reference': 'runtime.entry_data', 'reason': '读取DefectEntry的dataclass字段和DefectThermodynamics.transition_level_map。'},
                                   {'reference': 'runtime.affine_oracle', 'reason': 'NumPy按已给总能差独立计算1.5和0.5+EF，测试中能量为人工输入。'}],
        'boundaries': ['未执行DFT或FNV/eFNV修正；fixture_offset是给定修正项而非算法计算。', 'check_compatibility=False仅用于人工能量；未宣称所给化学势位于真实GaAs稳定区。', '在线latest可能比3.2.1新，工具签名以固定源码为准。'],
        'tasks': [
            recipe('formation_charge_transition', '为同一Ga空位构造q=0和+1能量条目，扫描0–2 eV费米能并求电荷转变；断言形成能为1.5及0.5+EF，转变在1 eV，0.5 eV稳定+1、1.5 eV稳定0。', 'bulk=-80 eV，缺陷总能差4.5/3.5 eV，muGa=-3 eV，VBM=0。', [
                ('构造一致结构与能量条目', ['doped.core.Vacancy.__init__', 'pymatgen.analysis.defects.core.Vacancy.defect_structure', 'pymatgen.core.entries.ComputedStructureEntry.__init__', 'doped.core.DefectEntry']),
                ('计算形成能与分析', ['doped.core.DefectEntry.formation_energy', 'doped.thermodynamics.DefectThermodynamics.__init__'])],
                ['形成能与解析直线误差<1e-10 eV；转变1±1e-9 eV；稳定态符合下包络。'], PREFIX+'verify_materials_defects.py'),
            recipe('repair_chemical_potential', '定位muGa符号错误使中性形成能变为7.5 eV，改回-3 eV后恢复1.5 eV；验证给定0.2 eV修正只加一次，并保存恢复条目后复算一致。', '沿用固定能量，错误muGa=+3 eV；正确目标1.5 eV。', [
                ('核查能量及化学势', ['doped.core.DefectEntry.get_ediff', 'doped.core.DefectEntry.formation_energy']),
                ('保存与恢复条目', ['doped.core.DefectEntry.to_json', 'doped.core.DefectEntry.from_json', 'doped.core.DefectEntry.formation_energy'])],
                ['错误值7.5被拒绝；正确1.5；q+1在EF=.5加修正后1.2；保存恢复误差<1e-10。'], PREFIX+'verify_materials_defects.py')],
        'runtime_report': PREFIX+'runtime/materials_defects/01.04.03.json', 'runtime_scope': '真实doped形成能和转变分析，固定人工总能量与化学势，无DFT。',
    })

    reconstruction = [
        *[snb('distortions', name, None, 'distortion', why) for name, why in [
            ('distort', '按明确近邻和畸变倍率生成候选。'), ('rattle', '固定随机种子扰动，避免仅对称缩放遗漏构型。'),
            ('local_mc_rattle', '局域化扰动的必要补充，控制远处原子的扰动。'), ('apply_dimer_distortion', '已知成键重构候选的定向畸变。')]],
        snb('input', 'Distortions', '__init__ from_structures apply_distortions write_distortion_metadata', 'campaign', '多电荷/多畸变实验组织与可重放元数据，不暴露依赖VASP许可的输入写出。'),
        *[snb('analysis', name, None, 'analysis', why) for name, why in [
            ('get_gs_distortion', '从已有能量表找到当前候选集中最低能，并忽略标记为High_Energy的无效项。'),
            ('get_energies', '读取当前计算返回的能量表。'), ('get_structures', '将候选身份与结构文件对应。'),
            ('compare_structures', '比较候选和基准结构，识别重复弛豫终态。')]],
        snb('energy_lowering_distortions', 'write_groundstate_structure', None, 'persistence', '保存当前候选集合的最低能结构，不能保证全局基态。'),
        doped('core', 'Vacancy', '__init__', 'defect', '构造带宿主/位点身份的缺陷供畸变使用。'),
        doped('core', 'Defect', 'get_supercell_structure as_dict to_json from_json', 'defect', '返回同一超胞中的缺陷坐标及等价位点，避免用原胞坐标畸变超胞。', '由具体Vacancy构造并继承，return_sites为复数且返回3项。'),
        *geometry(),
    ]
    c.write({
        'scenario_id': '01.04.02',
        'description': '缺陷重构候选搜索环境：doped提供有身份和超胞位置的缺陷，ShakeNBreak生成键畸变/随机扰动，整理外部弛豫返回的能量与结构并选择已评估集合的最低能候选。pymatgen-core负责结构与周期距离。固定样例验证跨包几何、随机重放和给定能量表排序；未执行电子结构弛豫，不能把排序结果当作已证实的物理全局基态。',
        'packages': [('shakenbreak', 'primary', '畸变候选生成、重构分析和结果管理。'), ('doped', 'complement', '同一缺陷身份及真实超胞/位点数据桥接，不再重复枚举所有缺陷。'), (CORE, 'complement', '共用结构、距离检查及保存恢复。')],
        'symbols': reconstruction,
        'sources': [
            source('https://shakenbreak.readthedocs.io/en/latest/ShakeNBreak_Example_Workflow.html', 'CdTe空位案例串联doped、畸变、HPC弛豫、重新测试和最终能量比较；扰动近邻数与电子计数相关但并非固定物理定律。', 'defect distortion relaxation_result', 'Distortions apply_distortions', '端到端流程及外部弛豫边界'),
            source('https://shakenbreak.readthedocs.io/en/latest/Analysis.html', '解析输出为畸变→最终能量eV的YAML，分析结构位移与最大匹配距离，再对比能量降低。', 'energy_table candidate reference_structure', 'get_energies compare_structures', '修复能量单位并筛选当前最低能候选'),
            source('https://doped.readthedocs.io/en/latest/generation_tutorial.html', '生成教程推荐先做ShakeNBreak结构搜索，超胞和缺陷坐标明确保存；POTCAR生成需要额外配置。', 'defect supercell site', 'Defect.get_supercell_structure', '在超胞坐标下局域畸变'),
        ],
        'entities': [entity('defect', 'host_revision defect_type charge site supercell', '构造缺陷与超胞→固定缺陷坐标→创建候选。'),
                     entity('candidate', 'defect_id distortion_factor neighbour_indices random_seed structure revision', '畸变/扰动→几何检查→保存→外部弛豫；改变输入须清除旧能量关联。'),
                     entity('relaxation_result', 'candidate_id converged final_structure energy_eV source valid', '导入结果→单位与状态核对→排除无效项→排序；未收敛项不参加最低能比较。')],
        'capabilities': ['distortion', 'campaign', 'analysis', 'persistence', 'defect', 'structure'],
        'bridges': [{'from': 'doped Defect.get_supercell_structure(return_sites=True)', 'to': 'ShakeNBreak.distort(structure, frac_coords=site.frac_coords)', 'contract': '返回3项：超胞、缺陷位点、等价位点；畸变使用超胞分数坐标，倍率0.8表示缩短20%，不等于-0.2。'},
                    {'from': 'external relaxation results', 'to': 'get_gs_distortion energy table', 'contract': '能量统一eV并关联同一缺陷、电荷和计算基准；当前仅使用已知fixture表，不宣称执行外部计算。'}],
        'runtime_infrastructure': [{'reference': 'runtime.rattle_dependencies', 'reason': 'ShakeNBreak内部调用ASE及hiphive1.5；均安装并freeze，但不额外暴露它们的接口。'},
                                   {'reference': 'runtime.energy_fixture', 'reason': '显式能量表、单位换算和NumPy几何/随机重放oracle；表值不从几何推断。'}],
        'boundaries': ['没有VASP或其他弛豫程序；能量表是固定输入，不是DFT计算结果。', 'rattle的d_min是Monte Carlo惩罚参数而非硬距离约束，生成后还需几何检查。', '方法名相似但doped return_sites与PAD return_site不同，任务按实际3.2.1接口验证。'],
        'tasks': [
            recipe('repair_bond_distortion', '创建Ga空位超胞，将错误倍率1.2修为0.8，对四个最近邻进行键缩短；核验距离为1.958256643 Å、组成不变且原结构未被修改，再以种子23扰动并检查重放和保存恢复一致。', 'GaAs a=5.653 Å，2×2×2超胞，四近邻原距sqrt(3)a/4；rattle stdev=.02 Å。', [
                ('构造缺陷超胞和位点', ['doped.core.Vacancy.__init__', 'doped.core.Defect.get_supercell_structure']),
                ('畸变、检查与扰动', ['shakenbreak.distortions.distort', 'pymatgen.core.lattice.Lattice.get_distance_and_image', 'shakenbreak.distortions.rattle']),
                ('保存恢复', ['pymatgen.core.structure.IStructure.to', 'pymatgen.core.structure.IStructure.from_file'])],
                ['错误倍率不达距离目标；正确四邻距误差<1e-8 Å；两次seed23坐标误差<1e-12；源结构不变。'], PREFIX+'verify_materials_reconstruction.py'),
            recipe('repair_energy_units', '检查参考与畸变能量混用eV和meV的问题，将畸变项转成eV并排除无效标记；断言当前最低能候选为-20%畸变、相对参考降低0.8 eV。', '参考-10 eV，候选-10800/-10200 meV；另有High_Energy无效项。', [
                ('观察错误单位的排序值', ['shakenbreak.analysis.get_gs_distortion']),
                ('统一单位后重新选择', ['shakenbreak.analysis.get_gs_distortion'])],
                ['错误降低-10790不达固定目标；修复后候选-.2，降低-.8±1e-12 eV；无效项未被选中。'], PREFIX+'verify_materials_reconstruction.py')],
        'runtime_report': PREFIX+'runtime/materials_reconstruction/01.04.02.json', 'runtime_scope': '真实doped到ShakeNBreak桥接、可重放扰动和已给能量排序，无电子结构弛豫。',
    })


if __name__ == '__main__':
    main()
