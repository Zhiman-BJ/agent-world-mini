"""Joint selections favoring stable, verifiable local optical cores."""
from pathlib import Path
from seed_gen.scripts.scenario_collection_support import Collection,symbol,recipe

BASE=Path('seed_gen/scenario_collection/lithography')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/lithography')

def entity(name,attributes,lifecycle):return dict(entity=name,attributes=attributes.split(),lifecycle=lifecycle)
def op(package,module,name,methods,capability,reason,construction=None):
    return symbol(package,module,name,methods,capability,reason,construction)

def optics():
    rows=[op('prysm','prysm.propagation','Wavefront','__init__ from_amp_and_phase thin_lens intensity phase copy pad2d crop free_space focus unfocus focus_fixed_sampling unfocus_fixed_sampling','state','统一复振幅、采样和波长，显式传播/裁剪/复制状态，不加入干涉仪、望远镜或射线追迹长尾。'),
          op('prysm','prysm._richdata','RichData','shape size x y support_x support_y support copy slices','observe','Wavefront.intensity等返回带网格的数据对象，保留尺寸/坐标/切片和复制。','由已选Wavefront.intensity/phase返回，不直接构造抽象数据基类。')]
    for module,names,cap,reason in [
        ('coordinates','make_xy_grid cart_to_polar polar_to_cart','geometry','显式网格、极坐标瞳孔与单位映射。'),
        ('geometry','circle rectangle gaussian','geometry','常见瞳孔与局部测试振幅形状，不再暴露重复几何构造。'),
        ('propagation','focus unfocus angular_spectrum angular_spectrum_transfer_function pupil_sample_to_psf_sample psf_sample_to_pupil_sample','propagate','真实公开传播入口与采样换算；固定样例用Q=1周期边界，未当成无限自由空间。'),
        ('fttools','pad2d crop_center forward_ft_unit','sampling','边界、填充、裁剪和空间频率坐标，帮助诊断采样混淆。')]:
        rows += [op('prysm','prysm.'+module,n,None,cap,reason) for n in names.split()]
    return rows

def layout():
    rows=[op('gdstk','gdstk','Library','__init__ new_cell add top_level layers_and_datatypes write_gds write_oas','layout','规范GDS/OASIS库、单位、层与保存生命周期。'),
          op('gdstk','gdstk','Cell','__init__ add get_polygons bounding_box copy filter remove write_svg','layout','单元和层选择、对象查询及编辑，避免整体扁平化破坏其他层。'),
          op('gdstk','gdstk','Polygon','__init__ copy area bounding_box translate scale rotate contain','mask','多边形及面积、坐标、局部修正与独立几何验收。')]
    return rows+[op('gdstk','gdstk',n,None,'mask','局部掩膜构造、偏置、固定像素中心栅格化与GDS读取；不使用包外未声明rasterizer。') for n in ['rectangle','offset','inside','read_gds']]

def optimizer():
    return [op('scipy','scipy.optimize._minimize','minimize',None,'optimize','单一有界L-BFGS-B入口；03用数值梯度，04显式交给自动微分的loss/jac。'),
            op('scipy','scipy.optimize._constraints','Bounds','__init__ residual','optimize','可修改的透射率范围与边界违反检查，不因优化器退出正常就验收。'),
            op('scipy','scipy.optimize._optimize','OptimizeResult',[],'observe','运行结果容器的x/fun/nit/success/message属性；不伪造动态dict方法为源定义函数。','由已选minimize返回，继承dict的数据协议不新增计数。'),
            op('scipy','scipy.optimize._optimize','approx_fprime',None,'diagnostic','可选数值梯度诊断；独立验收额外使用中央方向差分。'),
            op('scipy','scipy.optimize._optimize','check_grad',None,'diagnostic','优化前梯度差异诊断，防止错误Jacobian被当作模型质量问题。')]

