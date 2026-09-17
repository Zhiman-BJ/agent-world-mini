"""Joint selection for a common local atomic-calculation backend contract."""
from seed_gen.scripts.scenario_collection_support import Collection, recipe, symbol

PREFIX = 'seed_gen/scenario_collection/'
PACKAGE = 'pyiron-workflow-atomistics'
RELEASE = 'https://github.com/pyiron/pyiron_workflow_atomistics/blob/pyiron_workflow_atomistics-0.2.1/'


def main():
    collection = Collection()
    def op(package, module, name, methods, capability, reason, construction=None):
        return symbol(package, module, name, methods, capability, reason, construction)
    def extension(module, name, methods, capability, reason, construction=None):
        return op(PACKAGE, 'pyiron_workflow_atomistics.engine.'+module, name, methods, capability, reason, construction)
    def ase(module, name, methods, capability, reason, construction=None):
        return op('ase', 'ase.'+module, name, methods, capability, reason, construction)
    def entity(name, attributes, lifecycle):
        return {'name': name, 'attributes': attributes.split(), 'lifecycle': lifecycle}
    source = collection.source
    collection.write({
        'scenario_id': '01.03.04',
        'description': '为原子计算配置共同的结构、Engine输入和结果接口，在两个本地ASE解析势之间切换，诊断单位、收敛预算及结果失效，并保存和恢复固定计算。主实现采用pyiron-workflow-atomistics的ASEEngine；本轮验证限Ar2解析势，不冒称真实半导体DFT或多种外部可执行求解器已接通。',
        'packages': [
            (PACKAGE, 'primary', '发布版Engine/EngineOutput统一静态和弛豫调用，实际执行两种ASE计算器；不暴露未运行的MD与外部后台。'),
            ('ase', 'complement', '提供唯一原子结构、计算器、晶胞和结构读写；明确eV、Å单位及计算器状态。'),
            ('pyiron-atomistics', 'excluded', '完整项目/作业系统与当前Engine重复且依赖严格固定；不为此小场景再建另一套状态或安装外部求解器。'),
            ('pyiron-base', 'excluded', '项目作业与存储属于替代编排体系，当前Engine及显式文件生命周期已满足任务。'),
            ('pyiron-workflow', 'excluded', '最新候选0.20.0与扩展0.2.1要求的0.19.0冲突；环境按依赖安装0.19.0但不暴露workflow API，也不声称分布式图执行。'),
        ],
        'symbols': [
            extension('ase', 'ASEEngine', 'get_calculate_fn with_working_directory', 'engine', '共同计算器配置和不修改原对象的任务子目录。', 'dataclass生成构造，字段来自发布源码EngineInput/calculator/working_directory/properties；不伪造__init__文档。'),
            extension('inputs', 'CalcInputStatic', [], 'engine', '静态计算的显式输入类型。', 'dataclass自动生成无参数构造。'),
            extension('inputs', 'CalcInputMinimize', [], 'engine', '力阈值、步数预算和晶胞松弛配置，能量停止条件需独立检查。', 'dataclass生成带默认值的构造，字段见来源Attributes。'),
            extension('protocol', 'EngineOutput', 'to_dict', 'observe', '同一结果记录包含结构、能量、力、收敛和可选轨迹。', '由calculate产生；不手造成功结果。'),
            extension('protocol', 'calculate', None, 'execute', '静态或弛豫的共同高层调用，实际返回EngineOutput。'),
            ase('atoms', 'Atoms', '__init__ calc get_potential_energy get_forces get_stress get_total_energy', 'structure', '单一结构实体及计算器引用、科学输出，避免同时维护第二种原子结构。'),
            ase('cell', 'Cell', '__init__ new fromcellpar cellpar lengths angles volume rank orthorhombic scaled_positions cartesian_positions copy', 'structure', '晶胞构造、单位/退化晶胞诊断及分数/笛卡尔坐标转换。'),
            ase('build.bulk', 'bulk', None, 'structure', '常见晶体构造入口；固定Ar2样例使用Atoms直接构造。'),
            ase('calculators.lj', 'LennardJones', '__init__', 'calculator', '第一个实跑解析势，epsilon/sigma/rc明确为eV/Å；计算通过Engine。'),
            ase('calculators.morse', 'MorsePotential', '__init__', 'calculator', '第二个实跑解析势，epsilon/r0/rho0具有不同意义，不能直接复制所有参数。'),
            ase('calculators.calculator', 'Calculator', 'set reset check_state todict directory label get_atoms', 'calculator', '具体计算器继承的参数、缓存失效和配置导出，不额外实例化抽象计算器。', '由LennardJones或MorsePotential构造后继承；不直接构造基类。'),
            ase('optimize.bfgs', 'BFGS', '__init__', 'execute', 'Engine默认优化器，可显式提供日志/重启设置。'),
            ase('optimize.optimize', 'Optimizer', 'run irun converged todict', 'execute', 'BFGS继承的运行、收敛判断及配置，不重复选择内部step/Hessian操作。', 'BFGS继承；Engine内部创建，独立诊断也可用已选BFGS构造。'),
            ase('optimize.optimize', 'BaseDynamics', 'attach get_number_of_steps', 'observe', '优化观察回调与步数，形成预算诊断。', '由BFGS对象继承，不另建BaseDynamics。'),
            ase('io.formats', 'read', None, 'persist', '保存后的原子结构恢复。'),
            ase('io.formats', 'write', None, 'persist', '将结果结构导出extxyz等格式，文件路径限当前任务目录。'),
        ],
        'sources': [
            source(RELEASE+'pyiron_workflow_atomistics/engine/protocol.py', '发布版Engine定义get_calculate_fn与纯with_working_directory，EngineOutput统一final_structure/energy/converged；calculate接入物理工作流。', 'engine calculation result', 'calculate EngineOutput.to_dict', '同一输入切换计算器并验共同结果'),
            source(RELEASE+'tests/unit/engine/test_ase.py', '官方测试覆盖真实EMT静态/弛豫、子目录不变性、pickle及共享计算器锁；这是接口案例，不是当前Ar2参考值。', 'structure backend working_directory', 'ASEEngine calculate', '创建、运行、隔离路径、保存恢复'),
            source(RELEASE+'pyiron_workflow_atomistics/engine/ase.py', '实现按CalcInput分派，复制输入结构、按力或能量变化判断停止，构造EngineOutput并写job_data；能量条件可将converged设真，所以任务必须另验目标。', 'input budget force energy cache', 'CalcInputMinimize ASEEngine', '零预算失败、独立力和解析最低能修复'),
        ],
        'entities': [
            entity('atomic_structure', 'id symbols positions_A cell_A pbc revision', '创建/加载→改坐标或晶胞→使旧结果失效；输入保持不变，结果结构另存。'),
            entity('engine_configuration', 'id calculator parameters input_type tolerance max_iterations working_directory revision', '选后台→配置单位/预算→新建子目录→运行；参数变化使结果失效。'),
            entity('calculation_result', 'id source_revision energy_eV forces_eV_A volume_A3 converged trajectory', '计算产生→核验数值与状态→导出；converged不等于任务目标成立。'),
            entity('run_archive', 'id structure_file result_file engine_configuration source_versions', '保存可信本地配置和结果→恢复→重跑核验；reset隔离任务目录。'),
        ],
        'capabilities': ['structure', 'engine', 'calculator', 'execute', 'observe', 'persist'],
        'bridges': [{'from': 'ASE Atoms and Calculator', 'to': 'ASEEngine -> calculate -> EngineOutput', 'contract': '位置Å、能量eV、力eV/Å；两势参数不同；结构输入不变；with_working_directory共享calculator，内部锁保护但本轮未测并发。'}],
        'runtime_infrastructure': [
            {'reference': 'runtime.backend_oracle', 'reason': 'NumPy读取positions/cell等实体属性，独立LJ/Morse公式及作用反作用/解析极小点；不是隐藏求解器。ASE私有_LimitedAtoms的继承几何方法不伪造到参考API，脚本改用显式数组属性算距离。'},
            {'reference': 'runtime.trusted_archive', 'reason': 'Python标准JSON和仅对脚本自建对象的pickle保存恢复；包版本查询、局部文件路径与错误证据。后续Agent不得反序列化任意外来pickle。'},
        ],
        'boundaries': [
            '两种实际ASE解析势在同一适配器下验证共同合同，不等于VASP/GPAW/LAMMPS等外部后台已接通。Ar2用于可手算接口fixture，不作为半导体经验势验证。',
            'Engine和输入类的构造由dataclass生成，静态候选保留原样空方法；positions等状态属性不按函数计数。BFGS和Cell等保留接口并未全部实跑。',
            'pyiron-workflow0.19.0仅为扩展硬依赖；最新0.20.0候选保留排除证据，不混用其参考API。未验证MD、分布式计算、SLURM或优化重启续跑。',
            '在线ASE旧文档路径404保留，不计成功来源；使用实际读取的官方固定发布代码和完整案例。',
        ],
        'count_exception': '共同Engine收敛了大量后台细节；只保留结构、两势、运行观察、状态诊断与存储。低于50时不加入未验证外部求解器、MD或重复底层calculate以凑数。',
        'tasks': [
            recipe('switch_backend_and_repair_units', '对固定Ar2结构切换Lennard-Jones和Morse计算器，核验共同能量/力结果与独立解析公式；诊断epsilon把meV当eV造成的1000倍错误，修复后达到固定能量目标，并保证原输入结构和Engine路径不变。', 'Ar2距离1.3Å、10Å非周期盒；epsilon=.5eV，LJ sigma1/rc3Å，Morse r0=1.2Å/rho0=6。', [
                ('构造并配置两后台', ['ase.atoms.Atoms.__init__', 'ase.calculators.lj.LennardJones.__init__', 'ase.calculators.morse.MorsePotential.__init__', 'pyiron_workflow_atomistics.engine.ase.ASEEngine.get_calculate_fn']),
                ('共同运行并核验', ['pyiron_workflow_atomistics.engine.protocol.calculate', 'pyiron_workflow_atomistics.engine.protocol.EngineOutput.to_dict']),
                ('修正单位并隔离运行', ['pyiron_workflow_atomistics.engine.ase.ASEEngine.with_working_directory', 'pyiron_workflow_atomistics.engine.protocol.calculate']),
            ], ['LJ=-.325768736357904eV，Morse=-.422590939126912eV；公式误差<1e-12，合力0；错误-325.7687被拒绝，修复通过。'], PREFIX+'verify_materials_backend.py'),
            recipe('repair_relaxation_budget_and_restore', '在同一LJ结构上识别零迭代预算导致的未收敛，修成100步上限后执行弛豫；独立验证距离2^(1/6)Å、最大力和解析最低能，将结构/结果保存恢复，并对可信本地Engine序列化后重跑核验。', '从1.3Å开始，fmax1e-7eV/Å、能量停止条件关闭；错误预算0，修复预算100。', [
                ('执行错误预算并检查', ['pyiron_workflow_atomistics.engine.protocol.calculate']),
                ('修复并验解析目标', ['pyiron_workflow_atomistics.engine.protocol.calculate', 'pyiron_workflow_atomistics.engine.protocol.EngineOutput.to_dict']),
                ('保存恢复和重算', ['ase.io.formats.write', 'ase.io.formats.read', 'pyiron_workflow_atomistics.engine.ase.ASEEngine.with_working_directory', 'pyiron_workflow_atomistics.engine.protocol.calculate']),
            ], ['零预算不收敛且力>1e-2；修复fmax<1e-7、距离误差<1e-7Å、能量误差<1e-12eV；结构与数值归档往返。'], PREFIX+'verify_materials_backend.py'),
        ],
        'runtime_report': PREFIX+'runtime/materials_backend/01.03.04.json',
        'runtime_scope': '两个真实本地ASE计算器、共同Engine、静态/弛豫及文件恢复，独立解析oracle、故障和修复；无外部DFT/MD。',
    })


if __name__ == '__main__':
    main()
