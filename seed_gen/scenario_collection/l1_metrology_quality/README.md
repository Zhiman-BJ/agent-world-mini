# 07/08 场景联合搜集与固定样例执行记录

2026-09-18整理：本组输出已合入 [正式产物](../../pypi_outputs/final_results/README.md)，L3统一位于`final_results/l3/`；重复worker输出已移出产物目录，恢复位置见正式产物说明。下文旧输出路径和命令保留为重建记录，重建的worker目录不上传。

范围为07的28个L3和08的11个L3，共39个；不修改02和共享分类。2026-09-17开始按L1并行分工。本分支39个场景均已生成，合计2150参考操作、78条固定任务通过，39/39运行报告均为passed；26个完整候选包共77825参考操作，正式发布源码重解析一致。最后整批`--check`实际输出`Verified 39 scenario-first seeds.`，退出码0；本分支已冻结，待主分支提升。

产物为 `seed_gen/pypi_outputs/scenario_collection/l1_metrology_quality/`，全量候选池在 `seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/l1_metrology_quality/`。profiles与research、runtime均在本目录。25包PyPI发现清单仅代表候选身份发现，不代表版本源码或运行验证完成。

可跟踪的依赖锁已从8个通过检查的独立环境导出到本目录`requirements-{reliability,optical,quality-graph,microscopy,diffraction,abtem,xrd}-python312.txt`和`requirements-py4dstem-python311.txt`；这些文件不放在被忽略的runtime内。仓库根目录以对应venv执行`uv pip install --python <venv>/Scripts/python.exe -r seed_gen/scenario_collection/l1_metrology_quality/<requirements文件>`复现。py4DSTEM和Semi-ATE-STDF锁引用固定发布本地源码相对路径，其commit以来源manifest为准。只增加复现锁与文档，不修改冻结profile、产物和运行报告。

下表保留前两批11个场景的阶段记录；全部39场景计数以产物的 `reports/summary.json` 为准。

| 场景 | 主要包 | class | function | class_func | all_func |
| --- | --- | ---: | ---: | ---: | ---: |
| 08.01.01 Weibull / Lifetime Distribution | reliability | 15 | 7 | 45 | 52 |
| 08.01.02 Censored Life Data | lifelines | 7 | 12 | 38 | 50 |
| 08.01.03 Survival Regression | lifelines | 8 | 12 | 34 | 46 |
| 08.01.04 Accelerated Life Testing | reliability | 19 | 3 | 49 | 52 |
| 08.01.05 Repairable Systems | reliability + SurPyval | 12 | 0 | 42 | 42 |
| 08.01.06 Degradation | SurPyval | 8 | 1 | 48 | 49 |
| 07.06.01 Psi/Delta loading | pyElli | 11 | 12 | 38 | 50 |
| 07.06.02 Optical stack | pyElli | 13 | 2 | 44 | 46 |
| 07.06.03 Dispersion selection | pyElli | 16 | 2 | 44 | 46 |
| 07.06.04 Thickness/n/k fit | pyElli + lmfit | 13 | 3 | 52 | 55 |
| 07.06.05 Fit quality | pyElli + lmfit | 13 | 3 | 52 | 55 |

表内11场景共543项参考操作。低于50的场景保留明确例外：继承方法不重复展开、可修复系统去掉重复非参数MCF/内部似然、退化/等向光学场景不填入不需要的随机过程或各向异性方法。参考操作数包含构造/属性，不等于最终Agent工具服务器动作数。

## 正式发布与运行环境

- reliability v0.9.0，`86c93307c4f7bf7cbdddfa634119b39feead70f5`，PyPI0.9.0。
- lifelines v0.30.3，`a21e4328fa30bc107ae2a3e0276ec57e892e6504`，PyPI0.30.3。
- SurPyval v0.18.0，`dad29b6cbc7762de3bc3ab95e3252f3bfe69d067`；PyPI发现0.19.0，但官方最新非预发布GitHub Release为0.18.0，按指南采用Release。在线latest文档可能领先，接口按固定tag源码。
- 源码在 `seed_pypi_raw/l1_metrology_quality/{reliability,lifelines,surpyval}`；HEAD/tag/remote/工作区/子模块和全量AST重解析均记录在manifest及research/source_checks。
- Python3.12.4独立 `.venv-scenario-quality-reliability`，`uv pip check`验证29包兼容。NumPy2.5.3、SciPy1.18.1、pandas2.3.3为本批运行依赖；没有因此声称其完整参考API已采集。

