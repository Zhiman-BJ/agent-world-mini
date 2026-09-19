"""Joint Kwant/tkwant selection around verified static and transient tasks."""
from seed_gen.scripts.scenario_collection_support import Collection, symbol, recipe

BASE='seed_gen/scenario_collection/'
SCRIPT=BASE+'verify_materials_transport.py'


def k(module,name,methods,cap,reason,construction=None):
    return symbol('kwant','kwant.'+module,name,methods,cap,reason,construction)


def t(module,name,methods,cap,reason,construction=None):
    return symbol('tkwant','tkwant.'+module,name,methods,cap,reason,construction)


def geometry():
    return [k('lattice','chain',None,'geometry','一维器件/导线的统一晶格工厂，固定norbs以支持局域算子。'),
            k('lattice','square',None,'geometry','二维条带扩展入口，不再重复通用晶格/其他形状工厂。'),
            k('builder','Builder','__init__ sites site_value_pairs hoppings hopping_value_pairs neighbors degree dangling eradicate_dangling attach_lead finalized reversed','geometry','构造、连接诊断、悬挂点修复与显式finalize生命周期。'),
            k('builder','SiteFamily','__call__','geometry','晶格实例到Site的真实继承调用协议。','由chain/square工厂生成Monatomic，继承此方法；不自行构造抽象family。'),
            k('lattice','Polyatomic','neighbors shape vec','geometry','Monatomic实际继承的近邻、几何选择与晶格向量。','由chain/square产生的晶格继承，不重复创建Polyatomic对象。'),
            k('lattice','Monatomic','pos closest','geometry','按坐标定位格点，与矩阵/观测索引建立对应。','由chain/square产生。'),
            k('lattice','TranslationalSymmetry','__init__ reversed which act','leads','周期方向、反向导线与基本胞映射。'),
            k('system','System','hamiltonian_submatrix hamiltonian','diagnose','真实Cython绑定矩阵提取和矩阵元素诊断。','Builder.finalized返回FiniteSystem，通过基类继承方法。'),
            k('system','InfiniteSystem','cell_hamiltonian inter_cell_hopping modes selfenergy','leads','真实半无限lead的矩阵与开放边界诊断。','finalized系统leads字段产生，接口来自所列基类。')]


INFRA=[{'reference':'runtime.builder_mapping','reason':'已选Builder支持真实字典式onsite/hopping赋值；常规__setitem__未作为独立工具计数，封装时需白名单化site/hopping配置。Site集合/params/结果数组由已选工具创建，不另造物理求解器。'},
       {'reference':'runtime.configuration_archive','reason':'Python标准JSON保存显式可重建输入，NumPy NPZ保存数值结果；修改参数/边界需新建系统和新波函数，不能复用旧解。'}]


