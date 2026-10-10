"""Five reviewed ellipsometry scenes, using one optical forward model and lmfit."""
from pathlib import Path
from seed_gen.scripts.scenario_collection_support import Collection,symbol,recipe

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_metrology_quality')

def ec(module,name,methods,cap,why,construction=None):
    return symbol('pyElli','elli.'+module,name,methods,cap,why,construction)
def ef(module,names,cap,why):return [symbol('pyElli','elli.'+module,n,None,cap,why) for n in names.split()]
def lc(module,name,methods,cap,why):return symbol('lmfit','lmfit.'+module,name,methods,cap,why)

def optical_core():
    return [
        ec('dispersions.base_dispersion','BaseDispersion','__init__ get_mat add get_dielectric get_refractive_index get_dielectric_df get_refractive_index_df','dispersion','保留具体色散类继承的构造、材料工厂、参数添加与有单位光谱导出。','通过具体色散类构造；BaseDispersion抽象协议不直接实例化。'),
        ec('dispersions.constant_refractive_index','ConstantRefractiveIndex','refractive_index','dispersion','恒定折射率用于明确的解析参考材料及零色散假设。','继承BaseDispersion.__init__，公开elli.ConstantRefractiveIndex别名可用。'),
        ec('dispersions.cauchy','Cauchy','refractive_index','dispersion','透明薄膜Cauchy模型，显式保留n1前100和n2前1e7的来源单位约定。','继承BaseDispersion.__init__。'),
        ec('materials','IsotropicMaterial','__init__ set_dispersion','state','将色散绑定为等向材料，配置改变使旧谱失效。'),
        ec('materials','SingleMaterial','get_tensor','state','等向材料继承的介电张量查询。','由IsotropicMaterial构造或色散get_mat工厂获得。'),
        ec('materials','Material','get_refractive_index','observe','在波长轴上读取材料折射率张量。','抽象协议由具体材料实现，不直接构造。'),
        ec('structure','Layer','__init__ set_thickness set_material get_permittivity_profile','state','层厚nm、材料引用和修改入口。'),
        ec('structure','Structure','__init__ set_front_material set_back_material set_layers get_permittivity_profile evaluate','solve','从入射半空间到基底的有序堆栈与高层求解入口。'),
        ec('experiment','Experiment','__init__ set_structure set_vector set_theta set_lbda evaluate','configure','波长nm、角度deg与偏振构成独立实验配置。'),
        ec('solver2x2','Solver2x2','calculate','solve','等向层2x2求解器，由Structure.evaluate按solver参数实例化。','构造由继承Solver.__init__并经evaluate工厂完成；不暴露内部矩阵工具。'),
        ec('result','Result','rho psi delta R T jones_matrix_r get as_delta_range','observe','读取复rho及有约定的Psi/Delta与功率；不将周期相位直接做未折返差值。','由Structure/Experiment.evaluate返回，不伪造测量结果。'),
    ]

def fit_tools():
    return [lc('parameter','Parameters','__init__ add add_many copy update valuesdict update_constraints dumps loads','configure','具名参数、边界、约束和保存恢复。'),
        lc('parameter','Parameter','__init__ set value vary expr','configure','调整单参数初值、边界和固定/变化状态。'),
        symbol('lmfit','lmfit.minimizer','minimize',None,'fit','统一残差优化主入口；绑定pyElli正向谱与固定观测。'),
        symbol('lmfit','lmfit.printfuncs','fit_report',None,'diagnose','输出拟合成功状态、参数相关性和拟合统计；不只检查退出码。'),
        symbol('lmfit','lmfit.confidence','conf_interval',None,'diagnose','适用条件满足时分析参数区间，当前无噪声样例不声称区间校准。')]