## 可复现命令

从仓库根目录执行：

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_metrology_quality_discovery
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_metrology_quality/research/reliability_urls.json --output seed_gen/scenario_collection/l1_metrology_quality/research/reliability_web
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_metrology_quality/research/reliability_additional_urls.json --output seed_gen/scenario_collection/l1_metrology_quality/research/reliability_additional_web
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_metrology_quality_sources
C:/Apps/anaconda3/Scripts/uv.exe venv --python 3.12 .venv-scenario-quality-reliability
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-quality-reliability/Scripts/python.exe reliability==0.9.0 surpyval==0.18.0 lifelines==0.30.3
.venv-scenario-quality-reliability/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_reliability.py --scene 08.01.01
.venv-scenario-quality-reliability/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_reliability.py
C:/Apps/anaconda3/Scripts/uv.exe pip check --python .venv-scenario-quality-reliability/Scripts/python.exe
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_metrology_quality_reliability_profiles
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/l1_metrology_quality/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/l1_metrology_quality --merged-name semiconductor_scenario_metrology_quality.json --group-by-l1 --verify-sources
C:/Apps/anaconda3/python.exe -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/l1_metrology_quality/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/l1_metrology_quality --merged-name semiconductor_scenario_metrology_quality.json --group-by-l1 --check
```

先08.01.01小样例成功后扩大到6场景。全量重跑12任务通过；builder生成6种子成功，`--check`显示`Verified 6 scenario-first seeds.`。

## 验证证据与限制

- 寿命：独立二分求Weibull MLE得分根，alpha=51.8580015405、beta=2.8008599766、B10=23.22091534小时；错误时间放大1000倍被拒绝，恢复后同一容差通过。
- 删失：六条制造观测KM在2/4/6小时等于5/6、5/8、5/16；指数均值为曝光28/失效3=9.333333小时。误把删失标作失效得到4.666667并被拒绝。
- AFT：两组匹配分位样本的系数0.69314716≈ln2、中位寿命倍率1.99999996。查询编码反转产生0.5，修复回2；不把AFT时间倍率等同Cox风险比。
- ALT：三温度组各60条制造数据，解析组均值exp(3000/T)。实际a=2999.97255、b=1.00006179、350K均值5278.5774；错用摄氏得16448.2496被拒绝。仅统计外推，不声称失效机制验证。
- 可修复：两系统四次维修、20系统小时，MCF=.5/1/1.5/2、HPP=.2/h；真实reliability→SurPyval表示桥接。遗漏退役截止造成最后维修被误判删失，MCF1被拒绝并修复2。
- 退化：四条线性制造轨迹越阈140/b；新单位在400h越阈、300h时RUL100h。阈值15修复150后参数与JSON往返一致；伪失效明确区别于实际失效，不声称噪声置信界校准。

已记录失败与修复：猜测文档路径`Accelerated life testing.html`、`Repairable systems.html`返回404，改用实际目录中的单应力/MCF教程，失败页面未计入来源。首次MCF断言遇到`TypeError: ufunc 'isnan' not supported`，因为库返回object dtype，修复为显式float数组后保持原atol1e-12。首次builder因46/49计数无例外而拒绝，复核必要接口后添加明确例外，没有凑数。`acceleration_factor`实际为构造即计算的类，初稿误列函数在源符号校验失败后更正。

## 椭偏第二批

pyElli v0.23.1 commit `4a4b4ecd48d1cc648966c60b6b8ec11673800969`、lmfit1.3.4 commit `0566445d224889e882cea9d0e404324c3fe265e3`均官方最新Release，完整Python索引249/237操作。pyElli数据子模块未初始化，当前样例不用RII而使用明确固定常数；没有把未获取的数据库当成已读。

```powershell
C:/Apps/anaconda3/Scripts/uv.exe venv --python 3.12 .venv-scenario-metrology-optical
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-metrology-optical/Scripts/python.exe pyElli==0.23.1 lmfit==1.3.4 packaging==26.3
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_metrology_quality/research/optical_urls.json --output seed_gen/scenario_collection/l1_metrology_quality/research/optical_web
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/l1_metrology_quality/research/optical_additional_urls.json --output seed_gen/scenario_collection/l1_metrology_quality/research/optical_additional_web
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_metrology_quality_additional_sources optical
.venv-scenario-metrology-optical/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_optical.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_metrology_quality_optical_profiles
C:/Apps/anaconda3/Scripts/uv.exe pip check --python .venv-scenario-metrology-optical/Scripts/python.exe
```

随后使用上文builder生成/检查命令，实际`Verified 11 scenario-first seeds.`。光学venv23包兼容。先单膜Fresnel小样例通过再扩大五场景。NeXus固定reader将Angstrom除10到nm，误写nm造成40–70nm，修复400–700nm。复rho使用来源约定`tan(Psi)*exp(-iDelta)`；初稿用正号独立式失败后核对Result.psi/Delta文档与源码纠正，容差未改。正向单膜rho与独立Fresnel递推误差4.126e-16，错误层序误差1.18734。Cauchy中的100*n1/lambda²尺度错误修复；两角度反演d=23.00000054nm、n=1.79999994、k=.08000005。错误厚度下界40nm使残差.023288，修复后残差<1e-8。单波长训练近零的常数模型在保留谱误差.01611被拒绝；Cauchy修复保留谱2.43e-10。训练波长500/700与保留波长400/450/550/650/750/800显式不相交；初稿审阅发现重叠后在提升前修正并重跑。

最小安装首次import报`ModuleNotFoundError: No module named 'packaging'`：pyElli0.23.1的SpectraRay reader引用packaging但最小依赖未声明，显式安装26.3后通过，不修改第三方源码。NXellipsometry旧链接404已保留，不计成功来源。

## 质量证据第三批

08.02.01–05新增事件时间线、匹配批次比较、变点、候选根因和CAPA证据五场景，操作数80/51/55/80/78。使用networkx3.6.1及另一并行分支固定发布的pandas3.0.5、scipy1.18.1、ruptures1.1.10源码；所有HEAD/tag重解析通过。`.venv-scenario-quality-graph`的13包经`uv pip check`兼容，`verify_quality.py`通过10任务。

```powershell
.venv-scenario-quality-graph/Scripts/python.exe -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_quality.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_metrology_quality_graph_profiles
```

随后执行上文builder与`--check`，实得`Verified 16 scenario-first seeds.`。事件UTC顺序load/etch/test与1200秒时长由固定fixture独立断言；修复+08被错写Z和错误回边。分层均值每产品+2而混合总体-12.4，按预定等产品权重恢复+2，Welch由样本方差独立验算。80点L2分段独立枚举SSE最优37，jump20错得40后修复。A8/10与B2/10失败的Fisher OR16、p=.023014137565221155由组合数枚举验算；保留A两条通过反例并排除无上游关系C。CAPA固定五节点含30天效果审核，缺失节点必须从既存fixture证据恢复，cause.confirmed=False不自动升级。IBM RCA与OpenLineage来源实际读正文，用于问题/候选/措施及Job/Run/Dataset身份模型，不将参考指南冒充安装包或已执行的外部QMS操作。

## 显微文件/信号/谱学第四批

HyperSpy2.4.0、RosettaSciIO0.14.0、eXSpy0.3.2和scikit-image0.26.0按官方Release固定源码。`prepare_metrology_quality_microscopy_sources.py`提取完整候选；scikit-image0.26源码已改为src布局，首次布局断言失败后按实际目录修正。网页错误404保留且不作为来源；Rosetta格式文档位于supported_formats而非user_guide/supported_formats。

`.venv-scenario-metrology-microscopy`最初53包经uv检查兼容，`verify_microscopy.py --scene 07.01.01`先通过后跑全部6场景12任务。HSpy写入原始字典必须包含tmp_parameters、attributes、package_info、learning_results、models，最初缺失依次触发KeyError，按固定源码完整字段修复后无损读入HyperSpy。独立像素总和276/能量10+.5k；3×4×10谱像XY=(2,1)查得60..69、和645，反转导航次序被拒绝。去噪MSE从.0064到.000174046，配准(-3,4)恢复精确像素，反向修正失败。EDS计数100/200与CL因子1/2得到20/80%，错序2/1得到50/50后修复。EELS相对厚度ln1.25/ln2，45eV窗口误包含非弹峰产生0，修复5eV。谱像Al2×3组成矩阵10..60%，每像素两元素和100，转置3×2被拒绝。所有容差未因运行失败放宽。

```powershell
.venv-scenario-metrology-microscopy/Scripts/python.exe -u -W ignore -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_microscopy.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_metrology_quality_microscopy_profiles
```

builder和`--check`实际`Verified 22 scenario-first seeds.`。首次builder拒绝把TupleSA.set作为未选择的候选基础设施，已将set/get明确纳入轴动作并重生成。所有继承方法在定义处计一次。仅HSpy和固定任务执行，不声称所有DM/EMD/TIFF格式实测。

后续准备中发现abTEM1.0.10要求zarr>=3.1，而pyxem0.21.0要求zarr<3，uv明确拒绝同环境解析；已建立独立abTEM和diffraction venv，未篡改依赖约束。

## 晶圆图第五批

07.07.01–05五场景共304参考操作、10固定任务通过；整批生成及检查为`Verified 27 scenario-first seeds.`。wafermap0.3.2、Semi-ATE-STDF0.1.33，以及固定发布pandas/scipy/scikit-learn/networkx构成按场景裁剪的组合。AWS官方WM811K示例正文已读取，用其坐标、缺失/好/坏die与Center/Donut/Scratch分类启发状态和任务；没有启动AWS或下载全811457片数据。

- STDF独立二进制fixture含D1通过、D2失败、D3失败、D2重测通过，按最新测次得到3die中2通过；错误keepfirst仅1通过。wafermap HTML保存并检查标签/几何，未进行浏览器截图或PNG验收。首次add_label误传位置参数后按真实`label_text=`签名修复。
- DBSCAN固定两组5点和1噪点，正确分组可独立列出；坐标误放大1000倍造成11噪点，恢复同一eps后正确。numpy.int64索引转Python整数后才可序列化，未改变聚类判断。
- 中心/环/划痕制造图以scipy binned_statistic提径向失败率，保留样本与训练图不重合；1NN预测三类、混淆矩阵单位阵。逆序特征列导致ring/scratch/scratch后按原模式修复。仅验证固定制造可分性，不声称真实工业分类准确率。
- 24共同测量die的失效图余弦：A/B=√3/2，A/C=0，镜像错误恢复；未测die不当作通过。
- 固定chamber→process→wafer→pattern图与A8/10、B2/10环形缺陷率，OR16、Fisher p=.02301413756522由组合数独立验算。错误边污染祖先，按固定日志删除后JSON往返。相关性不自动升级为因果。

```powershell
.venv-scenario-quality-graph/Scripts/python.exe -u -W ignore -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_wafer.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_metrology_quality_wafer_profiles
```

## XRD第六批

07.05.01–04四场景共188操作（49/48/41/50）、8任务通过；整批生成和检查为`Verified 31 scenario-first seeds.`。pyFAI正式v2026.09（包版本2026.9.0）、xrayutilities1.8.0、diffpy.structure3.5.0按发布源码解析，lmfit1.3.4复用明确版本。`.venv-scenario-metrology-xrd`41包经uv检查兼容。pyFAI HTTPS文档证书链失败，未关闭TLS校验；使用其官方HTTP文档，失败和最终URL均保留。

- 64×64的15像素环，以100um像素/.1m距离独立算峰atan(.0015/.1)=.859372°，pyFAI峰.856328°满足±.04°。常量图7关闭solid-angle修正后积分为7。距离.05m错误峰1.709383°拒绝，PONI保存加载恢复。
- HXRD对称扫描omega10/20/30°得到qz1.4169658/2.7908778/4.0799905 A^-1，独立4πsinω/λ验算；错传弧度数值当度数被拒绝。
- XRR固定8keV SiO2/Si、两界面Fresnel目标与原库正向误差<1e-14；230A=23nm厚度先粗扫180..280A再lmfit恢复229.999999999997A。初次210A初值落在202.32356A局部极小，加入实际粗扫选229A后精化，不放宽验收。错误10..100A边界log残差.50739358，修复后2.007e-13。只变厚度，不声称密度/粗糙度联合唯一性。独立Fresnel初稿相位负号错误按物理约定改为exp(+2ikd)。
- 金刚石Si八显式P1原子a5.43A，体积160.103007A³、最近邻a√3/4=2.3512589713A，CIF往返保留八Si/分数坐标；错误.543nm数值直接传Angstrom修复为5.43A，不修改分数坐标遮掩单位错误。

```powershell
.venv-scenario-metrology-xrd/Scripts/python.exe -u -W ignore -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_xrd.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_metrology_quality_xrd_profiles
```

## 电子衍射及分块第七批

最后8场景的16任务已逐场景运行通过，`prepare_metrology_quality_diffraction_profiles.py`生成联合profile。按07.01.03、07.02.02–04、07.03.01–04顺序，参考操作47/52/51/54/46/51/53/62，共416，低于50的两项保留明确完整性例外。整批源码重解析后实际`Generated 39 scenario-first seeds.`。正式发布包括LiberTEM0.16.0、Atomap0.4.2（官方GitLab tag）、abTEM1.0.10、kikuchipy0.13.1、orix0.15.0、pyxem0.21.0、py4DSTEM0.14.8。各源HEAD/tag和全量候选池均已记录。

- LiberTEM memory两分块 arange384 总73536，ROI帧0/5总24512，反ROI49024被拒绝。RAW arange768的3072字节、三分块总294528与单分块memory一致；错误>f4触发真实版本`NotImplementedError: byte swapping for floats not implemented yet`，明确记录后修复<f4满足独立逐帧和。初版统计`stat().st_size()`误把整数当函数，改为属性后通过。未作TB吞吐或分布式压力测试。
- Atomap九个固定亚像素Gaussian中心，经质心和2D高斯精化最大误差3.635e-10像素；separation24找0峰，恢复7后重精化通过。HyperSpy协方差警告保留，未把位置恢复当成不确定度验证。
- abTEM200keV/总投影势20V*A，独立SI公式lambda=.025079340436A、相位.014576802088；出射复值.9998937249+.0145762851i，强度总1023.999939≈1024。误用200eV波长.86712927A被拒绝并修复。仅常量势，不声称真实IAM成键精度。
- kikuchipy背景图固定减法后按uint8范围重缩放，独立atol1量化验收；7×8背景对8×8探测器触发ValueError后恢复。NCC图样置换[2,0,1]分数1；orix0/30/60°取向标签必须按同一行绑定，错误标签重排即使高分也被拒绝。制造图样非物理EBSD模拟，未验证真实角度精度。
- pyxem XY束中心(12,19)需要(+4,-3)移到YX(16,16)；反号移到(22,8)失败，恢复后绑定.05 k_A^-1尺度。
- py4DSTEM Qx2:5/Qy3:6虚拟探测图[[252,828,1404],[1980,2556,3132]]；掩码转置得到错误315/891/...后修复。导航均值与独立数组归约一致，R2nm/Q.05A^-1保留。官方正式Release0.14.8虽落后PyPI0.14.18/文档0.14.14，仍采用固定发布tag；按源码`Python<3.12`建立3.11.16环境并从本地tag安装，未沿用允许3.12的探索PyPI轮子。

分离环境经`uv pip check`：diffraction139包、abTEM64包、py4DSTEM311共66包、microscopy61包、quality-graph29包均兼容。abTEM要求zarr>=3.1，pyxem要求zarr<3，不能直接放入同一环境；按场景分别安装，未放宽第三方依赖。

```powershell
.venv-scenario-metrology-diffraction/Scripts/python.exe -u -W ignore -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_diffraction.py --scene 07.01.03
# 同一venv逐个运行07.02.04、07.03.01、07.03.02、07.03.03
.venv-scenario-metrology-microscopy/Scripts/python.exe -u -W ignore -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_diffraction.py --scene 07.02.02
.venv-scenario-metrology-abtem/Scripts/python.exe -u -W ignore -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_diffraction.py --scene 07.02.03
.venv-scenario-metrology-py4dstem311/Scripts/python.exe -u -W ignore -X utf8 seed_gen/scenario_collection/l1_metrology_quality/verify_diffraction.py --scene 07.03.04
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_metrology_quality_diffraction_profiles
```

尚未验证：全体参考API运行覆盖；真实设备失效/维修、光学/衍射测量；Agent环境状态存储、隔离、reset/step及任务自动生成。当前制造固定样例不是上述未验证范围的替代证明。