def static(c):
    symbols=geometry()+[
        k('solvers.common','SparseSolver','smatrix wave_function ldos greens_function','solve','公开kwant.smatrix/wave_function等为default.hidden_instance继承这些真实方法的绑定别名。','库在solvers.default初始化MUMPS或SciPy后端单例；只用公开绑定入口，不构造抽象SparseSolver。'),
        k('solvers.common','SMatrix','transmission num_propagating out_block_coords in_block_coords','observe','散射透射、传播通道及lead块索引；不开放结果伪造构造。','由kwant.smatrix返回。'),
        k('solvers.common','BlockResult','submatrix conductance_matrix block_coords','observe','SMatrix实际继承的块矩阵与电导观测。','SMatrix继承；由求解结果获得。'),
        k('solvers.common','WaveFunction','__call__','solve','按入射lead读取通量归一散射态。','由kwant.wave_function返回，不手造成功解。'),
        k('physics.dispersion','Bands','__init__ __call__','diagnose','周期导线色散与速度诊断；实际比较-2cos(k)。'),
        k('operator','Density','__init__ __call__ bind act tocoo','observe','局域密度与矩阵表征；原生公开Cython API。'),
        k('operator','Current','__init__ __call__ bind act','observe','按有向键求局域流，方向必须与oracle一致。'),
        k('operator','Source','__init__ __call__ bind act','diagnose','局域源项定位连续性问题。'),
        k('system','FiniteSystem','precalculate validate_symmetries','diagnose','同能量lead预计算与对称检查，配置修改使预计算失效。','Builder.finalized返回类继承这些基类方法。')]
    c.write(dict(scenario_id='01.09.01',
        description='构建散射区和半无限导线，扫描能量求透射、电导与局域电流；以解析单杂质透射、散射矩阵幺正性和电流连续性验证势垒/跃迁修复，形成可保存重建的静态量子输运任务。',
        packages=[('kwant','primary','同一包拥有Builder、lead、散射求解和局域算子，避免重复tight-binding状态；本场景无需tkwant的时间演化。')],
        symbols=symbols,
        sources=[c.source('https://kwant-project.org/doc/1/tutorial/first_steps','官方完整量子线例子从离散Hamiltonian、Builder、两个lead到Landauer透射，采用t=a=1单位。','lattice device lead energy','Builder attach_lead smatrix','建器件并扫电导'),
                 c.source('https://kwant-project.org/doc/1/tutorial/operators','官方局域观测教程以实际Density/Current/Source及散射态计算密度和流；本轮限定单轨道标量。','state current density','wave_function Current Density Source','有向键电流与连续性诊断'),
                 c.source('https://kwant-project.org/doc/1/tutorial/faq','官方FAQ区分可修改Builder与用于计算的finalized系统，并解释site family和矩阵索引；用于状态生命周期。','builder finalized_system site_order','finalized sites hamiltonian_submatrix','配置变化后重新finalize与索引核对')],
        entities=[dict(name='device_model',attributes=['id','sites','hoppings','barrier','norbs','revision'],lifecycle='构造→修改势垒/键→检查连通→finalize；上游变更使全部结果失效。'),
                  dict(name='lead',attributes=['id','period','unit_cell','coupling','orientation'],lifecycle='构造周期胞→接触匹配→附接→求色散/自能。'),
                  dict(name='scattering_result',attributes=['id','model_revision','energy','s_matrix','transmission','units'],lifecycle='求解→检查通道/幺正→读电导→归档；求解完成不自动代表达到目标。'),
                  dict(name='local_observable',attributes=['id','incoming_lead','mode','site_order','bond_orientation','density','current'],lifecycle='散射态→定向算子→连续性/源项→保存重建核验。')],
        capabilities=['geometry','leads','diagnose','solve','observe'],
        bridges=[{'from':'Builder.finalized','to':'default sparse solver -> SMatrix/WaveFunction -> operator','contract':'单轨道norbs=1；散射态按单位入射通量归一，电流边(i+1,i)正向。能量以|hopping|=1计，电导单位e²/h，site索引必须使用finalized顺序。'}],
        runtime_infrastructure=INFRA+[{'reference':'runtime.scattering_oracle','reason':'NumPy独立公式T=(4-E²)/(4-E²+U²)、S†S=I、2Im(psi_i*Hij psi_j)与-2cos(k)只做验收。'}],
        boundaries=['固定15格点一维相干单电子链，没有真实材料参数、二维器件、无序统计、非弹性或自洽相互作用；参考二维/LDOS接口未全部运行。',
                    'WSL实际Kwant1.5.0；python-mumps0.0.6元数据要求NumPy>=2，与tkwant1.1.0的NumPy1.26组合冲突，锁定0.0.4后pip check通过。',
                    '公开solver别名来自已核查default.py的绑定；原生算子继承_LocalOperator由专用解析适配器追溯，不伪造源docstring。'],
        tasks=[recipe('repair_barrier_and_conductance','给定15格点两端导线和目标势垒U=2，识别U=2000造成的透射错误；修复并扫描13个能点，核验解析透射、S矩阵幺正、导线色散，保存配置后重建得到相同电导。','E∈[-1.5,1.5]，hopping=-1，错误U=2000。',[
            ('建晶格/散射区和导线',['kwant.lattice.chain','kwant.builder.Builder.__init__','kwant.builder.SiteFamily.__call__','kwant.lattice.Polyatomic.neighbors','kwant.lattice.TranslationalSymmetry.__init__','kwant.builder.Builder.attach_lead','kwant.builder.Builder.reversed','kwant.builder.Builder.finalized']),
            ('扫能并诊断',['kwant.solvers.common.SparseSolver.smatrix','kwant.solvers.common.SMatrix.transmission','kwant.physics.dispersion.Bands.__init__','kwant.physics.dispersion.Bands.__call__']),
            ('修配置后重建与比较',['kwant.builder.Builder.finalized','kwant.solvers.common.SparseSolver.smatrix','kwant.solvers.common.SMatrix.transmission'])],['T(0)从约1e-6恢复.5；13点解析透射误差<1e-12；S†S与通道守恒、存储重建一致。'],SCRIPT),
               recipe('repair_bond_and_current','定位中央弱键导致单位透射目标失败，将hopping=-.25恢复-1；在E=.3求左入射散射态，检查全部14键电流为1，密度和源项符合独立算术，并保存数值结果。','15格点、无势垒、错误中央键-.25；目标均匀导线。',[
                   ('诊断弱键透射',['kwant.solvers.common.SparseSolver.smatrix','kwant.solvers.common.SMatrix.transmission']),
                   ('修复与求波函数',['kwant.builder.Builder.finalized','kwant.solvers.common.SparseSolver.wave_function','kwant.solvers.common.WaveFunction.__call__']),
                   ('检查矩阵/密度/有向键流',['kwant.system.System.hamiltonian_submatrix','kwant.operator.Current.__init__','kwant.operator.Current.__call__','kwant.operator.Density.__init__','kwant.operator.Density.__call__','kwant.operator.Source.__init__','kwant.operator.Source.__call__'])],['错误T(0)=.2214532872；修复T=1；14个键电流与独立公式差<1e-12，Source=0。'],SCRIPT)],
        runtime_report=BASE+'runtime/materials_transport/01.09.01.json',runtime_scope='真实Kwant/MUMPS散射求解，两任务两次重复；解析透射、矩阵/电流和配置重建独立验收。'))