def main():
    c=Collection(base=BASE,raw=RAW);P='https://pyelli.readthedocs.io/en/latest/'
    evidence={
      P+'auto_examples/plot_01_basic_usage.html':'SiO2/Si完整例子读取NeXus光谱、按角度和波长选择、建立Cauchy材料/膜厚/基底并拟合；文档说明2x2不处理各向异性。',
      P+'read_write.html':'读写章节定义Psi/Delta表以入射角和波长为多重索引，提供NeXus、Woollam、SpectraRay读取；明确当前NeXus只支持读取。',
      P+'result.html':'Result由求解器返回，读取rho、Psi/Delta、Jones与R/T；结果长度对应波长轴，可使用as_delta_range管理相位范围。',
      P+'structure.html':'Structure由入射半空间、有序Layer列表、出射半空间构成；层携带厚度和材料，重复层及渐变层为可选专项。',
      P+'materials.html':'色散经IsotropicMaterial或get_mat变为材料，可查询介电张量；不同晶轴和有效介质属于额外假设。',
      P+'dispersions.html':'波长默认nm，区分一次参数和重复振子参数，列出Cauchy/常数/表格/Sellmeier等模型及其公式。',
      P+'auto_examples/plot_interface_reflection.html':'教程将独立Fresnel反射/透射和pyElli输出比较，使用等向两半空间参考与功率守恒。',
      P+'auto_examples/plot_03_custom_fitting.html':'多角度NeXus输入到Cauchy/厚度模型和lmfit.minimize的完整桥接，用户残差显式拼接不同角度观测。',
      'https://lmfit.github.io/lmfit-py/examples/example_fit_with_bounds.html':'制造样本展示Parameters min/max约束、残差函数和fit_report；边界须包含目标且拟合退出不替代误差检查。',
      'https://lmfit.github.io/lmfit-py/examples/example_fit_with_algebraic_constraint.html':'通过expr表达共享参数/约束，保留拟合统计和参数相关性；说明可辨识性需结合约束而非仅自由拟合。',
    }
    def src(urls):return [c.source(u,evidence[u],['measurement','material','layer_stack','fit_result'],['load','configure','evaluate','fit','diagnose'],['单位/层序/模型或拟合修复并按固定目标验收']) for u in urls]
    def task(sid,name,description,initial,steps,checks):
        return recipe(name,description,initial,steps,checks,'verify_optical.py:'+sid)
    core=optical_core()
    io=ef('importer.nexus','read_nexus_psi_delta read_nexus_rho read_nexus_materials','io','读取NeXus光谱或材料；保留原文件和schema/单位版本。')
    io+=ef('importer.woollam','read_woollam_psi_delta read_woollam_rho','io','常见仪器格式到同一Psi/Delta或rho表示；非本样例实跑格式单列边界。')
    io+=ef('importer.spectraray','read_spectraray_psi_delta read_spectraray_rho','io','补充实际光谱仪格式输入，避免自造泛化reader。')
    io+=ef('utils','calc_rho calc_pseudo_diel conversion_wavelength_energy conversion_wavelength_frequency conversion_wavelength_wavenumber','convert','显式相位约定与波长/能量换算，保持量纲和顺序。')
    designs=[dict(scenario_id='07.06.01',description='椭偏Psi/Delta数据导入与单位核查环境：保留测量文件、入射角、波长及相位约定，将常见仪器/NeXus数据统一到可校验表格和复rho。仅保留必要前向参考用于一致性检查，固定样例明确检查NeXus的Angstrom到nm约定。',
      symbols=core+io,packages=[('pyElli','primary','领域reader、相位转换和前向参考形成数据入口；不再暴露通用HDF5任意写入。')],
      sources=src([P+'read_write.html',P+'auto_examples/plot_01_basic_usage.html',P+'result.html']),
      tasks=[task('07.06.01','load_psi_delta','读取四波长制造NeXus数据，核对60°角度与400/500/600/700nm轴，并按rho=tan(Psi)exp(-iDelta)独立验收复数光谱。','原文件波长4000/5000/6000/7000 Angstrom，Psi[30,32,34,36]°和Delta[170,175,180,185]°。',[
          ('读取并核对角度/波长/观测',['elli.importer.nexus.read_nexus_psi_delta']),('计算复rho',['elli.utils.calc_rho'])],['轴和数据atol1e-12','负相位约定的rho atol1e-12']),
        task('07.06.01','repair_wavelength_unit','发现nm数值误写入读取器要求Angstrom的字段，按原始单位证据修复文件副本再读入；拒绝40–70nm并恢复400–700nm及一致rho。','错误副本波长字段为400/500/600/700，原始仪器量纲nm。',[
          ('读出错误波长定位量纲',['elli.importer.nexus.read_nexus_psi_delta']),('重读修复副本和rho',['elli.importer.nexus.read_nexus_rho'])],['错误轴必须失败','修复轴和rho与原固定目标一致'])])]
    stack_symbols=core+[ec('structure','RepeatedLayers','__init__ set_repetitions set_layers get_permittivity_profile','state','周期堆栈作为常见可组合结构，裁去扭转/渐变专项。'),
        ec('dispersions.table_index','Table','__init__ refractive_index','dispersion','实测/已知表格光学常数输入，范围外预测要显式处理。')]+ef('utils','calc_rho conversion_wavelength_energy','convert','测量复rho和波长单位转换。')
    designs.append(dict(scenario_id='07.06.02',description='用材料、nm厚度、层顺序与实验角度构建等向薄膜堆栈并求解椭偏谱，显式区分入射/出射半空间；正向光谱通过独立单膜Fresnel递推验证，并支持层序修复和零厚度界面极限。',
      symbols=stack_symbols,packages=[('pyElli','primary','同一领域对象链负责色散→材料→层→实验→Result，避免重复传输矩阵求解器。')],
      sources=src([P+'structure.html',P+'materials.html',P+'auto_examples/plot_interface_reflection.html',P+'auto_examples/plot_01_basic_usage.html']),
      tasks=[task('07.06.02','construct_optical_stack','建立空气/23nm透明薄膜/吸收基底，在400–800nm、60°输出rho和Psi；验收独立单膜Fresnel递推以及零膜厚界面极限。','nfilm1.46、nsubstrate3.8+0.02i，21个波长，等向2x2。',[
          ('构造材料层与堆栈',['elli.dispersions.base_dispersion.BaseDispersion.__init__','elli.dispersions.base_dispersion.BaseDispersion.get_mat','elli.structure.Layer.__init__','elli.structure.Structure.__init__']),('求解并读取',['elli.structure.Structure.evaluate','elli.result.Result.rho','elli.result.Result.psi'])],['rho atol1e-12','Psi atol1e-11','零厚度与解析界面一致']),
        task('07.06.02','repair_stack_order','发现入射介质与基底颠倒后按实验光路恢复空气→薄膜→基底，重算并通过相同复rho目标，禁止仅用求解成功判完成。','错置前后半空间但保留原实验记录。',[
          ('按光路重建前后介质',['elli.structure.Structure.__init__']),('重求解并验收rho',['elli.structure.Structure.evaluate','elli.result.Result.rho'])],['颠倒误差>0.1','恢复误差<1e-12'])]))
    dispersion_symbols=core+[ec('dispersions.table_index','Table','__init__ refractive_index','dispersion','表格折射率作为独立候选。'),
        ec('dispersions.sellmeier','Sellmeier','dielectric_function','dispersion','透明材料共振色散候选，参数/波长适用域保留。','继承BaseDispersion构造。'),
        ec('dispersions.lorentz_energy','LorentzEnergy','dielectric_function','dispersion','吸收薄膜振子候选，区别透明Cauchy假设。','继承BaseDispersion构造。'),
        ec('dispersions.tauc_lorentz','TaucLorentz','dielectric_function','dispersion','吸收边候选，当前固定验证只覆盖Cauchy。','继承BaseDispersion构造。'),
        ec('dispersions.base_dispersion','DispersionFactory','get_dispersion','configure','由名称获取发布版色散，保留显式模型选择。','工厂静态入口无需实例化。')]+ef('utils','conversion_wavelength_energy calc_rho','convert','模型单位与观测复rho转换。')
    designs.append(dict(scenario_id='07.06.03',description='为透明/吸收膜选择有物理含义的色散候选，记录波长单位、参数尺度和适用范围。固定透明膜样例用独立n(lambda)检验Cauchy并拒绝常数近似，修复n1的100倍系数误读；未将参考振子模型全部宣称运行通过。',
      symbols=dispersion_symbols,packages=[('pyElli','primary','统一色散协议及材料/谱桥接；不同模型作为受控候选而非重复求解包。')],
      sources=src([P+'dispersions.html',P+'materials.html',P+'auto_examples/plot_01_basic_usage.html']),
      tasks=[task('07.06.03','select_dispersion_model','对五个固定透明膜折射率比较常数与Cauchy模型，选择匹配n=1.45+10000/lambda_nm²的模型并校验所有波长。','400/500/600/700/800nm与明确生成公式。',[
          ('构造并查询候选色散',['elli.dispersions.base_dispersion.BaseDispersion.__init__','elli.dispersions.constant_refractive_index.ConstantRefractiveIndex.refractive_index','elli.dispersions.cauchy.Cauchy.refractive_index'])],['Cauchy atol1e-12','常数最大误差.0625须拒绝']),
        task('07.06.03','repair_cauchy_units','依据pyElli公式中100*n1/lambda²将误填10000的n1修复为100，重新输出折射率并通过原始五点目标。','错误n1=10000，独立目标系数10000nm²。',[
          ('修复参数重新构造并查询',['elli.dispersions.base_dispersion.BaseDispersion.__init__','elli.dispersions.cauchy.Cauchy.refractive_index'])],['错误最大误差6.1875','修复atol1e-12'])]))
    for sid,desc,urls,tasks in [
      ('07.06.04','多角度多波长薄膜d/n/k反演环境：以pyElli为唯一光学主实现，lmfit负责具名参数、边界和残差优化，观测/参数/预测均有版本引用。固定目标由独立Fresnel递推生成，检验23nm及n/k，修复错误厚度边界。',
       [P+'auto_examples/plot_03_custom_fitting.html',P+'auto_examples/plot_01_basic_usage.html','https://lmfit.github.io/lmfit-py/examples/example_fit_with_bounds.html'],[
        task('07.06.04','fit_thickness_n_k','用55°和70°、400–800nm制造谱联合拟合d/n/k，验收隐藏目标23nm、1.8、0.08与复rho残差，保持两角度共享同一薄膜。','独立Fresnel生成42复数观测；初值d15/n1.6/k.04；物理边界内。',[
          ('配置具名有界参数',['lmfit.parameter.Parameters.__init__','lmfit.parameter.Parameters.add']),('光学残差优化',['elli.structure.Structure.evaluate','elli.result.Result.rho','lmfit.minimizer.minimize'])],['d/n/k atol2e-5','max复分量残差<1e-8','success且参数目标均通过']),
        task('07.06.04','repair_fit_bounds','检测厚度下界40nm排除23nm目标导致的残差，修复边界后重拟合并保存恢复参数；验收相同光谱和d/n/k目标。','错误d范围40–80nm，原始允许范围1–80nm。',[
          ('复制并修复边界',['lmfit.parameter.Parameters.copy','lmfit.parameter.Parameter.set']),('重拟合保存恢复',['lmfit.minimizer.minimize','lmfit.parameter.Parameters.dumps','lmfit.parameter.Parameters.loads'])],['错误max残差>1e-4','修复参数atol2e-5','恢复预测残差<1e-8'])]),
      ('07.06.05','面向椭偏拟合质量的训练/验证分离环境：固定已知厚度，比较色散模型在独立波长上的复rho残差，拒绝单波长零训练误差却失配全谱的模型；修复后仍用未参加拟合的波长验收。参考区间/相关性接口不等于已验证统计覆盖率。',
       [P+'auto_examples/plot_03_custom_fitting.html',P+'dispersions.html','https://lmfit.github.io/lmfit-py/examples/example_fit_with_algebraic_constraint.html'],[
        task('07.06.05','reject_training_only_fit','对仅600nm校准的常数折射率模型检查六个保留波长，证明训练误差近零仍不能通过谱外验证并拒绝模型。','真实制造色散n=1.45+18000/lambda²、已知厚度23nm；验证波长400/450/550/650/750/800nm。',[
          ('构造常数模型并分别预测训练/验证谱',['elli.dispersions.base_dispersion.BaseDispersion.__init__','elli.structure.Structure.evaluate','elli.result.Result.rho'])],['训练误差<1e-12','保留谱误差>1e-3必须拒绝']),
        task('07.06.05','repair_spectral_model','改用Cauchy模型，仅用500/700nm两条训练谱拟合n0/n1，再对六个固定验证波长预测；恢复n0=1.45/n1=180且验证误差低于1e-6。','独立Fresnel目标和固定验证阈值；不把验证谱交给优化器。',[
          ('配置色散参数并拟合',['lmfit.parameter.Parameters.__init__','lmfit.parameter.Parameters.add','lmfit.minimizer.minimize','elli.dispersions.base_dispersion.BaseDispersion.get_mat']),('预测保留谱',['elli.structure.Structure.evaluate','elli.result.Result.rho'])],['n0/n1 atol2e-3','保留波长max误差<1e-6'])])]:
        designs.append(dict(scenario_id=sid,description=desc,symbols=core+fit_tools(),packages=[('pyElli','primary','负责唯一光学前向模型、材料与谱结果。'),('lmfit','complement','仅负责有界残差优化/参数管理，不引入第二个光学模型。')],sources=src(urls),tasks=tasks))
    for d in designs:
        d['capabilities']=list(dict.fromkeys(s['capability'] for s in d['symbols']))
        d['entities']=[
          {'name':'measurement','identity':'measurement_id','attributes':['file/source','wavelength_nm','angle_deg','psi_deg','delta_deg','rho_convention','revision','training_or_holdout'],'lifecycle':'读取→校验轴/单位→形成不可变观测；修正副本留原文，修改输入使拟合失效。'},
          {'name':'optical_stack','identity':'stack_id','attributes':['front_material','ordered_layers','material_dispersion','thickness_nm','back_material','revision'],'lifecycle':'构造→改层序/参数→求解→绑定Result；变更材料/厚度使旧结果失效。'},
          {'name':'fit_config','identity':'fit_id','attributes':['free/fixed parameters','bounds','training_indices','holdout_indices','residual_convention','tolerance'],'lifecycle':'配置→拟合→训练和保留谱分别检查→保存恢复参数；不能用测试谱调优后继续当独立验证。'},
          {'name':'spectrum_result','identity':'result_id','attributes':['source_revisions','rho','psi/delta convention','residuals','fit_status'],'lifecycle':'计算→解析/保留谱验收→导出；重置回初始数据和参数重新生成。'}]
        d['bridges']=[{'from':'pyElli dispersion -> material -> Layer/Structure -> Result','to':'complex rho and when selected lmfit residual vector','contract':'lambda和厚度nm，角度deg；rho=tan(Psi)exp(-iDelta)。残差拼接real/imag跨波长/角度，保留共享参数与样本标签。'}]
        d['runtime_infrastructure']=[{'reference':'python.numpy_pandas_json_h5py','kind':'infrastructure','reason':'制造NeXus文件、数组/表格、单位修复、独立Fresnel oracle和结果存储。h5py只写固定fixture；没有实现新的通用数据工具或另一套任务求解器。'},
          {'reference':'installed.packaging','kind':'hard_dependency','reason':'pyElli0.23.1在importer.spectraray导入packaging但未在最小依赖中声明，本venv显式安装26.3后通过import。'}]
        d['boundaries']=['固定制造样例；不是实测薄膜参数认证、完整Agent环境或所有参考接口实跑。','当前验证等向单膜2x2；各向异性/渐变层/粗糙度和多解性需后续专门样例。','不使用在线RII数据库；发布源码数据子模块未初始化并记录，固定材料常数独立保存。','标准读入器波长Angstrom→nm及负Delta约定按发布源码明确保留；其他仪器格式仅参考。']
        n=sum(len(s['methods']) if s['type']=='class' else 1 for s in d['symbols'])
        if n<50:d['count_exception']=f'当前场景保留{n}项：通用继承构造/输出只计一次，必要读取/构造/配置/求解/观察已闭合；不添加不相关各向异性/随机拟合动作凑数。'
        d['runtime_report']=(BASE/f'runtime/optical/{d["scenario_id"]}.json').as_posix()
        d['runtime_scope']='固定正常、错误、修复与独立Fresnel/单位oracle；不声称所有工具执行。'
        c.write(d)

if __name__=='__main__':main()