def main():
    c=Collection(base=BASE,raw=RAW)
    def src(url,evidence):return c.source(url,evidence,['mask','field','process','result'],['construct','propagate','correct','verify'],['local_fixed_mask_repair'])
    prysm_sources=[
        src('https://prysm.readthedocs.io/en/stable/tutorials/First-Diffraction-Model.html','官方教程从网格、圆形瞳孔、振幅/相位构成复波前并传播成PSF，说明构造→成像链和波长单位。'),
        src('https://prysm.readthedocs.io/en/stable/api/propagation.html','focus/unfocus、Wavefront和angular_spectrum公开定义；prysm传播单位wvl为微米、dx/z为毫米，桥接必须显式。'),
        src('https://prysm.readthedocs.io/en/stable/how-tos/Radiometrically-Correct-Modeling.html','官方辐射度教程解释FFT norm与输入条件如何保持功率，支持独立能量和强度尺度验收。')]
    gds_source=src('https://heitzmann.github.io/gdstk/gettingstarted.html','官方库/单元/多边形与GDSII写入案例定义层次几何及库单位；运行时按lib.unit米换算nm像素中心。')
    scipy_source=src('https://docs.scipy.org/doc/scipy/reference/optimize.minimize-lbfgsb.html','L-BFGS-B支持box bounds、maxiter和投影梯度停止；文档标1.18.0，签名按安装/源码1.18.1核对。')
    original_source=src('https://github.com/VLSIDA/lithosim/blob/b3868e025716ba1c236f892345d57709f0cebacd/lithosim.py','固定研究源码提供GDS→mask→成像→contour/EPE思路，但anneal随机选点循环退出阈值至少8.2且rng.random<1，不能退出；未发布，不作为正式优化器。')
    entities=[entity('mask_layout','id cell layer datatype library_unit_m polygons revision','读取或构造→选择层→偏置/修改→重新栅格化；原目标只读，修正另存。'),
              entity('mask_grid','id shape spacing_nm origin_nm transmission target revision','采样→单位/范围检查→变换或优化→NPZ/GDS保存；灰度mask不能自动冒称制造二值版图。'),
              entity('optical_configuration','id wavelength pupil NA propagation_method z dose boundary revision','创建显式单位配置→传播→修改光学参数使所有强度/优化结果失效。'),
              entity('simulation_result','id input_revision complex_field intensity contour objective diagnostics','求解产生→独立DFT/解析公式核验→保存重放；success只作诊断，目标由固定oracle决定。')]
    infra=[{'reference':'runtime.lithography_array_protocol','reason':'NumPy网格数组、复数乘法/模平方、dose强度缩放和显式阈值是状态/比较层；全部衍射传播来自已选prysm API。标准NPZ/JSON负责归档。'},
           {'reference':'runtime.lithography_oracle','reason':'独立显式DFT矩阵乘法及单频Fresnel解析式只在verifier计算，不代替包的实际物理运行；验收错误与正确配置用同一目标。'},
           {'reference':'runtime.lithography_objective','reason':'minimize回调组合已选传播入口、明示强度残差和物理边界；没有隐藏第二光刻求解器。'}]
    common_boundaries=['局部标量相干光学近似，固定16×16周期边界；不声称完整Hopkins部分相干、商用光刻胶、工艺标定或全芯片能力。',
                       '明示强度阈值只定义任务contour比较，不冒充已校准的resist包模型；mask透射率、dose与强度区分。',
                       '所选API集合大于固定任务覆盖；本轮产物是种子和可执行fixture，不是完整Agent服务器。',
                       'lithosim/OpenILT/TorchLitho/TorchLitho-2.0均无release和tag、无已确认PyPI分发，只保留source_kind=unreleased_commit_snapshot的候选/研究证据；任何snapshot不计正式主包。']
    def rec(sid,i,description,initial,steps,assertions,script='verify_stable.py'):
        return recipe(i,description,initial,steps,assertions,(BASE/script).as_posix())
    def emit(sid,description,packages,symbols,sources,tasks,bridges,runtime,extra=(),count_exception=None,additional_infra=()):
        d=dict(scenario_id=sid,description=description,packages=packages,symbols=symbols,sources=sources,entities=entities,capabilities=list(dict.fromkeys(s['capability'] for s in symbols)),bridges=bridges,runtime_infrastructure=infra+list(additional_infra),boundaries=common_boundaries+list(extra),tasks=tasks,runtime_report=(BASE/runtime).as_posix(),runtime_scope='正式发布核心CPU固定任务、错误与修复、独立解析/直接DFT oracle，每项重复两次。')
        if count_exception:d['count_exception']=count_exception
        c.write(d)
    bridge={'from':'gdstk Library/Cell/Polygon','to':'nm pixel-center transmission -> prysm field','contract':'lib.unit单位米，几何用户坐标乘unit×1e9变nm；gdstk.inside查询固定像素中心，保留layer/datatype与坐标原点。01/02 dx40nm、λ193nm，prysm自由传播额外转dx/z毫米、λ微米。'}
    propagation=['prysm.propagation.focus','prysm.propagation.unfocus']
    emit('03.12.01','从GDS选层与库单位建立掩膜透射率网格，通过已发布prysm标量相干成像/菲涅尔传播预测局部aerial image；诊断GDS长度和defocus单位，独立检查强度、dose和归档恢复。以局部光学近似替代未发布光刻研究软件，不宣称完整工业resist。',
         [('prysm','primary','已发布的傅里叶/菲涅尔光学核心与波前状态。'),('gdstk','complement','真实GDS单位、层、多边形与像素中心inside桥接。'),('lithosim','excluded','无发布版；其GDS/SOCS设计作线索，正式运行采用可复现的发布包。'),('TorchLitho-2.0','excluded','无release且高层自定义VJP研究误差约31%；正向场景不需要另一个状态/梯度后端。')],
         optics()+layout(),prysm_sources+[gds_source,original_source],
         [rec('03.12.01','repair_gds_length_units','读取含两个层的GDS并选layer1，将误把微米用户坐标当nm导致的1像素掩膜修为25像素；传播后与独立DFT逐点对照，并保存/回读mask和aerial。','16×16、40nm像素、库unit1e-6m、200nm正方形，NA.65/λ193nm。',
              [('读取与正确栅格化',['gdstk.read_gds','gdstk.Library.top_level','gdstk.Cell.get_polygons','gdstk.inside']),('传播和校验',propagation)],['错误1像素必须失败；正确25像素，直接DFT差<1e-12，归档相同。']),
          rec('03.12.01','repair_defocus_unit_and_dose','在固定正弦灰度掩膜上，将100nm defocus误作100mm的传播配置修为1e-4mm；施加0.8强度dose，输出aerial并与单频Fresnel解析式和平均功率比较。','mask=.5+.25cos，λ.193微米，dx40nm，Q=1周期。',
              [('传播错误与修复配置',['prysm.propagation.angular_spectrum']),('读取传播场并验强度尺度',['prysm.propagation.angular_spectrum'])],['错误单位逐点差>1e-3；修复<1e-12，平均强度0.8×(.25+.0625/2)。'])],
         [bridge],'runtime/stable/03.12.01.json')
    emit('03.12.02','在固定局部相干成像模型下，对目标层掩膜作有限候选的向外边界偏置，比较打印contour误差并保存更优几何；同时修复错误层选择。使用已发布gdstk+prysm闭合几何OPC小任务，避免未发布lithosim无界退火缺陷。',
         [('gdstk','primary','负责显式几何偏置、层选择和修正GDS。'),('prysm','complement','单一成像核心给出固定阈值contour误差。'),('lithosim','excluded','anneal退出条件不可能满足，且无发布版；不修改或模拟其成功输出。'),('TorchLitho','excluded','无发布，旧训练框架与当前几何候选修正重复且负担大。'),('TorchLitho-2.0','excluded','无发布，像素可微后端不是当前有限几何候选任务必需。')],
         optics()+layout(),[gds_source,src('https://heitzmann.github.io/gdstk/geometry/gdstk.offset.html','offset正距离膨胀、负距离腐蚀；join/precision明确顶点形状与网格截断。'),prysm_sources[0],original_source],
         [rec('03.12.02','repair_mask_bias','对初始120nm正方形遍历0/40/80nm向外偏置并模拟，选择固定target contour错误最小者；用独立DFT确认最佳40nm、错误像素由25降到4，保存并回读修正GDS。','目标5×5像素正方形，阈值.35，16×16/40nm网格。',
              [('构造与修改掩膜',['gdstk.rectangle','gdstk.offset','gdstk.inside']),('成像比较并保存',propagation+['gdstk.Library.__init__','gdstk.Library.new_cell','gdstk.Cell.add','gdstk.Library.write_gds','gdstk.read_gds'])],['最佳候选必须为40nm，错误25→4；重新载入掩膜完全一致。']),
          rec('03.12.02','repair_target_layer_before_opc','识别把辅助条带layer9当作目标layer1产生的contour错误；恢复正确层、保留库单位并重新栅格化/成像，验证正方形面积40000nm²及固定4像素残差。','同一GDS两个层，目标只读，禁止改阈值或目标以掩盖选层错误。',
              [('查询目标层和几何',['gdstk.read_gds','gdstk.Cell.get_polygons','gdstk.Polygon.area','gdstk.inside']),('重做成像验收',propagation)],['layer9残差大于4；修复layer1残差4、面积40000nm²。'])],
         [bridge],'runtime/stable/03.12.02.json',extra=['这是有限、可穷举的几何偏置OPC任务；不声称全局最优或一般复杂版图EPE均达标，也不声称运行了lithosim.anneal。'])
    emit('03.12.03','把局部目标强度作为逆问题，使用prysm相干成像与SciPy有界L-BFGS-B优化连续灰度mask；诊断迭代预算和透射率上界并以独立直接DFT验证结果、保存重放。此环境保留OpenILT的仿真→初始化→优化→评估结构，正式运行选已发布替代包。',
         [('prysm','primary','局部光学forward统一由发布版提供。'),('scipy','complement','box约束与数值梯度L-BFGS-B，提供可诊断预算/边界结果。'),('OpenILT','excluded','无稳定release；其64²官方kernel CPU烟测可跑，但不能用研究snapshot充当正式发布；当前灰度强度ILT无需其完整ICCAD核/shot计数。'),('lithosim','excluded','无发布且anneal无限循环，不能作为替代优化器。')],
         optics()+optimizer(),prysm_sources[:2]+[scipy_source,src('https://github.com/OpenOPC/OpenILT/blob/dabb97c6ca3dfd159362e48273c436444c77353b/pyilt/simpleilt.py','SimpleILT显式mask参数化、forward、L2/PVBand损失、SGD与bestMask，启发任务状态和预算；这里只作研究快照线索，不调用其优化器。')],
         [rec('03.12.03','repair_ilt_iteration_budget','从0.3均匀透射率开始对固定解析目标优化，发现maxiter1残差仍超标；提高到30并重跑，保持[0,1]界限，独立DFT验证残差小于1e-9后归档mask。','目标对应.4+.2cos低频振幅的平方；16×16相干周期模型，Q=1。',
              [('配置并运行有界优化',['scipy.optimize._minimize.minimize']),('每次回调与验收',propagation)],['错误预算残差>1e-4；修复独立MSE<1e-9，所有mask值在[0,1]。']),
          rec('03.12.03','repair_transmission_upper_bound','发现误设透射率上界0.35导致优化后仍无法达到目标；将物理允许上界恢复1并重算，用独立DFT和保存后重放确认两个结果均达到固定残差目标。','同一初值/目标/光学配置，错误范围[0,.35]与正确[0,1]。',
              [('修复边界并重优化',['scipy.optimize._minimize.minimize']),('成像/归档后复验',propagation)],['错误上界MSE>1e-4；正确及归档直接DFT MSE<1e-9。'])],
         [{'from':'continuous mask vector','to':'prysm coherent forward -> SciPy minimize','contract':'N×N row-major透射率在[0,1]；强度目标只读，数值梯度下每次重新传播；OptimizeResult.success不能替代独立残差。'}],
         'runtime/stable/03.12.03.json',extra=['连续灰度强度逆问题，无二值可制造性、ICCAD13 EPE/PVBand验收或shot计数；未冒称调用OpenILT完成任务。'],count_exception='局部逆问题由同一传播核心、状态/采样和单一box优化器闭合，42个左右操作足够；不为50下限引入重复光刻后端、低层优化器或未验证制造约束。')
    torchrows=[
        op('torchoptics','torchoptics.fields','Field','__init__ intensity power centroid std propagate propagate_to_z propagate_to_plane modulate normalize inner copy visualize','state','唯一复光场/波长/网格与可微传播、强度功率、复制；不同时加入prysm状态。'),
        op('torchoptics','torchoptics.planar_grid','PlanarGrid','__init__ shape geometry cell_area length bounds meshgrid is_same_geometry geometry_str','geometry','字段继承的SI网格及几何一致性；波长、spacing、z都以米明确传入。'),
        op('torchoptics','torchoptics.optics_module','OpticsModule','set_optics_property','configure','运行时更新光学属性时保持注册属性规则。','由已选Field/元件继承；不直接构造基础模块。'),
        op('torchoptics','torchoptics.system','System','__init__ elements forward measure measure_at_z measure_at_plane sorted_elements','propagate','可组合平面元件与观察面，用于拓展固定Field自由传播任务；不纳入未验证偏振分支。'),
        op('torchoptics','torchoptics.elements.modulators','AmplitudeModulator','__init__ modulation_profile','mask','幅度掩膜实体，透射率界限由任务或参数化明确限制。'),
        op('torchoptics','torchoptics.elements.modulators','PhaseModulator','__init__ modulation_profile','mask','相位掩膜互补状态，单位弧度；当前fixture只优化幅度。'),
        op('torchoptics','torchoptics.elements.lens','Lens','__init__ modulation_profile','mask','薄透镜相位和焦距配置，供4f场景参考；当前不声称4f完整教程已执行。'),
        op('torchoptics','torchoptics.elements.elements','ModulationElement','forward','propagate','元件继承的真实调制动作，不伪造到各子类。','由AmplitudeModulator/PhaseModulator/Lens继承，实际可调用对象由所选构造产生。')]
    torchrows += [op('torchoptics','torchoptics.config',n,None,'configure','显式全局默认值读写；任务优先显式参数并记录dtype，重置不得沿用过期状态。') for n in ['get_default_spacing','set_default_spacing','get_default_wavelength','set_default_wavelength','get_default_dtype','set_default_dtype']]
    torchrows += [op('torchoptics','torchoptics.profiles.'+module,n,None,'mask','常见可配置振幅初值，明确几何尺寸/周期和网格单位，不要求Agent手工拼每个像素。') for module,n in [('shapes','circle'),('shapes','rectangle'),('gratings','sinusoidal_grating')]]
    torchrows += optimizer()
    emit('03.12.04','以正式发布TorchOptics和SciPy构建可微近场掩膜优化：保留复场、SI采样和两个defocus/dose条件，通过真实PyTorch计算图提供梯度；诊断detach断图和Jacobian反号，独立差分与DFT验证后完成过程窗口目标。采用局部标量Fresnel近似，替代无发布且梯度不可靠的TorchLitho候选。',
         [('torchoptics','primary','官方v1.0.2 release/PyPI一致，Field传播原生自动微分已验。'),('scipy','complement','接收真实自动微分loss/jac的有界L-BFGS-B，区别03纯数值梯度。'),('TorchLitho-2.0','excluded','无release，固定研究自定义VJP方向差分偏差31.0%；原生AbbeSim图可过差分但不满足发布门槛，正式任务不用。'),('TorchLitho','excluded','无发布旧训练框架，与发布TorchOptics重复，避免额外数据/训练配置。'),('prysm','excluded','本发布版传播为NumPy/SciPy，04选原生autograd链，避免修改数学backend或手工装配另一个梯度实现。')],torchrows,
         [src('https://torchoptics.readthedocs.io/en/stable/user-guide/propagation.html','官方定义ASM/DIM及精确/Fresnel模型，asm_pad=(0,0)明确禁用填充形成周期计算；SI单位、传播距离和数值边界可审计。'),
          src('https://torchoptics.readthedocs.io/en/stable/user-guide/inverse_design.html','所有传播/调制/检测通过torch.autograd可微；参数可固定或Parameter，幅度范围可通过约束/参数化实现；强调损失与目标场。'),
          src('https://torchoptics.readthedocs.io/en/stable/examples/optical_systems/4f_system.html','Field→Lens→AmplitudeModulator→Lens→观察面的完整光学链启发实体和位置；当前仅执行小型近场窗口，不冒称完整4f benchmark。'),
          src('https://github.com/MatthewFilipovich/torchoptics/blob/v1.0.2/examples/optimization/training_petal_beam.py','固定v1.0.2官方逆设计示例实际调用传播、损失、backward和optimizer，证明所选发布版接口范围；本轮固定CPU而不运行其大型训练。'),scipy_source],
         [rec('03.12.04','repair_detached_gradient_graph','在已知局部mask上建立Field并传播，发现中途detach后损失无法对原mask求梯度；恢复完整计算图后核对方向导数与中央差分，并以直接DFT比较强度与传播前后功率。','16×16 CPU float64，spacing200nm、λ193nm、z1µm、ASM_FRESNEL、无padding。',
              [('构造可微输入并传播',['torchoptics.fields.Field.__init__','torchoptics.fields.Field.propagate_to_z','torchoptics.fields.Field.intensity']),('修复计算图并验守恒',['torchoptics.fields.Field.propagate_to_z','torchoptics.fields.Field.power'])],['detach错误必须拒绝；正确方向差分相对误差<1e-8，直接DFT强度误差<1e-12，功率保持。'],script='verify_torchoptics.py'),
          rec('03.12.04','repair_process_window_gradient_sign','用两个z/dose角点构成固定强度损失，诊断反号Jacobian导致无法降低目标；修为真实autograd梯度后有界优化mask，并独立DFT核对两个角点及归档恢复。','角点(1µm,.9)/(5µm,1.1)，初值.3，解析低频目标，透射率[0,1]。',
              [('计算窗口目标和梯度',['torchoptics.fields.Field.__init__','torchoptics.fields.Field.propagate_to_z','torchoptics.fields.Field.intensity']),('修复Jacobian并优化',['scipy.optimize._minimize.minimize'])],['反号MSE>.01；正确两角点独立MSE均<1e-10，保存回读仍通过。'],script='verify_torchoptics.py')],
         [{'from':'NumPy optimizer vector','to':'TorchOptics Field -> autograd -> SciPy loss/jac','contract':'每次回调创建float64 requires_grad tensor，Field内部复场complex128；spacing/λ/z均米，dose乘强度。只在返回标量/梯度给SciPy时detach，中途不得断图；二维row-major顺序一致。'}],
         'runtime/torchoptics/03.12.04.json',extra=['04使用正式TorchOptics1.0.2，不依赖未发布TorchLitho2；参考包仅保留排除和差分失败证据。',
                                                          '这里只验证单色标量Fresnel近场双角点、连续灰度掩膜；未声称NA限制的投影系统、Hopkins多源、二值制造约束或标定resist。'],
         additional_infra=[{'reference':'runtime.torchoptics_tensor_autograd','reason':'TorchOptics官方硬依赖PyTorch的张量/自动微分协议：创建输入、torch.autograd.grad、detach用于SciPy边界，独立负例故意中途detach。所有物理传播均为已选Field接口；不暴露完整PyTorch训练库作参考工具。'}])

if __name__=='__main__':main()
