"""Reviewed joint selections for fixed ML-potential and tight-binding tasks."""
from seed_gen.scripts.scenario_collection_support import Collection, recipe, symbol

BASE = 'seed_gen/scenario_collection/'


def entity(name, attributes, lifecycle):
    return dict(name=name, attributes=attributes.split(), lifecycle=lifecycle)


def ml(collection):
    def c(module, name, methods, capability, reason, construction=None):
        return symbol('chgnet', 'chgnet.'+module, name, methods, capability, reason, construction)
    def pm(module, name, methods, capability, reason, construction=None):
        return symbol('pymatgen-core', 'pymatgen.'+module, name, methods, capability, reason, construction)
    release = 'https://github.com/CederGroupHub/chgnet/blob/v0.4.2/'
    script = BASE+'verify_materials_mlpot.py'
    collection.write({
        'scenario_id': '01.01.04',
        'description': '固定预训练势权重和结构版本，对局部扰动晶体预测能量/力，核验每原子能与总能的单位桥接，执行有限步弛豫并保存恢复。以CHGNet为主势、pymatgen为结构状态，ASE只补实际计算器桥接；CPU小样例验证内部数值一致性，不替代DFT精度评估。',
        'packages': [('chgnet', 'primary', '发布版携带0.3.0权重，可核对源码与安装权重SHA，在CPU执行预测、图诊断及高层弛豫。'),
                     ('pymatgen-core', 'complement', '唯一晶体结构、晶胞、扰动和序列化状态；显式继承接口裁剪。'),
                     ('ase', 'complement', '只公开实际Atoms能量/力桥接，优化器作为CHGNet硬依赖，不重复暴露第二套优化工作流。'),
                     ('matgl', 'excluded', '图势预测/弛豫与主实现重叠；本场景无需额外图后端与另一套权重。完整Python候选保留，未安装或验证模型。'),
                     ('mace-torch', 'excluded', '另一套等变模型及训练体系，当前两个固定任务已由主势闭合；训练和其他权重不在边界内。')],
        'symbols': [
            c('model.model', 'CHGNet', 'load from_file from_dict as_dict predict_structure predict_graph version n_params', 'model', '固定权重加载、版本诊断及结构/图预测；不暴露内部forward与训练。', '用load/from_file工厂获取预训练实例，不随机初始化未训练网络。'),
            c('model.dynamics', 'CHGNetCalculator', '__init__ from_file version n_params', 'bridge', 'ASE能量按原子数由eV/atom转为总eV，避免直接调用内部calculate。'),
            c('model.dynamics', 'StructOptimizer', '__init__ relax version n_params', 'relax', '配置FIRE、力阈值、固定晶胞与预算的高层弛豫。'),
            c('model.dynamics', 'TrajectoryObserver', 'compute_energy save', 'observe', '读取弛豫产生的能量/力轨迹并存档。', '由StructOptimizer.relax返回trajectory实例，数组字段保持原始单位。'),
            c('graph.converter', 'CrystalGraphConverter', '__init__ forward set_isolated_atom_response as_dict from_dict', 'graph', '显式构图、孤立原子诊断和转换器配置，避免隐式接受孤立点。'),
            c('graph.crystalgraph', 'CrystalGraph', 'to_dict save from_file from_dict num_isolated_atoms', 'graph', '由转换器产生图对象，保存恢复与拓扑诊断。', '由CrystalGraphConverter.forward或from_file/from_dict取得，不手工拼邻接索引。'),
            pm('core.structure', 'Structure', '__init__ sites cart_coords frac_coords lattice translate_sites replace make_supercell apply_strain scale_lattice', 'structure', '结构与坐标单位、扰动/超胞/应变使既有预测失效。'),
            pm('core.structure', 'IStructure', 'copy get_distance from_file to as_dict from_dict', 'persist', 'Structure继承的复制、近邻距离与标准存储恢复，避免第二种结构状态。', '通过Structure实例继承；本场景不另建不可变基类。'),
            pm('core.lattice', 'Lattice', '__init__ cubic from_parameters matrix volume get_cartesian_coords get_fractional_coords', 'structure', '晶格定义及分数/笛卡尔转换，单位Å。'),
            pm('io.ase', 'AseAtomsAdaptor', 'get_atoms get_structure', 'bridge', '唯一跨包结构转换点，核验组成、晶胞和总能归一化。', '无构造参数的静态适配器；只用选定的静态方法。'),
            symbol('ase', 'ase.atoms', 'Atoms', 'calc get_potential_energy get_forces', 'observe', '访问转换产生的Atoms和计算器结果，供桥接独立对照。', 'AseAtomsAdaptor.get_atoms工厂产生，不独立构造另一种主状态。'),
        ],
        'sources': [
            collection.source(release+'README.md', '官方示例展示CHGNet.load、predict_structure的e/f/s/m以及StructOptimizer.relax，并明确eV/atom和eV/Å；模型版本与软件版本不同。', 'structure model prediction', 'CHGNet.load predict_structure StructOptimizer.relax', '固定权重的预测和弛豫链'),
            collection.source(release+'chgnet/model/dynamics.py', 'ASE计算器将intensive能量乘原子数；relax可限定steps、fmax和relax_cell，返回final_structure和trajectory。', 'normalization budget trajectory', 'CHGNetCalculator StructOptimizer.relax', '单位错误、预算错误的诊断修复'),
            collection.source(release+'tests/test_model.py', '发布版测试包含旋转、超胞、批处理和孤立结构，提供不变量与异常测试思路；本轮具体数值来自本地Si扰动fixture。', 'invariance graph result', 'predict_structure predict_graph', '有限差分、平移与总力守恒验证'),
        ],
        'entities': [entity('crystal_structure', 'id species lattice_A fractional_positions revision', '构造→扰动→预测失效→复制/存储/恢复。'),
                     entity('potential_model', 'id software_version weight_version checkpoint_sha256 device', '固定可信权重→CPU加载；切换权重使全部结果失效。'),
                     entity('prediction', 'id structure_revision energy_eV_atom total_energy_eV forces_eV_A', '预测→按原子数换算→有限差分核验；保留归一化来源。'),
                     entity('relaxation_run', 'id input_revision fmax steps relax_cell trajectory final_structure', '设预算→运行→独立检查最终力与能量→导出；返回对象不等于收敛。')],
        'capabilities': ['model', 'structure', 'graph', 'bridge', 'relax', 'observe', 'persist'],
        'bridges': [{'from': 'pymatgen Structure / CHGNet intensive prediction', 'to': 'ASE Atoms / CHGNetCalculator', 'contract': 'Å坐标不变，e为eV/atom，ASE能量为N*e eV，力均eV/Å；真实双向结构适配，脚本核对两种能量输出。'}],
        'runtime_infrastructure': [{'reference': 'runtime.numeric_oracle', 'reason': 'NumPy有限差分、平移不变量和总力检查；PyTorch只固定CPU与线程数，hashlib核对可信发布权重，不额外实现势。'},
                                   {'reference': 'runtime.entity_fields', 'reason': '读取trajectory能量/力列表及Structure坐标等状态属性；len与JSON/hash/版本是基础设施。'}],
        'boundaries': ['两原子Si原胞沿x扰动0.12Å；这是数值一致性固定样例，不据此声称模型对某器件/材料的DFT误差。',
                       '软件0.4.2携带模型0.3.0；安装权重与发布源码SHA完全相同。无新模型下载、训练、GPU或分子动力学验证。',
                       'CHGNet官网返回403、examples/README返回404，保留失败证据，只计成功读取的固定发布页。',
                       '模型图/存档和部分结构操作是受限参考能力，未逐个实跑；正式Agent状态容器与JSON工具仍待构建。'],
        'tasks': [recipe('repair_energy_normalization', '对固定扰动Si原胞预测能量和力，诊断把每原子能量误当总能造成的有限差分错误；修复归一化后核验力、平移不变量和ASE计算器真实桥接。', '0.12Å单原子位移，发布版0.3.0权重，CPU2线程；差分步长0.005Å。', [
            ('加载模型和结构', ['chgnet.model.model.CHGNet.load', 'pymatgen.core.structure.Structure.__init__', 'pymatgen.core.structure.Structure.translate_sites']),
            ('预测并扰动对照', ['chgnet.model.model.CHGNet.predict_structure', 'pymatgen.core.structure.IStructure.copy', 'pymatgen.core.structure.Structure.translate_sites']),
            ('修复总能并验桥接', ['pymatgen.io.ase.AseAtomsAdaptor.get_atoms', 'chgnet.model.dynamics.CHGNetCalculator.__init__', 'ase.atoms.Atoms.get_potential_energy', 'ase.atoms.Atoms.get_forces'])],
            ['力-0.913655996eV/Å；总能差分-0.909137726，误差<.01；错误归一化-0.454568863被拒绝；桥接总能误差<2e-5eV。'], script),
                  recipe('repair_relaxation_and_restore', '用零步预算触发力阈值未达标，修复预算后对相同结构执行固定晶胞弛豫；核验能量下降和最终最大力，保存结构恢复后重算，同时确认输入没有改变。', 'FIRE，fmax=.025eV/Å，steps从0改100，relax_cell=False。', [
                      ('运行错误预算并检查', ['chgnet.model.dynamics.StructOptimizer.__init__', 'chgnet.model.dynamics.StructOptimizer.relax', 'chgnet.model.model.CHGNet.predict_structure']),
                      ('修复预算并独立验力', ['chgnet.model.dynamics.StructOptimizer.relax', 'chgnet.model.model.CHGNet.predict_structure']),
                      ('保存恢复并重算', ['pymatgen.core.structure.IStructure.to', 'pymatgen.core.structure.IStructure.from_file', 'chgnet.model.model.CHGNet.predict_structure'])],
                      ['最终最大力.008372955<.025；每原子能-5.283009529降至-5.312177658eV；13轨迹帧；两次任务结果相同。'], script)],
        'runtime_report': BASE+'runtime/materials_mlpot/01.01.04.json', 'runtime_scope': '真实CPU预训练CHGNet预测、ASE桥接、弛豫及结构恢复；有限差分和固定力门槛，重复两次。',
    })


