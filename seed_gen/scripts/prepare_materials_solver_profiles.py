"""Reviewed, jointly selected DFT interface and ABINIT input/result seeds."""
from pathlib import Path
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe

BASE=Path('seed_gen/scenario_collection/materials_solver')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/materials_solver')

def ent(name,attributes,lifecycle):
    return dict(entity=name,attributes=attributes.split(),lifecycle=lifecycle)

def op(package,module,name,methods,capability,reason,construction=None):
    return symbol(package,module,name,methods,capability,reason,construction)

def main():
    c=Collection(base=BASE,raw=RAW)
    def src(url,evidence,entities,tools,tasks):return c.source(url,evidence,entities,tools,tasks)
    def rec(script,i,description,initial,steps,assertions):
        return recipe(i,description,initial,steps,assertions,(BASE/script).as_posix())
    def emit(sid,description,packages,symbols,sources,entities,bridges,infra,boundaries,tasks,runtime,scope):
        c.write(dict(scenario_id=sid,description=description,packages=packages,symbols=symbols,sources=sources,
                     entities=entities,capabilities=list(dict.fromkeys(s['capability'] for s in symbols)),
                     bridges=bridges,runtime_infrastructure=infra,boundaries=boundaries,tasks=tasks,
                     runtime_report=(BASE/runtime).as_posix(),runtime_scope=scope))

    gpaw_symbols=[
        op('ase','ase.build.bulk','bulk',None,'structure','用常见晶体原胞构造单一ASE结构，Si diamond固定晶格提供独立体积和电子数目标。'),
        op('ase','ase.atoms','Atoms','__init__ calc get_potential_energy get_forces get_stress get_dipole_moment','structure','ASE为唯一结构状态和能量/力统一入口；位置、晶胞、物种、周期性作为属性存储。'),
        op('ase','ase.cell','Cell','__init__ new fromcellpar cellpar lengths angles volume rank orthorhombic scaled_positions cartesian_positions reciprocal copy todict','geometry','晶胞构造、单位、坐标表示与退化晶胞检查；裁掉多种等价格规约。'),
        op('ase','ase.io.formats','read',None,'persist','明确格式的结构读入，不宣称可用结构文件重启电子态。'),
        op('ase','ase.io.formats','write',None,'persist','结构归档与电子态GPW分开，限定本地任务目录。'),
        op('gpaw','gpaw.new.ase_interface','GPAW',None,'calculator','固定26.7.0新版计算器工厂：从参数建立或从GPW恢复，运行官方纯Python后端。'),
        op('gpaw','gpaw.new.ase_interface','ASECalculator',
           'atoms results calculation_required check_state write get_atoms get_fermi_level get_homo_lumo get_number_of_electrons get_number_of_bands get_number_of_grid_points get_eigenvalues get_occupation_numbers get_reference_energy get_number_of_iterations get_bz_k_points get_ibz_k_points get_k_point_weights get_pseudo_density get_all_electron_density get_electrostatic_potential get_xc_functional get_number_of_spins parameters todict get_effective_potential get_atomic_electrostatic_potentials fixed_density',
           'electronic_result','保留计算状态、结果、占据、密度、电势和重启主链，能量/力只经ASE调用；不纳入低层builder、MPI及私有波函数存储。get_occupation_numbers使用raw=True并复制后施加权重，避免发布代码默认路径原地修改占据。',
           '由已选gpaw.new.ase_interface.GPAW工厂产生；不调用需要内部Parameters/Logger/DFT对象的__init__。')]
    gr=['ase.build.bulk.bulk','ase.atoms.Atoms.calc','gpaw.new.ase_interface.GPAW','ase.atoms.Atoms.get_potential_energy']
    go=['gpaw.new.ase_interface.ASECalculator.get_number_of_iterations','gpaw.new.ase_interface.ASECalculator.get_number_of_electrons','gpaw.new.ase_interface.ASECalculator.get_occupation_numbers','gpaw.new.ase_interface.ASECalculator.get_k_point_weights']
    emit('01.07.01','通过ASE统一结构和结果接口连接GPAW 26.7.0，在本地WSL运行官方纯Python平面波LDA自洽计算；围绕SCF预算、电子数、单位和GPW重启构建可修复任务。固定Si2粗网格验证接口与守恒关系，不把正常收敛误称为生产级DFT精度。',
         [('ase','primary','唯一原子状态与统一计算器入口；不再加入另一套Structure/后端编排。'),('gpaw','complement','实际DFT求解和电子态输出；使用固定发布源码的官方purepython路径，无上游补丁。')],
         gpaw_symbols,[
             src('https://gpaw.readthedocs.io/documentation/basic.html','官方基本教程明确ASE Atoms.calc挂接GPAW，公共单位为eV和Angstrom，并说明能量、力等按需计算。',['atomic_structure','calculator','result'],['bulk','GPAW','get_potential_energy','get_forces'],['创建→配置→实际SCF→结果验收']),
             src('https://gpaw.readthedocs.io/documentation/restart_files.html','GPW保存密度、势、特征值等；write(mode=all)才附带波函数。GPAW(filename)恢复，结构文件不能代替电子态文件。',['restart_artifact','electronic_state'],['write','GPAW','get_atoms'],['保存恢复与单位检查']),
             src('https://gpaw.readthedocs.io/tutorialsexercises/electronic/bandstructures/bandstructures.html','Si原胞案例先做平面波基态并写GPW，再fixed_density沿高对称路径计算能带；本轮只验证粗Gamma基态和重启，不冒称完整能带教程已跑。',['structure','kmesh','band_state'],['bulk','GPAW','fixed_density'],['SCF预算诊断与后续能带任务线索']),
             src('https://gpaw.readthedocs.io/install.html','常规安装需要编译依赖和PAW数据；本轮固定源码cgpaw分派及purepython.py提供官方无C扩展替代，WSL私有venv安装NumPy/SciPy/ASE/gpaw-data。',['backend','paw_dataset'],['GPAW'],['记录版本、数据校验和与后端边界'])],
         [ent('atomic_structure','id symbols positions_A cell_A pbc revision','构造或读入→配置计算器→更改结构使旧计算失效；重置使用新Atoms。'),
          ent('scf_configuration','id mode cutoff_eV xc kmesh nbands mixer convergence maxiter revision','配置→运行→失败保留日志→修复预算后创建新计算器，收敛阈值不放宽。'),
          ent('electronic_result','id source_revision energy_eV forces_eV_A eigenvalues_eV occupations electrons iterations','真实SCF产生→核对电子数/单位/有限值→保存；不把数值有限当精度达标。'),
          ent('restart_artifact','path format_version source_version structure results energy_terms sha256','当前任务写GPW→独立读取字段→新计算器恢复→比较结构和结果。')],
         [{'from':'ASE Atoms.calc','to':'GPAW new ASECalculator','contract':'结构Å、公开能量eV、力eV/Å；非自旋Si2为8价电子，Gamma权重1。占据读取固定raw=True，复制后乘自旋简并2和k权重；不得原地修改后再保存。'},
          {'from':'GPAW ASECalculator.write','to':'GPW v7 -> GPAW(filename)','contract':'GPW results和energy_contributions已是eV，不再次乘Hartree常数；默认文件不包含完整波函数，不声称所有后处理可免重算。'}],
         [{'reference':'runtime.gpaw_fixture_oracle','reason':'NumPy/标准库独立计算a³/4、2×4电子和能量项求和，ASE ULM直接读取序列化字段；它们是验收器而非替代DFT求解器。'},
          {'reference':'runtime.gpaw_source_runtime','reason':'WSL私有Python环境通过sys.path加载未修改的固定GPAW源码，GPAW_NO_C_EXTENSION=1使用官方purepython。包未伪装成已安装的native wheel。'}],
         ['真实新SCF与GPW恢复各重复两轮，但只验证Si2/100eV/Gamma/LDA；未做cutoff、kmesh、力或带隙的生产精度收敛研究。',
          '无编译_gpaw扩展、MPI、LibXC和广泛XC覆盖；密度、电势、应力及fixed_density等选中候选没有全部实跑。',
          '发现26.7.0新版get_occupation_numbers默认raw=False会原地乘权重，导致之后保存/恢复电子数翻倍；使用raw=True+copy避免该上游行为，未修改上游源码。',
          '最新文档含旧mixer fft示例，但26.7.0新Parameters只接受pulay/msr1等；当前用源码支持的pulay，失败证据留README。',
          '这是可验证种子和脚本，不是已封装Agent服务器；本地任务路径隔离、状态ID和清理仍需工程层。'],
         [rec('verify_gpaw.py','repair_scf_budget','对a=5.43Å的Si2原胞执行GPAW SCF，确认maxiter=1在固定阈值下失败；仅将预算修到80并重新计算，检查步数、有限能量/力、8价电子和体积a³/4。','平面波100eV、Gamma、LDA、6 bands、pulay；错误预算1，收敛energy1e-4/density1e-3。',
              [('构造并执行错误预算',gr),('修复预算并验电子数/结果',gr+go+['ase.atoms.Atoms.get_forces'])],['错误预算必须产生KohnShamConvergenceError；正确迭代>1且≤80。','占据权重和8、体积5.43³/4；不设未经外部验证的绝对DFT能量目标。']),
          rec('verify_gpaw.py','repair_restart_energy_units','保存已收敛电子态为GPW并恢复；定位把文件中已为eV的能量再次乘Hartree常数的错误，按文件合同修复，并用独立能量项求和验收，同时验证原子、晶胞和8电子保持。','本轮实际SCF结果和自建GPW v7；文件内ha只作元数据。',
              [('保存并恢复电子态',['gpaw.new.ase_interface.ASECalculator.write','gpaw.new.ase_interface.GPAW','gpaw.new.ase_interface.ASECalculator.get_atoms']),('修复结果单位并检查',['ase.atoms.Atoms.get_potential_energy','gpaw.new.ase_interface.ASECalculator.get_occupation_numbers'])],['错误换算偏离>1eV；修复与独立能量项/原SCF差<1e-10eV。','结构坐标/晶胞保持1e-12，重启后8电子。'])],
         'runtime/gpaw/01.07.01.json','真实官方purepython SCF与GPW重启，正常/失败/修复，独立电子数、几何和文件能量项oracle，重复两次。')

    abipy_symbols=[
        op('abipy','abipy.abio.inputs','AbstractInput','write deepcopy set_vars set_vars_ifnotin pop_vars remove_vars','configure','AbinitInput继承的变量更新、复制与文件输出，只保留配置生命周期。','通过AbinitInput或MultiDataset产生具体输入对象，不直接构造抽象基类。'),
        op('abipy','abipy.abio.inputs','AbinitInput','__init__ as_dict from_dict vars runlevel set_spell_check spell_check to_string structure set_structure replicate set_cutoffs_for_accuracy set_scf_nband_semicond set_kmesh set_gamma_sampling get_ngkpt_shiftk set_kpath pseudos ispaw isnc num_valence_electrons new_with_vars new_with_structure pop_tolerances make_ebands_input make_edos_input','input','保留SCF/NSCF输入准备、变量校验和赝势/价电子元数据；裁去需外部abinit的abivalidate/autoparal与DFPT执行入口。'),
        op('abipy','abipy.abio.inputs','MultiDataset','__init__ from_inputs replicate_input ndtset append extend addnew_from split_datasets deepcopy has_same_structures to_string write','workflow','在内存中组织相关输入并明确拆分，修改一个dataset不污染其他对象；不添加未运行的集群调度器。'),
        op('abipy','abipy.core.structure','Structure','as_structure from_file from_abivars to_abivars from_abistring get_abi_string calc_ngkpt calc_shiftk num_valence_electrons','structure','AbiPy维护唯一输入结构表示，读写ABINIT变量并规划采样；不重复暴露pymatgen和ASE结构编辑集合。','通过已选from_abivars/from_file或AbinitInput结构字典自动构造；底层继承构造不伪造为直接方法。'),
        op('abipy','abipy.electrons.gsr','GsrFile','__init__ from_file ebands is_scf_run ecut structure energy energy_per_atom cart_forces max_force cart_stress_tensor pressure residm xc energy_terms params close as_dict','result','高层打开已存在GSR，读取单位化能量/力/带与收敛元数据并关闭；没有结果文件时不得伪造成功。'),
        op('abipy','abipy.electrons.ebands','ElectronBands','from_file as_dict from_dict to_json nband eigens occfacts shape has_bzmesh has_bzpath kptopt fundamental_gaps direct_gaps get_gaps_string','band','从GSR返回的能带对象作占据/采样和带边检查；不加入未验证插值、GW与图形控件。','由GsrFile.ebands或已选from_file/from_dict工厂产生，不直接手填内部构造。'),
        op('abipy','abipy.electrons.ebands','ElectronTransition','energy qpoint is_direct','band','基本/直接带隙对象的能量和动量关系。','由ElectronBands.fundamental_gaps/direct_gaps产生。'),
        op('abipy','abipy.core.mixins','AbinitNcFile','abinit_version','provenance','读取参考结果写入的真实ABINIT版本，与当前AbiPy包版本分开记录。','由GsrFile继承，不直接构造抽象NetCDF文件类。'),
        op('abipy','abipy.core.mixins','Has_ElectronBands','nelect nkpt nsppol nspinor','band','GsrFile继承的电子数、k点和自旋维度元数据，保留真实定义位置。','由GsrFile继承，不直接构造mixin。')]
    ar=['abipy.abio.inputs.MultiDataset.__init__','abipy.abio.inputs.AbstractInput.set_vars','abipy.abio.inputs.AbinitInput.set_kmesh','abipy.abio.inputs.AbinitInput.set_kpath','abipy.abio.inputs.MultiDataset.split_datasets']
    emit('01.07.02','使用AbiPy建立和修复ABINIT的SCF/NSCF输入链，校验结构、赝势、价电子和dataset隔离；读取官方发布的Si GSR结果，修复Hartree/eV单位并核对能量、占据和带隙。本轮本地验证输入准备与既有输出分析，未新运行ABINIT或DFPT。',
         [('abipy','primary','统一ABINIT输入、dataset、GSR和能带对象；底层pymatgen/netCDF依赖仅作为实现和验收基础，不另暴露重复工具。')],abipy_symbols,
         [src('https://abinit.github.io/abipy/flow_gallery/run_si_ebands.html','官方Si电子能带工作流将SCF均匀网格与NSCF高对称路径分开，MultiDataset再split_datasets交给BandStructureWork；这里只执行输入部分。',['dataset','structure','pseudopotential'],['MultiDataset','set_kmesh','set_kpath','split_datasets'],['修复混用SCF/NSCF角色']),
          src('https://github.com/abinit/abipy/blob/v1.0.0/abipy/examples/flows/run_si_ebands.py','固定v1.0.0案例定义Si primitive cell、14si.pspnc、ecut6以及set_kmesh/set_kpath顺序，作为可重复输入原型。',['input','kmesh','kpath'],['set_vars','set_kmesh','set_kpath'],['构建输入并保存，不假设后台已执行']),
          src('https://github.com/abinit/abipy/blob/v1.0.0/abipy/electrons/tests/test_gsr.py','固定发布测试读取si_scf_GSR.nc并核对能量-241.23647eV、电子数8和能带；参考文件保留真实ABINIT8.0.6 provenance。',['gsr','energy','band'],['GsrFile','energy','ebands'],['独立NetCDF单位/带隙验收']),
          src('https://github.com/abinit/abipy/blob/v1.0.0/abipy/abio/tests/test_inputs.py','输入测试覆盖无效变量、MultiDataset、复制与结构/赝势；参考其错误检测规则但不依赖它给出独立oracle。',['input','dataset','variables'],['set_vars','as_dict','from_dict'],['拼写错误拒绝与序列化恢复'])],
         [ent('abinit_structure','id acell_bohr rprim xred typat znucl natom volume_A3','从ABINIT变量创建→校验坐标/单位/价电子→组成输入。'),
          ent('input_dataset','id structure pseudos variables role revision','SCF/NSCF拆分→配置采样/容差→写abi与JSON；变更只影响目标dataset。'),
          ent('pseudopotential','path sha256 element valence pseudo_type','固定发布文件载入→校验Si和价电子4→与输入/输出provenance关联。'),
          ent('reference_result','path sha256 backend_version energy_eV bands_eV occupations weights','只读官方GSR→检查头和单位→提取指标→独立验收→关闭文件；不当成当前输入运行结果。')],
         [{'from':'AbinitInput/MultiDataset','to':'ABINIT .abi files','contract':'acell来源Bohr、rprim无量纲、xred分数坐标；SCF kptopt1，NSCF kptopt-2/iscf-2。文件只是待运行输入，不能据此产生完成状态。'},
          {'from':'Released ABINIT8.0.6 GSR NetCDF','to':'AbiPy GsrFile/ElectronBands','contract':'原始etotal/eigenvalues为Hartree，公共结果为eV；保留2原子和8价电子。结果来自官方固定参考，不绑定本轮新生成输入为已执行。'}],
         [{'reference':'runtime.abipy_oracle','reason':'netCDF4直接读原始etotal/eigenvalues/occupations/k权重，固定CODATA和独立band min/max算验收；标准库哈希/JSON记录可重复来源。'},
          {'reference':'runtime.abipy_dynamic_dispatch','reason':'MultiDataset.set_vars通过__getattr__向AbinitInput继承AbstractInput.set_vars转发；dataset索引和GsrFile上下文管理为容器/资源协议，不伪造顶层API。'},
          {'reference':'runtime.abipy_structure_attributes','reason':'AbiPy Structure继承pymatgen的volume/len等实体属性；固定几何oracle使用这些只读属性，不重复选择完整pymatgen工具集合。'}],
         ['未安装或运行ABINIT可执行程序，abivalidate、SCF/DFPT新计算、集群队列和流执行不在已验证范围。',
          'GSR参考由ABINIT8.0.6生成，AbiPy当前发布为1.0.0；输入文件和参考输出provenance分开，不能推断当前6Ha/2³网格产生该参考结果。',
          '官方v1.0.0标签源码pyproject存在0.9.8旧值，运行时release和安装分发元数据均为1.0.0，固定commit已记录。',
          '两条任务重复两次且uv pip check通过，选中所有方法并未全部执行；输入文件可生成不代表物理收敛或生产质量。'],
         [rec('verify_abipy.py','repair_scf_nscf_roles','构造Si的两个dataset并拒绝拼错的ecu变量；识别NSCF误用均匀SCF采样，将其改为指定路径，拆分并保存；验证角色、8价电子、独立体积、修改隔离和JSON恢复。','acell10.217Bohr的Si2原胞，固定14si.pspnc；ecut6Ha、nband8。',
              [('建立并修复输入',ar),('检查、保存和恢复',['abipy.abio.inputs.AbinitInput.num_valence_electrons','abipy.abio.inputs.AbstractInput.write','abipy.abio.inputs.AbinitInput.as_dict','abipy.abio.inputs.AbinitInput.from_dict'])],['错误变量必须拒绝；SCF kptopt1、NSCF kptopt-2/iscf-2。','2原子、8电子、体积(10.217×0.529177210903)³/4；修改SCF ecut8不改NSCF ecut6。']),
          rec('verify_abipy.py','repair_gsr_energy_units','读取官方Si GSR并诊断直接把Hartree etotal当eV的结果；修复后用原始NetCDF数值独立核对总能/每原子能、带隙和占据积分，并保留真实ABINIT版本。','只读发布fixture si_scf_GSR.nc，原始etotal=-8.865276768060479Ha；2原子。',
              [('读取公共结果',['abipy.electrons.gsr.GsrFile.__init__','abipy.electrons.gsr.GsrFile.energy','abipy.electrons.gsr.GsrFile.energy_per_atom','abipy.electrons.gsr.GsrFile.ebands']),('校验带隙与来源',['abipy.electrons.ebands.ElectronBands.fundamental_gaps','abipy.electrons.ebands.ElectronTransition.energy','abipy.core.mixins.Has_ElectronBands.nelect','abipy.core.mixins.AbinitNcFile.abinit_version','abipy.electrons.gsr.GsrFile.close'])],['正确总能约-241.236470313eV，每原子除2，错误单位偏差>200eV。','独立带隙约0.5622774098eV、占据积分8，来源ABINIT8.0.6。'])],
         'runtime/abipy/01.07.02.json','真实AbiPy输入生成、错误修复、文件/JSON与官方GSR读取；NetCDF独立单位和band oracle，重复两次，无新ABINIT执行。')

if __name__=='__main__':main()
