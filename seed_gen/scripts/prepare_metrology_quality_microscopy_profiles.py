"""Reviewed file/signal/image/EDS/EELS joint selections; no automatic ranking."""
from pathlib import Path
from seed_gen.scripts.scenario_collection_support import Collection,symbol,recipe
BASE=Path('seed_gen/scenario_collection/l1_metrology_quality');RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_metrology_quality')

def cl(p,m,n,methods,cap,why,construction=None):return symbol(p,m,n,methods,cap,why,construction)
def fn(p,m,names,cap,why):return [symbol(p,m,n,None,cap,why) for n in names.split()]
def common():
    return [cl('HyperSpy','hyperspy.signal','BaseSignal','data metadata original_metadata copy deepcopy crop rebin sum mean std min max integrate1D transpose swap_axes change_dtype set_signal_type as_signal1D as_signal2D save','signal','统一信号状态、统计/物理积分、裁剪、轴变换与保存；修改数据后派生结果失效。','由Signal1D/Signal2D或领域子类继承，不另设第二套基础构造。'),
      cl('HyperSpy','hyperspy.axes','AxesManager','signal_axes navigation_axes signal_shape navigation_shape signal_dimension navigation_dimension as_dictionary convert_units copy','axes','显式区分导航和信号轴，保留维度、单位与可审阅轴字典。','由signal.axes_manager取得，避免脱离数据单独构造。'),
      cl('HyperSpy','hyperspy.axes','UniformDataAxis','get_axis_dictionary value2index calibrate scale_as_quantity offset_as_quantity crop','axes','均匀轴的标定、物理值到像素和带单位尺度；属性赋值另列基础设施。','由AxesManager管理的axis对象取得。'),
      cl('HyperSpy','hyperspy.axes','BaseDataAxis','index_in_array index2value value_range_to_indices copy','axes','声明轴到ndarray的映射与继承转换。','UniformDataAxis继承，不独立实例化。'),
      cl('HyperSpy','hyperspy.misc._utils','DictionaryTreeBrowser','as_dictionary has_item get_item set_item add_dictionary copy','metadata','元数据键、原始来源和处理配置的显式读取/编辑/导出。','由signal.metadata/original_metadata获得。'),
      cl('HyperSpy','hyperspy.misc._utils','TupleSA','set get','axes','轴元组的批量属性设置/读取，避免导航各轴单位不一致。','由AxesManager.signal_axes/navigation_axes返回，不独立构造。')]+fn('HyperSpy','hyperspy.io','load','io','高层加载委托RosettaSciIO，保留多数据集和领域类型。')

def spectrum():return [cl('HyperSpy','hyperspy._signals.signal1d','Signal1D','__init__ crop_signal remove_background estimate_shift1D shift1D align1D smooth_savitzky_golay gaussian_filter','spectrum','构造一维信号、背景与可追溯谱预处理，避免同时引入另一套谱容器。')]
def eds_tools():return [cl('eXSpy','exspy.signals.eds_tem','EDSTEMSpectrum','__init__ set_microscope_parameters get_calibration_from quantification vacuum_mask get_probe_area create_model','eds','EDS-TEM主容器、显微参数和选定CL定量；因子与谱线顺序必须一致。'),
      cl('eXSpy','exspy.signals.eds','EDSSpectrum','set_elements add_elements set_lines add_lines get_lines_intensity estimate_integration_windows estimate_background_windows get_take_off_angle','eds','继承的一致元素/谱线表示与峰积分，显式背景窗口和出射几何。','EDSTEMSpectrum继承，EDSSpectrum不重复构造。')]