def transient(c):
    c.write(dict(scenario_id='01.09.02',
        description='用Kwant定义有限区和含时跃迁，tkwant传播单粒子波函数并读取密度/电流；以可手算脉冲与无限链Bessel解验证脉冲幅度和导线缓冲长度修复，约束时间只能前进及修改后重新初始化。',
        packages=[('tkwant','primary','拥有时间演化、单粒子状态、导线边界和脉冲入口；固定官方v1.1.1提交，内部包版本为1.1.0。'),
                  ('kwant','complement','tkwant硬依赖，保留真实几何/导线/局域算子桥接，不重复暴露静态求解器及另一套状态。')],
        symbols=geometry()+[
            t('onebody.onebody','WaveFunction','__init__ from_kwant psi evolve evaluate add_perturbation','evolve','单一动态状态、已选Kwant桥接、前向演化与观测；修改Hamiltonian后新建实例。'),
            t('onebody.onebody','ScatteringStates','__init__','initial_state','官方开放系统平衡散射初态高层工厂，仅保留单粒子流程。'),
            t('leads','automatic_boundary',None,'boundary','自动边界是需验收的建议，不能将refl_max当实际误差证明。'),
            t('leads','SimpleBoundary','__init__ __call__','boundary','显式缓冲区细化，实际从6修到32格点。'),
            t('leads','MonomialAbsorbingBoundary','__init__','boundary','需要较长时间时的正式吸收边界替代，未声称本样例运行。'),
            t('leads','Boundary','num_absorb_cells num_buffer_cells num_total_cells','boundary','统一读取/诊断真实边界长度。','由automatic_boundary或已选具体边界构造产生。'),
            t('leads','add_voltage',None,'initial_state','官方lead电压脉冲转换为积分相位耦合入口；本固定任务用可解析跃迁脉冲，未声称校准电压器件。'),
            k('operator','Density','__init__ __call__','observe','单粒子位点概率，动态评价由tkwant提供实际time参数。'),
            k('operator','Current','__init__ __call__','observe','有向键概率流，不将单电子流冒称热费米海总电流。')],
        sources=[c.source('https://tkwant.kwant-project.org/doc/stable/tutorial/onebody.html','官方同时展示有限链、两个半无限导线、automatic_boundary和from_kwant传播；初态可为局域波包或散射态。','system wavefunction boundaries','from_kwant automatic_boundary evolve evaluate','有限/开放边界比较及密度轨迹'),
                 c.source('https://tkwant.kwant-project.org/doc/stable/tutorial/time_dep_system.html','官方解释onsite与coupling回调中的time参数，以及电压积分相位/gauge变换，不允许把瞬时电压直接当相位。','pulse callback phase','Builder add_voltage','含时配置/单位的故障修复'),
                 c.source('https://tkwant.kwant-project.org/doc/stable/tutorial/open_system.html','官方量子点案例以一个入射散射态传播并评估指定接触有向键Current；用于界定单粒子流而非多体总电流。','state contact current','ScatteringStates Current evolve','局域电流、正方向与传播观测')],
        entities=[dict(name='time_dependent_device',attributes=['id','lattice','hopping_callback','pulse_amplitude','revision'],lifecycle='构建可调用含时Hamiltonian→finalize→修改后重建，禁止复用旧状态。'),
                  dict(name='boundary_configuration',attributes=['id','lead_id','buffer_cells','absorb_cells','tmax','measured_error'],lifecycle='自动估计→与独立解比较→显式细化→重建状态；refl_max不替代实际误差。'),
                  dict(name='wavefunction',attributes=['id','model_revision','initial_state','time','psi','boundary_revision'],lifecycle='初始化→时间单向演化→观测；回退需从初态重跑。'),
                  dict(name='transient_trace',attributes=['id','times','probabilities','currents','units','oracle_error'],lifecycle='采样→脉冲/边界目标检验→JSON配置与NPZ结果存档→重放。')],
        capabilities=['geometry','leads','diagnose','evolve','initial_state','boundary','observe'],
        bridges=[{'from':'Kwant finalized system and operators','to':'tkwant.onebody.WaveFunction.from_kwant/evaluate','contract':'每site一个轨道，复数初态按finalized矩阵顺序；回调名time由tkwant供给，其他参数通过params；hbar=|hopping|=1。'}],
        runtime_infrastructure=INFRA+[{'reference':'runtime.transient_oracle','reason':'NumPy闭式theta=t+A(1-cos(t))及sin²theta、导数电流；SciPy特殊函数J_n(2t)为无限链独立解析解，未用另一个ODE求解器替代tkwant。ScatteringStates序列取值是已选工厂结果访问。'}],
        boundaries=['实际为两位点跃迁脉冲及7位点中心区接双lead的局域初态，无多体费米积分、自洽、实器件电压标定或材料输运预测。',
                    'tkwant v1.1.1固定commit b2040b88d56b90288f1b6617e7f2070ec1db7ef5，内部version/PyPI1.1.0；WSL从该commit原生编译，direct_url核验，不用conda旧rc版。',
                    '自动边界在tmax6时返回6个SimpleBoundary单元，refl_max从1e-6改到1e-12仍有约.00225密度误差；修为32单元后按同一3e-6目标验收。该结果不推断所有系统自动边界失效。',
                    '参考add_voltage、ScatteringStates和吸收多项式边界未纳入当前四次任务实跑；边界适用时间由后续facade强制，不能依赖库仅warning的t>tmax行为。'],
        count_exception='保留有限/开放系统构造、脉冲、初态、边界修复、传播、观测和存储重建闭环；49个操作已覆盖该局部单粒子场景，不追加多体积分或重复静态求解器凑到50。',
        tasks=[recipe('repair_pulse_and_population','对初态[1,0]的两位点模型传播含时跃迁-(1+A sin t)，识别A=.3无法满足目标A=.7；修复后同时检查概率与电流解析轨迹、归一化、回退时间拒绝，再从归档参数重新初始化重放。','hbar=1，0≤t≤2，21采样点；theta=t+.7(1-cos t)。',[
            ('建含时模型和初态',['kwant.lattice.chain','kwant.builder.Builder.__init__','kwant.builder.Builder.finalized','tkwant.onebody.onebody.WaveFunction.from_kwant']),
            ('演化并读概率和流',['tkwant.onebody.onebody.WaveFunction.evolve','tkwant.onebody.onebody.WaveFunction.evaluate','kwant.operator.Density.__init__','kwant.operator.Current.__init__']),
            ('修复幅度后重建并重放',['tkwant.onebody.onebody.WaveFunction.from_kwant','tkwant.onebody.onebody.WaveFunction.evolve','tkwant.onebody.onebody.WaveFunction.evaluate'])],['P_right=sin²theta误差<2e-6；J=(1+.7sin t)sin(2theta)误差<3e-6；归一与重放通过，倒退时间报错。'],SCRIPT),
               recipe('repair_open_boundary','从中央单格点激发比较有限链与开放导线，发现闭边界及自动6格缓冲均不符合无限链目标；改成两端各32格缓冲后重跑，核对25时刻Bessel密度和有向流，确认中心区概率流出而非被错误强制归一。','中心区7格，tmax=6；解析psi_n=i^n J_n(2t)，初态中央delta。',[
                   ('接lead并检查自动边界',['kwant.lattice.TranslationalSymmetry.__init__','kwant.builder.Builder.attach_lead','kwant.builder.Builder.finalized','tkwant.leads.automatic_boundary']),
                   ('诊断失败并改显式缓冲',['tkwant.onebody.onebody.WaveFunction.from_kwant','tkwant.onebody.onebody.WaveFunction.evolve','tkwant.onebody.onebody.WaveFunction.evaluate','tkwant.leads.SimpleBoundary.__init__']),
                   ('重新演化与独立解比较',['tkwant.onebody.onebody.WaveFunction.from_kwant','tkwant.onebody.onebody.WaveFunction.evolve','tkwant.onebody.onebody.WaveFunction.evaluate','kwant.operator.Density.__init__','kwant.operator.Current.__init__'])],['自动边界密度误差>1e-3；修复密度/电流误差<3e-6；t6中心区概率<.3而闭链总概率≈1。'],SCRIPT)],
        runtime_report=BASE+'runtime/materials_transport/01.09.02.json',runtime_scope='真实Kwant→tkwant单粒子桥接；脉冲与边界两任务各两次，解析时间积分与无限链Bessel oracle。'))


if __name__=='__main__':
    collection=Collection()
    static(collection)
    transient(collection)
