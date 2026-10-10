"""Reviewed anharmonic transport scene from the released Si force fixture."""
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe
from seed_gen.scripts.prepare_materials_workflow_physics_profiles import PREFIX, entity


def main():
    c = Collection()
    def source(url, evidence, entities, tools, tasks):
        return c.source(url, evidence, entities.split(), tools.split(), tasks.split())
    symbols = [
        symbol('phono3py', 'phono3py.cui.load', 'load', None, 'load', '从固定YAML/位移/力数据构造并恢复非谐模型。'),
        symbol('phono3py', 'phono3py.api_phono3py', 'Phono3py',
               '__init__ fc2 fc3 sigmas sigma_cutoff nac_params primitive unitcell supercell phonon_supercell supercell_matrix phonon_supercell_matrix primitive_matrix dataset phonon_dataset masses supercells_with_displacements phonon_supercells_with_displacements mesh_numbers thermal_conductivity displacements forces phonon_displacements phonon_forces init_phph_interaction get_phonon_data run_phonon_solver generate_displacements generate_fc2_displacements produce_fc2 produce_fc3 symmetrize_fc2 symmetrize_fc3 run_thermal_conductivity save',
               'transport', '二/三阶力常数、位移和热输运主链；统一高层入口，裁去ML势拟合与低层张量变换。'),
        symbol('phono3py', 'phono3py.conductivity.calculators', 'ConductivityCalculatorBase',
               'mesh_numbers frequencies qpoints grid_points grid_weights temperatures sigmas gamma gamma_isotope group_velocities mode_heat_capacities',
               'observe', '追溯当前求解的采样、温度、寿命相关宽度和模态贡献输入。', 'Phono3py.run_thermal_conductivity创建RTACalculator，通过继承获得查询。'),
        symbol('phono3py', 'phono3py.conductivity.calculators', 'RTACalculator', 'kappa mode_kappa number_of_ignored_phonon_modes',
               'observe', '读取RTA宏观/模态热导并检查被忽略声子数；不重复暴露直接构造和内部求解。', '通过Phono3py.thermal_conductivity取得。'),
        symbol('phonopy', 'phonopy.structure.atoms', 'PhonopyAtoms', '__init__ cell scaled_positions symbols masses volume copy get_yaml_lines',
               'structure', 'phono3py的结构硬依赖，仅暴露共享输入晶胞而不再暴露谐波求解器。'),
    ]
    c.write({
        'scenario_id': '01.05.02',
        'description': '非谐声子热输运环境：从固定结构、位移与原子力建立二/三阶力常数，配置q网格、温度和同位素散射，计算RTA热导张量并追踪模态/散射信息。phono3py为主，phonopy仅提供共享结构；hiPhive拟合能力在已有力数据的当前闭环中不重复纳入。固定官方Si-PBEsol样例验证网格配置修复及同位素散射影响，未据此声明网格收敛或真实实验预测精度。',
        'packages': [('phono3py', 'primary', '提供给定力数据的二/三阶力常数和RTA热输运。'),
                     ('phonopy', 'complement', '硬依赖且负责PhonopyAtoms结构；不重复暴露谐波后处理。'),
                     ('hiphive', 'excluded', '官方1.5支持稀疏高阶力常数拟合；当前已有完整官方位移/力fixture，不引入训练、误差评估和另一套力常数拟合状态。')],
        'symbols': symbols,
        'sources': [
            source('https://phonopy.github.io/phono3py/phono3py-api.html', 'AlN-LDA API案例使用PhonopyAtoms和Phono3py串联结构、位移/力及声子计算；站点标4.4.0，所选源码为正式4.5.0。', 'structure forces fc2 fc3', 'load Phono3py', '固定外部力数据构造模型'),
            source('https://phonopy.github.io/phono3py/examples.html', '官方提供VASP/Pwscf硅示例，指向Si-PBEsol Python流程；用于确定可本地后处理的输入边界。', 'dataset transport_result', 'run_thermal_conductivity', '复用固定力文件而不执行外部DFT'),
            source('https://github.com/phonopy/phono3py/blob/v4.5.0/test/conductivity/test_kappa_RTA.py', '发布版测试给出300 K、9³网格非compact力常数RTA参考107.694以及同位素散射97.296 W/(m K)，atol0.5；独立固定值用于本地验收。', 'mesh temperature isotope kappa', 'init_phph_interaction run_thermal_conductivity', '网格修复和散射开关对比'),
            source('https://hiphive.materialsmodeling.org/', '官方示例为ClusterSpace→StructureContainer→Optimizer训练→ForceConstantPotential，可从更少构型估计高阶力常数；此能力不等于RTA求解主接口。', 'training_structures force_constant_model', 'ForceConstantPotential', '明确互补拟合阶段，当前场景排除以控制范围'),
        ],
        'entities': [entity('force_dataset', 'structure displacement_ids forces unit supercell source_hash', '读取→核对单位与构型顺序→生成FC；输入改变使原FC失效。'),
                     entity('anharmonic_model', 'fc2 fc3 primitive supercell masses revision', '拟合/读取→对称与有限性检查→初始化相互作用；FC改变后重新初始化。'),
                     entity('transport_run', 'model_revision mesh temperatures isotope sigma kappa mode_kappa gamma', '配置→初始化→求解→张量和参考验收→保存；每次条件改变需重新计算。')],
        'capabilities': ['load', 'transport', 'observe', 'structure'],
        'bridges': [{'from': 'phonopy PhonopyAtoms + released force files', 'to': 'phono3py model -> RTACalculator', 'contract': 'YAML记录位移/超胞身份，FORCES_FC3按位移顺序排列；单位来自原VASP输入，热导W/(m K)，温度K；结构不换成另一套对象。'}],
        'runtime_infrastructure': [{'reference': 'runtime.transport_oracle', 'reason': '读取官方版本化测试常数，用NumPy比较张量；两线程OpenMP限制资源，未隐藏替代输运求解器。'}],
        'boundaries': ['9³网格是固定回归目标，未验证实际材料的网格收敛；粗网格失败是未达到既定参考条件。',
                       '使用已发布预计算力文件，没有新运行VASP/QE，也没有训练hiPhive模型。',
                       '只实跑RTA与同位素项，不含完整迭代LBTE、四声子或电子声子散射。',
                       '文档4.4.0与发布源码4.5.0的差异保留，具体方法由4.5.0重新解析。'],
        'tasks': [
            recipe('repair_transport_mesh', '载入官方Si-PBEsol力数据计算300 K的RTA热导，发现3³网格未达到固定参考；改为9³后重新初始化并求解，断言对角热导约107.694 W/(m K)、非对角为零且符合立方对称。', 'v4.5.0 test/phono3py_si_pbesol.yaml与FORCES_FC3_si_pbesol；make_r0_average=True。', [
                ('加载模型并核对力常数', ['phono3py.cui.load.load', 'phono3py.api_phono3py.Phono3py.fc2', 'phono3py.api_phono3py.Phono3py.fc3']),
                ('修改网格并计算', ['phono3py.api_phono3py.Phono3py.mesh_numbers', 'phono3py.api_phono3py.Phono3py.init_phph_interaction', 'phono3py.api_phono3py.Phono3py.run_thermal_conductivity']),
                ('读取热导张量', ['phono3py.api_phono3py.Phono3py.thermal_conductivity', 'phono3py.conductivity.calculators.RTACalculator.kappa'])],
                ['粗网格不满足参考±0.5；9³三个对角均107.694±0.5；非对角及对角差<1e-6。'], PREFIX+'verify_materials_anharmonic.py'),
            recipe('isotope_scattering_comparison', '在相同Si模型、300 K和9³网格上启用同位素散射重新求解，比较热导；断言三个对角分量仍为正、低于未启用值，并接近97.296 W/(m K)的发布版参考。', '沿用修复后的固定模型和网格，仅改变is_isotope。', [
                ('重新初始化和求解', ['phono3py.api_phono3py.Phono3py.init_phph_interaction', 'phono3py.api_phono3py.Phono3py.run_thermal_conductivity']),
                ('比较散射前后结果', ['phono3py.api_phono3py.Phono3py.thermal_conductivity', 'phono3py.conductivity.calculators.RTACalculator.kappa'])],
                ['同位素热导对角97.296±0.5；全部>0且低于无同位素值。'], PREFIX+'verify_materials_anharmonic.py')],
        'runtime_report': PREFIX+'runtime/materials_anharmonic/01.05.02.json',
        'runtime_scope': '发布Si-PBEsol力文件的真实FC2/FC3与RTA，固定参考值、立方对称和同位素散射对比；无新DFT。',
    })


if __name__ == '__main__':
    main()