def main():
    c=Collection(base=BASE,raw=RAW)
    H='https://hyperspy.org/hyperspy-doc/current/user_guide/';R='https://hyperspy.org/rosettasciio/';E='https://hyperspy.org/exspy/'
    evidence={R+'supported_formats/hspy.html':'HSpy是HyperSpy默认多维HDF5格式，支持原始/规范元数据及校准；不同格式并不保证无损，不能只验像素。',
      R+'supported_formats/tiff.html':'TIFF插件支持科学图像及FEI/Zeiss等标定，标签支持有限；multipage策略影响每页元数据。',
      R+'supported_formats/emd.html':'实际NCEM/Velox格式区别：多数据集选择、谱流默认帧/探测器求和，prunedVelox不支持；读取选项改变物理语义。',
      H+'io.html':'实际多文件/多数据集加载及signal_type配置示例；域类型来自扩展包，I/O由RosettaSciIO提供。',
      H+'axes.html':'以EELS/EDS谱像示例区分X/Y导航和能量信号轴；Signal1D(10,20,30)展示(20,10|30)说明顺序约定。',
      H+'signal/signal_basics.html':'通过axes初始化指定name、units、scale、offset，信号状态包含数据和物理坐标而非仅ndarray。',
      H+'signal/indexing.html':'isig/inav分别访问信号/导航；HyperSpy[x,y]不同于NumPy[y,x]，浮点和整数切片分别物理坐标/像素。',
      H+'signal2d.html':'图像漂移estimate_shift2D/align2D与crop示例，明确对齐原地修改需保存副本；峰和标定可作为后续能力。',
      'https://scikit-image.org/docs/stable/auto_examples/registration/plot_register_translation.html':'图像相位互相关实际例子将制造offset(-22.4,13.32)还原为反向修正位移，说明xy/yx及符号验收。',
      'https://scikit-image.org/docs/stable/auto_examples/filters/plot_denoise.html':'实际含噪图像比较TV、双边和小波，噪声降低与边缘保真需共同权衡，不等同真实分辨率提升。',
      E+'user_guide/eds.html':'Ni合金/核壳EDS实例从读取、keV能量标定、Al等谱线积分到CL/zeta定量及空间成分图；校准因子按谱线顺序。',
      E+'user_guide/eels.html':'EELS教程包含零损峰定位、对齐、背景和log-ratio厚度；未知平均自由程时仅相对t/lambda，不能伪造nm。',
      E+'user_guide/metadata_structure.html':'EDS/EELS状态记录TEM/SEM、beam_energy keV、live_time s、能量分辨率eV、采集角mrad及元素/谱线元数据。',
    }
    def sources(urls):return [c.source(u,evidence[u],['signal_id','axes','sample_metadata','processing_config'],['load','calibrate','slice','analyze','persist'],['固定像素/谱计数和单位的正常、诊断与修复任务']) for u in urls]
    def task(sid,tid,text,initial,steps,checks):return recipe(tid,text,initial,steps,checks,'verify_microscopy.py:'+sid)
    hs=[('HyperSpy','primary','作为唯一多维信号/轴/元数据容器。')]
    ex=[('eXSpy','primary','采用EDS或EELS领域子类，负责领域峰/定量/厚度。'),('HyperSpy','complement','提供继承的数据、轴、元数据和持久化，不再暴露重复领域实现。')]
    designs=[dict(scenario_id='07.01.01',description='将显微镜文件读入统一的信号字典和HyperSpy状态，管理数据集、轴顺序、标定、规范/原始元数据及保存损失。以HSpy无损往返作为本地核心，TIFF/EMD/DM/EDAX格式作为受限参考能力，每一格式需后续独立样例覆盖。',packages=[('RosettaSciIO','primary','负责格式解码/写出，HSpy为主保存格式。'),('HyperSpy','complement','消费Rosetta字典形成信号/轴状态与后续分析。')],
      symbols=common()+[cl('HyperSpy','hyperspy._signals.signal1d','Signal1D','__init__','construct','由带校准的文件字典创建谱像信号。')]+sum([fn('RosettaSciIO','rsciio.'+m+'._api',names,'file_format','官方reader/writer别名对应源码定义；格式选择明确记录元数据支持。') for m,names in [('hspy','file_reader file_writer'),('tiff','file_reader file_writer'),('emd','file_reader file_writer'),('digitalmicrograph','file_reader'),('edax','file_reader')]],[]),
      sources=sources([R+'supported_formats/hspy.html',R+'supported_formats/tiff.html',R+'supported_formats/emd.html',H+'io.html']),
      tasks=[task('07.01.01','roundtrip_microscopy_file','将2×3×4制造谱像写HSpy并读取，桥接HyperSpy；核验像素总和276、energy10+.5*k eV与sample/source元数据。','独立制造arange24及Y/X/energy轴字典、样品W1。',[('写读文件',['rsciio.hspy._api.file_writer','rsciio.hspy._api.file_reader']),('桥接领域状态',['hyperspy._signals.signal1d.Signal1D.__init__','hyperspy.axes.AxesManager.signal_axes'])],['精确像素相等','能量10/10.5/11/11.5','原始来源保留']),
        task('07.01.01','repair_energy_scale_metadata','诊断eV轴scale误写500导致1000倍步长，从保存的原始轴恢复.5，再以同一坐标oracle验收。','错误副本与原始读入字典同时存在。',[('复制并检查轴',['hyperspy.signal.BaseSignal.deepcopy','hyperspy.axes.AxesManager.signal_axes']),('按来源恢复并核验',['hyperspy.axes.UniformDataAxis.get_axis_dictionary'])],['错误轴拒绝','像素不变且物理坐标恢复'])]),
      dict(scenario_id='07.01.02',description='围绕image/spectrum/spectrum-image建立多维信号状态，区分导航与信号维，定义物理轴、元数据、切片、聚合及保存；X/Y访问与ndarray相反是必须可诊断的环境约束。',packages=hs,symbols=common()+spectrum()+[cl('HyperSpy','hyperspy._signals.signal2d','Signal2D','__init__ crop_signal','image','图像容器是同一信号模型的二维特例。')],sources=sources([H+'axes.html',H+'signal/signal_basics.html',H+'signal/indexing.html',H+'io.html']),
      tasks=[task('07.01.02','calibrate_and_slice_signal','为3×4×10谱像指定X/Y nm和energy100+.5*k eV，切出101≤E<103并查询XY=(2,1)，验收4通道裁剪与像素和645。','arange120,ndarray顺序Y/X/E。',[('构造并标定',['hyperspy._signals.signal1d.Signal1D.__init__','hyperspy.axes.AxesManager.navigation_axes','hyperspy.axes.AxesManager.signal_axes']),('按命名能量轴汇总',['hyperspy.signal.BaseSignal.sum'])],['裁剪indices2..5','XY=(2,1)等于ndarray[1,2]']),
        task('07.01.02','repair_navigation_axis_order','识别把XY写为YX导致错误像素；按轴契约修复，HSpy保存恢复后同一查询仍为60..69。','错误查询inav[1,2]。',[('核验轴与保存',['hyperspy.axes.AxesManager.navigation_axes','hyperspy.signal.BaseSignal.save']),('重新加载查询',['hyperspy.io.load'])],['错误像素不同','正确像素和645且元数据保留'])]),
      dict(scenario_id='07.02.01',description='为SEM/TEM图像建立可追溯预处理链：保留原图和校准，选择一种TV去噪与相位互相关位移估计，再将结果交回HyperSpy裁剪/保存。以固定干净图像和已知位移验收误差与方向，不把图像增强当作物理分辨率增加。',packages=[('HyperSpy','primary','保存图像、轴标定、副本和裁剪。'),('scikit-image','complement','提供TV去噪和相位互相关主算法，避免同时暴露同任务多个滤波/配准实现。')],
      symbols=common()+[cl('HyperSpy','hyperspy._signals.signal2d','Signal2D','__init__ crop_signal calibrate find_peaks','image','图像状态、已知尺寸标定与后续特征定位。')]+fn('scikit-image','skimage.registration._phase_cross_correlation','phase_cross_correlation','registration','主配准算法，返回需要施加的(y,x)位移。')+fn('scikit-image','skimage.restoration._denoise','denoise_tv_chambolle','denoise','TV唯一主去噪，参数固定并用干净fixture评价MSE。'),sources=sources([H+'signal2d.html','https://scikit-image.org/docs/stable/auto_examples/registration/plot_register_translation.html','https://scikit-image.org/docs/stable/auto_examples/filters/plot_denoise.html']),
      tasks=[task('07.02.01','denoise_and_register_image','对64×64制造图像去除固定棋盘噪声，MSE须小于原值1/4；估计已知(3,-4)移动图的修正位移(-3,4)。','原图、噪声图、移动图均固定，验收目标不能随滤波修改。',[('去噪',['skimage.restoration._denoise.denoise_tv_chambolle']),('估计配准',['skimage.registration._phase_cross_correlation.phase_cross_correlation'])],['MSE降低至少4倍','位移精确[-3,4]']),
        task('07.02.01','repair_alignment_sign','拒绝符号相反的位移，恢复正确(y,x)方向并验证与干净原图逐像素相等；保存.25nm标定后裁8×8区域。','错误修正(+3,-4)使位移加倍。',[('构造校准图像',['hyperspy._signals.signal2d.Signal2D.__init__','hyperspy.axes.AxesManager.signal_axes']),('副本裁剪',['hyperspy.signal.BaseSignal.deepcopy','hyperspy._signals.signal2d.Signal2D.crop_signal'])],['错误图MSE>.01','修复误差0','裁剪8×8'])]),
      dict(scenario_id='07.04.01',description='从经能量和显微参数标定的EDS-TEM谱提取元素谱线计数，并用与谱线同序的CL因子生成重量百分比；状态保留峰/背景窗口、元素标签、因子来源和适用假设。固定Al/Si谱验证积分与因子错序修复。',packages=ex,symbols=common()+eds_tools(),sources=sources([E+'user_guide/eds.html',E+'user_guide/metadata_structure.html',H+'axes.html']),
      tasks=[task('07.04.01','integrate_and_quantify_eds','标定.01keV通道并积分Al/Si两峰100/200，按CL因子1/2计算20/80重量百分比，以I*k归一化独立验算。','1000通道制造delta谱，200keV TEM、1s采集、显式积分窗。',[('构造标定并设线',['exspy.signals.eds_tem.EDSTEMSpectrum.__init__','exspy.signals.eds_tem.EDSTEMSpectrum.set_microscope_parameters','exspy.signals.eds.EDSSpectrum.add_elements','exspy.signals.eds.EDSSpectrum.add_lines']),('积分定量',['exspy.signals.eds.EDSSpectrum.get_lines_intensity','exspy.signals.eds_tem.EDSTEMSpectrum.quantification'])],['计数100/200','组成20/80且和100']),
        task('07.04.01','repair_line_factor_order','识别把CL因子错排2/1得到50/50，从谱线和因子来源恢复1/2，按同一20/80oracle复核。','保留Al_Ka/Si_Ka及因子映射。',[('重做一致定量',['exspy.signals.eds_tem.EDSTEMSpectrum.quantification'])],['错序结果50/50拒绝','恢复20/80'])]),
      dict(scenario_id='07.04.02',description='构建EELS能量校准、零损峰、弹性计数和相对厚度分析环境，并保留背景/去卷积与吸收边模型参考能力。t/lambda由log-ratio得到；缺少平均自由程与角度信息时不输出被误解为nm的绝对厚度。',packages=ex,
      symbols=common()+[cl('eXSpy','exspy.signals.eels','EELSSpectrum','__init__ add_elements estimate_zero_loss_peak_centre align_zero_loss_peak get_zero_loss_peak_mask estimate_elastic_scattering_intensity estimate_elastic_scattering_threshold estimate_thickness set_microscope_parameters power_law_extrapolation fourier_log_deconvolution create_model','eels','核心EELS标定/计数/厚度及有限领域扩展，吸收边模型不是默认自动定量。')],sources=sources([E+'user_guide/eels.html',E+'user_guide/metadata_structure.html',H+'axes.html']),
      tasks=[task('07.04.02','estimate_eels_relative_thickness','校准offset=-10eV、scale=.5eV，定位ZLP=0eV；对两谱用5eV阈值计算ln1.25、ln2相对厚度。','每谱100总计数，ZLP80/50，40eV非弹峰20/50。',[('构造和定位',['exspy.signals.eels.EELSSpectrum.__init__','exspy.signals.eels.EELSSpectrum.estimate_zero_loss_peak_centre']),('相对厚度',['exspy.signals.eels.EELSSpectrum.estimate_thickness'])],['ZLP0','log-ratio独立值atol1e-12']),
        task('07.04.02','repair_zero_loss_window','识别45eV弹性窗口误包含40eV非弹峰使厚度为0，恢复5eV窗口，保持原t/lambda容差且不补造平均自由程。','错误45eV阈值，原始谱与固定验收目标保留。',[('修复阈值重算',['exspy.signals.eels.EELSSpectrum.estimate_thickness'])],['错误两值0必须失败','恢复ln1.25/ln2'])]),
      dict(scenario_id='07.04.03',description='将多维EDS谱像按空间坐标与元素线积分转换为组成图，所有像素共享明确的校准/因子契约，结果继续保留导航轴和原始来源。以两行三列制造Al/Si组成图检验空间方向、百分比守恒和HSpy往返。',packages=ex,symbols=common()+eds_tools(),sources=sources([E+'user_guide/eds.html',H+'axes.html',H+'signal/indexing.html',R+'supported_formats/hspy.html']),
      tasks=[task('07.04.03','build_composition_map','对2×3谱像提取Al/Si计数并生成CL组成图；Al重量百分比为[[10,20,30],[40,50,60]]、两元素每像素和100。','固定两条非重叠谱线，CL因子1/1，X/Y标定.5nm。',[('构造与积分',['exspy.signals.eds_tem.EDSTEMSpectrum.__init__','exspy.signals.eds.EDSSpectrum.get_lines_intensity']),('像素定量',['exspy.signals.eds_tem.EDSTEMSpectrum.quantification'])],['Al固定矩阵','每像素守恒100']),
        task('07.04.03','repair_composition_map_orientation','拒绝X/Y转置为3×2的导出结果，按原导航轴保存并重新载入组成图，核验Y1X2为60%。','错误图转置，原始2×3轴和数据仍可追溯。',[('保存恢复图',['hyperspy.signal.BaseSignal.save','hyperspy.io.load']),('检查导航轴',['hyperspy.axes.AxesManager.navigation_axes'])],['错误shape3×2','恢复2×3且指定像素60'])])]
    for d in designs:
        d['capabilities']=list(dict.fromkeys(x['capability'] for x in d['symbols']))
        d['entities']=[{'name':'microscopy_source','identity':'source_id','attributes':['path','format','dataset','sha256','original_metadata'],'lifecycle':'读入→保存原始像素/元数据→规范副本；转换损失和不支持格式必须报告。'},
          {'name':'signal','identity':'signal_id','attributes':['data','shape','axis_names','navigate_flags','scale','offset','units','sample_id','instrument'],'lifecycle':'构造→核对轴/标定→选区/处理→另存；数据/标定改变让派生图和定量失效。'},
          {'name':'processing_configuration','identity':'analysis_id','attributes':['source_revision','window','shift_yx','filter_weight','line_order','calibration_factors','acceptance'],'lifecycle':'在分析前冻结目标和oracle→执行→故障诊断→按来源修复，不能随结果改验收值。'},
          {'name':'measurement_result','identity':'result_id','attributes':['source_refs','units','array_coordinates','metrics','composition_or_thickness','validation'],'lifecycle':'生成→独立物理/坐标/计数检查→带元数据保存恢复；无实验输入不声称真实材料性质。'}]
        d['bridges']=[{'from':'RosettaSciIO dict / HyperSpy signal / eXSpy domain subclass according toselectedpackages','to':'canonical calibrated signal, image or composition map','contract':'数据ndarray顺序与axes.index_in_array一致；导航XY访问顺序独立于ndarrayYX；保持units/scale/offset、原始来源与谱线因子顺序。'}]
        d['runtime_infrastructure']=[{'reference':'python.numpy_json_pathlib','kind':'infrastructure','reason':'制造确定数组、整像素roll位移应用、独立MSE/求和/log-ratio/因子公式oracle、文件路径及JSON；不隐藏第二套谱定量或图像配准。'},
          {'reference':'runtime.signal_axis_indexing','kind':'infrastructure','reason':'isig/inav、ndarray索引及axis.name/scale/offset/units等属性；框架描述器和[]不伪造为顶层函数，后续封装需显式状态读写。'}]
        d['boundaries']=['固定正常/错误/修复均为制造小数据；未验证真实SEM/TEM文件集合、所有候选接口或硬件。','RosettaSciIO/HyperSpy在线文档按固定发布源码核验；领域子类继承只在定义模块计一次，底层GUI和内部测试不选。','未实现Agent状态注册、reset/step、用户权限和跨任务隔离；HSpy文件只在本地固定样例目录写入。','EDS为理想无重叠峰与给定CL因子；EELS只验证相对log-ratio，不证明绝对厚度、吸收修正或真实化学态。']
        d['runtime_report']=(BASE/f'runtime/microscopy/{d["scenario_id"]}.json').as_posix();d['runtime_scope']='固定制造数据的两条任务、错误被拒绝并修复，独立像素/坐标/守恒/解析公式oracle。'
        c.write(d)
if __name__=='__main__':main()