def tightbinding(collection):
    def op(module, name, methods, capability, reason, construction=None):
        return symbol('pythtb', 'pythtb.'+module, name, methods, capability, reason, construction)
    script = BASE+'verify_materials_tightbinding.py'
    release = 'https://github.com/pythtb/pythtb/blob/v2.0.2/'
    collection.write({
        'scenario_id': '01.08.02',
        'description': '以PythTB构造晶格、轨道和跃迁，计算周期能带与有限链本征态，诊断现场势及边界条件错误，并以解析谱和独立矩阵验证修复。采用单一紧束缚状态，避免将TBmodels和pybinding的重复建模/求解器同时暴露。',
        'packages': [('pythtb', 'primary', '2.0.2有高层晶格/模型及有限边界入口，纯Python小型模型可用解析谱独立验证。'),
                     ('tbmodels', 'excluded', 'Wannier导入和另一套Model状态不为本SSH任务所需；保留42操作完整候选，不安装重复求解器。'),
                     ('pybinding', 'excluded', '晶格/模型/求解重叠且需额外C++后端；纯Python候选345操作已保存，原生/继承扩展尚未全量适配，不能据此称原生API全覆盖，也不据它补齐当前任务。')],
        'symbols': [
            op('models.ssh', 'ssh', None, 'construct', '官方二轨道SSH工厂，输入v/w为胞内/胞间跃迁。'),
            op('lattice', 'Lattice', '__init__ periodic_dirs orb_vecs lat_vecs dim_r dim_k norb recip_lat_vecs cell_volume info add_orb remove_orb make_supercell k_path k_uniform_mesh get_kpath_distance', 'lattice', '几何、周期方向、轨道和k点单位的单一来源；避免旧get_*别名重复。'),
            op('tbmodel', 'TBModel', '__init__ lattice nspin spinful nstate onsite hoppings nhops parameters info copy clear_hoppings clear_onsite set_onsite set_hop set_shell_hops set_parameters with_parameters add_orb remove_orb cut_piece make_finite reduce_dim make_supercell hamiltonian solve_ham velocity position_matrix position_expectation visualize plot_bands', 'model', '建模、参数化、边界、求解、观察；裁掉旧solve_all等别名、拓扑长尾和内部实现。'),
            op('mesh', 'Mesh', '__init__ points flat nodes npoints dim_k shape build_path build_grid build_custom get_k_points get_axis_range info', 'sampling', '路径和均匀网格定义/检查；支持后续任务输入采样，当前固定样例用显式101点。'),
        ],
        'sources': [collection.source('https://pythtb.readthedocs.io/en/latest/tutorials.html', '官方教程目录包括有限SSH链边缘模式、Bloch能带及周期边界有限片；作为工程任务入口，实际签名固定2.0.2。', 'lattice bands edge_states', 'TBModel cut_piece solve_ham', '从周期带隙到有限边界态'),
                    collection.source('https://pythtb.readthedocs.io/en/latest/generated/pythtb.models.ssh.html', 'SSH文档明确胞内v与胞间w以及二轨道链Hamiltonian；输入含义可独立导出正负根解析谱。', 'hopping unit_cell', 'ssh', '修复现场势并核验解析带隙'),
                    collection.source(release+'pythtb/models/ssh.py', '固定发布代码用Lattice周期方向0和两次set_hop构建SSH，轨道位置0和1/2。', 'orbital hopping', 'Lattice TBModel set_hop', '独立有限链矩阵对照'),
                    collection.source(release+'pythtb/tbmodel.py', '发布代码明确cut_piece glue_edges、copy和solve_ham，边界闭合会消除本样例近零端态；新求解返回方向与旧接口不同。', 'boundary eigenstate result', 'copy cut_piece hamiltonian solve_ham', '闭边界失败、开边界修复和残差验收')],
        'entities': [entity('lattice', 'id vectors orbital_positions periodic_directions revision', '定义晶格和轨道→修改几何→模型结果失效。'),
                     entity('tight_binding_model', 'id lattice_ref onsite hoppings parameters boundary revision', '构建→复制→配置势/跃迁→裁有限边界→求解；不修改原模型。'),
                     entity('spectral_result', 'id model_revision k_points energies_eV eigenvectors', '计算→验带隙/残差/正交/端点概率→导出。'),
                     entity('model_archive', 'id parameter_json result_npz package_version', '只保存显式fixture参数→通过真实工厂重建→谱核对；无自造库序列化API。')],
        'capabilities': ['construct', 'lattice', 'model', 'sampling'],
        'bridges': [{'from': 'PythTB periodic TBModel', 'to': 'PythTB finite TBModel', 'contract': 'cut_piece(8,0,glue_edges=False)按晶胞展开为16轨道；胞内0.5eV、胞间1eV；独立矩阵检验轨道次序/边界。'}],
        'runtime_infrastructure': [{'reference': 'runtime.spectral_oracle', 'reason': 'NumPy仅作解析SSH公式、独立16×16矩阵/eigvalsh、残差和概率检查；不是隐藏第二个可调用环境求解器。'},
                                   {'reference': 'runtime.fixture_archive', 'reason': '标准JSON存v/w/晶胞数/边界，NPZ存结果；用所选ssh/cut_piece重建，持久化属于后续facade基础设施。'}],
        'boundaries': ['SSH为显式模型fixture，不是特定半导体材料的第一性原理拟合；无接触/散射、NEGF或Wannier90数据验证。',
                       'PythTB tag及安装分发版本为2.0.2，但源码__version__仍2.0.0；版本证据采用精确tag/commit与importlib.metadata，不篡改源码常量。',
                       'pybinding排除候选的Python池不覆盖C++原生/继承接口；当前场景的主包接口完整来源重算与实跑不依赖这一缺口。',
                       'Mesh、参数化和绘图等是裁剪后的参考能力，未全部实跑；最终Agent环境状态/工具封装另行实现。'],
        'tasks': [recipe('repair_bulk_hamiltonian', '构造v=.5、w=1eV的SSH周期链，在101个k点计算能带；定位并移除意外的交错现场势，使谱回到解析正负根并达到1eV最小带隙，同时保持原模型不变。', 'k为倒格矢分数坐标0到1；错误现场势[.3,-.3]eV，目标为[0,0]。', [
            ('构造与初次求解', ['pythtb.models.ssh.ssh', 'pythtb.tbmodel.TBModel.solve_ham']),
            ('复制、注入并定位错误', ['pythtb.tbmodel.TBModel.copy', 'pythtb.tbmodel.TBModel.set_onsite', 'pythtb.tbmodel.TBModel.solve_ham']),
            ('重置现场势并验收', ['pythtb.tbmodel.TBModel.set_onsite', 'pythtb.tbmodel.TBModel.solve_ham'])],
            ['解析谱误差<1e-12eV，最小带隙1eV；错误谱偏差.083095189eV被拒绝，修复通过。'], script),
                  recipe('repair_boundary_and_verify_edges', '从同一SSH模型裁8个晶胞，识别误设闭边界导致端态消失；改为开边界后核验独立16×16矩阵、两近零端态与端点概率，保存参数/结果后重建并核对。', '错误glue_edges=True，修复False；近零阈值.01eV，端点权重下限.74。', [
                      ('裁剪与发现缺失端态', ['pythtb.tbmodel.TBModel.cut_piece', 'pythtb.tbmodel.TBModel.solve_ham']),
                      ('修复并核验矩阵和本征态', ['pythtb.tbmodel.TBModel.cut_piece', 'pythtb.tbmodel.TBModel.hamiltonian', 'pythtb.tbmodel.TBModel.solve_ham']),
                      ('从参数重建并重算', ['pythtb.models.ssh.ssh', 'pythtb.tbmodel.TBModel.cut_piece', 'pythtb.tbmodel.TBModel.solve_ham'])],
                      ['闭边界最近能量.5eV；开边界±.0029299446eV，端点概率.750117345；残差/正交和矩阵谱误差<1e-12；两次重复一致。'], script)],
        'runtime_report': BASE+'runtime/materials_tightbinding/01.08.02.json', 'runtime_scope': '真实PythTB2周期/有限求解、解析谱和独立矩阵oracle、故障修复、配置存档，两次重复。',
    })


def main():
    collection = Collection()
    ml(collection)
    tightbinding(collection)


if __name__ == '__main__':
    main()
