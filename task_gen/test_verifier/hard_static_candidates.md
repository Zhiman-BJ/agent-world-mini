# 100条样本中静态验收困难的10个候选

范围：浏览已有100条任务列表，并回读候选任务完整原文；以下是实现难度的定性筛选，不是实测难度排行榜。静态指确定性程序判定，可使用格式解析器、重算和符号库。已有参考终态不等于只有一个合法输出。

## S71：设计下一轮试验，没有唯一正确方案

- 任务ID：`semiconductor_scenario_08_02_w1_0514_program_003`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_08_02_w1_0514/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_08_02_w1_0514/tasks.json)

> 提出新一轮的对照布局、按各组0次值归一的前段与末段比较办法及候选晋级准则

固定温循条件和已有数值能检查；方案是否同时降低反应代价、维持阻氧效果，以及晋级准则是否合理，需要语义和专业判断。参考方案不能穷举合法设计。

<details><summary>完整任务原文</summary>

先进封装纳米孪晶铜再布线（NT-Cu RDL）温度循环可靠性分析环境，供封装材料与失效分析人员比较表面防护方案、追溯电阻漂移机制。

封装材料组准备下一批Sn覆盖的NT-Cu RDL温循样片：当前Sn能压低氧信号，但Cu-Sn反应可能让前段电阻上升。请提出一份针对Sn层反应代价的下一轮试验方案，而不是在现有涂层中重新选优。以现有sn浸镀工艺作为基准，仅将Sn目标层厚向下调整成两档待试配方；不预设未测的新厚度数值，也不要把文献中三类涂层合并报告的厚度区间误认作Sn的逐件实测分布。bare作无防护氧化对照，ag、sicn作已测阻隔表现的参照，不作为本次工艺改动对象。

所有组按既有协议 pmc11595427_tct 比较：−55至125°C，冷热端各驻留5分钟，变温18°C/分钟，每条件3件；电阻在0、250、500、750、1000次记录。请根据原有sn的制备范围与阶段电阻，提出新一轮的对照布局、按各组0次值归一的前段与末段比较办法及候选晋级准则：既要压低原sn在250/500次的电阻上冲、控制250至1000次的Cu-Sn反应层和Cu/IMC附近及其他空洞，也要维持1000次的阻氧效果，不能以Sn在750次的一次回落抵消前段代价，更不能为了低电阻牺牲氧阻隔。说明未来需怎样同时观察Sn/Cu界面和定位氧，并划清图像与电阻仅能在同组、同阶段、同协议下组级对应的边界；新厚度及其验证结果目前都不存在。

作为方案依据，请报告现有sn、bare、ag、sicn五阶段组均值及已报告的相对各自初值的变化、Sn早晚期反应层与1000次定位Cu/Sn/O、裸铜1000次两个位置的氧含量；解释为什么Ag的低氧线扫和SiCN截面只能分别作有限对照，不可冒充Sn的定量元素测点或SiCN的元素图。请注明发表图估读与标准差误差棒并非逐件原始数据，元素比例也不能独立确认晶相。材料图件位于 ntcu_study 范围：初始截面 ntcu_study/materials-17-05458-g003.jpg、电学轨迹 ntcu_study/materials-17-05458-g004.jpg、1000次表面 ntcu_study/materials-17-05458-g005.jpg、裸铜氧化及定位元素 ntcu_study/materials-17-05458-g006.jpg、Sn早晚期截面及元素 ntcu_study/materials-17-05458-g007.jpg、Ag截面与低氧线扫 ntcu_study/materials-17-05458-g008.jpg、SiCN截面 ntcu_study/materials-17-05458-g009.jpg。方案末尾列出实际采用图件的完整相对路径。

</details>

## S58：判断公式适用条件与物理量的区别

- 任务ID：`ingaas_gaas_qd_radiation_review_program_009`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_01_08_01_0071/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_01_08_01_0071/tasks.json)

> 请逐项决定可用、仅在什么条件下可用或不可由现有资料计算

标定算例可复算；符号关系、条件性结论、尚缺数据的最小集合有多种等价表述，不能匹配单一答案。

<details><summary>完整任务原文</summary>

面向半导体量子点堆叠研发的八带 k·p 电子结构与跃迁辐射审查环境，供理论建模、外延设计和光学测量协同筛选垂直出光几何。

光学组正在编写 series_b 的角分辨测量报告，想把已有报道的导带 p→s 辐射结果转换成仪器可测的量。请给出一份可直接交给报告负责人的方法适用性意见，明确当前能报告什么、哪些换算应暂缓、补齐哪些数据后才能继续。只讨论 series_b，不替其他系列选结构，也不做新仿真。

拟采用的方案是：把论文图5中沿 [001] 的归一化曲线读数当作物镜收光效率；把两个非相干发光群体的 DORA 按群体占比直接平均；把归一化方向发射率取倒数作为绝对辐射寿命，再用学位论文输运模型中的电子声子散射时间替代测得的衰减时间；最后借用其中的激光模式 β 因子和光学截面作为自由空间探测器的计数标定常数。请逐项决定可用、仅在什么条件下可用或不可由现有资料计算，给出替换表述与量之间的正确关系，不能只给一个总的通过/不通过。

先确定 series_b 的几何族、材料体系及实际关联的跃迁规则，关联缺失时保留未匹配引用。说明该系列原文的层数范围与点间势垒，确保被讨论的是既有扫描。方法意见须明确初末态、包络项和中心胞项的物理含义、两项形成发射率的方式、偏振积分与传播方向的区别、简并通道求和和重复计数归一化。区分归一化每单位立体角辐射、单方向比值、有限孔径积分、绝对发射率、辐射寿命及含非辐射过程的测量衰减时间。说明图5蓝/红曲线的观察平面和 p_y/p_x 对应，不把单个截面当成已知的全空间角分布。

方向采用 [110]、[1bar10]、[001]，bar 表示负指数。给出规则声明的全部 DORA 平面法向量及这三个晶向的笛卡尔分量与单位向量；计算各法向量与各晶向笛卡尔分量的点积，以确定每个 DORA 的方向对和符号意义。保留三种 DORA 的分子、分母定义。有限孔径的正确收光关系用符号表达即可：以全空间发射为分母、物镜接收立体角为积分域，并保留随方向和偏振变化的仪器透过率及探测效率。当前没有物镜孔径、完整角分布或绝对强度，不要给出收光效率、秒数或计数率的虚构数值。

报告中的合并算法用下面独立的标定算例验证，不把它写成 series_b 的仿真或测量。两个非相干群体共用同一强度单位和同一方向对 H=[110]、V=[001]：群体甲每个发光体的 H=2、V=6，群体乙 H=8、V=4；发光体数占比甲=0.75、乙=0.25。按 D=(H−V)/(H+V)，计算两群体各自 D、占比加权的 H 和 V、合并后 D、直接按占比平均的 D，以及“直接平均减合并后”的偏差。说明一般情况下何种强度权重才能平均 D，何种条件下可按发光体占比直接平均；说明非相干群体的发射率求和与同一跃迁内两个矩阵元的合成为何不同。

来源均位于 scope_id=stacked_qd_reference，论文正文完整相对路径 source/Mittelstaedt_01_08_R1arxiv.tex，学位论文完整相对路径 stacked_qd_thesis.pdf。源码方法定义串为 \label{eq:matrixelements}、\label{eq:rate}、\label{eq:Gamma1}、\label{eq:DORA1-10}；图5的原文定位串是 normalized radiated power；series_b 扫描条件的原文定位串分别为 Here, for closely stacked QDs 和 The role of number of stacked QDs.。使用这些完整定义串或原句定位，证据窗口为命中行及前后各10行。结论注明对应行号和少量关键原文，不需要逐条复述检索过程。

学位论文的四处争议依据分别以 counted several times（重复计数归一化）、normalized radiated power（图例）、electron-phonon-scattering rate（辐射、散射与激光模式耦合）及 Einsteins（光学截面）定位。每处取首次出现的 PDF 页，恢复该整页上下文，每页最多保留20000字符，给出实际 PDF 页码与足以支持判断的短摘录。对照源码与学位论文说明各自归一化和通道计数口径，特别区分 β 所代表的激光模式耦合比例与物镜收光比例，以及光学截面所需的波长、介电参数和绝对自发辐射率与仪器效率的不同。不要因为正文中出现比例式或输运公式，就声称现有 series_b 记录已含绝对寿命或探测器标定数据。

最终交付中文文字意见，覆盖方案逐项处置、适用方法和方向定义、算例全部数值、报告可沿用的表述、继续计算所需的最小数据及出处。原始记录和文件保持原样，无需生成附件。

</details>

## S56：跨文献选择路线并限定证据力度

- 任务ID：`sic_oxygen_pl6_research_program_005`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_01_04_02_0030/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_01_04_02_0030/tasks.json)

> 说明什么同口径结果会改变优先离子的选择

数字和引文位置可核对；下限、近似值、样本范围、跨论文不可比性及反事实选型条件需要理解论证。

<details><summary>完整任务原文</summary>

这是一个面向4H-SiC量子缺陷研究团队的氧相关PL6色心归属与缺陷重构工作空间，连接氧同位素实验、ODMR/PL数据和DFT候选模型。

我们准备为下一批室温单色心样本申请离子束机时，需要决定碳、氮、氧中优先复现哪一种注入路线。目标是提高PL5、PL6及其氧相关改性双空位家族的单中心产出，并获得可辨认的发射体；这次按产出证据选择离子种类，不把相干时间或系综亮度峰值设成筛选门槛。请交付一份中文离子选择备忘录，给出优先离子、另外两种离子的对照用途，以及推荐适用的已报道条件。只做文献支持的回顾性决定，不开展新实验、仿真或另建文件。

材料限于scope_id=oxygen_vacancy_2504中的main.tex、scope_id=oxygen_vacancy_2504中的SupplementaryMaterial.tex、scope_id=oxygen_vacancy_2511中的main.tex、scope_id=oxygen_vacancy_2511中的SI.tex。每个关键数字和判断都要标注区域、完整相对路径及真实行号；原文摘录可按主题附在文末，中文结论仍须直接标注出处；摘录通常只保留命中行。以下英文短语是定位证据的原文锚点，并非要把英文段落代替中文结论。

2504的氧/氮直接对照应成为定量主表。main.tex的“identical conditions”提供可比制备条件，“Statistical analysis over extended areas”提供作者的增强倍数；SupplementaryMaterial.tex的比较表按行首Statistical area、Counting rate of single spot、Number of color centers、ODMR contrast、Generation Efficiency匹配表行，避免读入其他正文或注释。把表中的面积、单点计数率、两类中心计数、对比度及转换效率逐项列出，保留下限、近似值和千分数单位。按表值计算PL5、PL6及两类合计的氧/氮计数比，单点计数率比的区间，以及从面积和剂量推得的各类及合计转换效率；1 cm²=10⁸ μm²，转换效率=中心数/(剂量×面积)×100%。同时列出主文倍数，解释为何表中下限和近似分母不能当作精确统计量，不能擅自把主文改成表值比。SupplementaryMaterial.tex的“All measurements were conducted under zero magnetic field”说明两组如何被识别，请据此说明频率选择、荧光及对比度差异对可检出计数的影响，不把亮度提升等同于数量提升。

2511的单中心证据用于决定氧路线是否值得优先复现，但不能直接和2504做绝对产率比。main.tex中“An $n$-type”、“For single-defect preparation”、“All samples were subjected”提供基片、掺杂、能量、氧剂量及退火条件；单中心退火时间未报告就保留缺失。SI.tex的“Scanning maps of samples implanted”图注给出碳/氮/氧剂量、面积和激发功率；“All samples were annealed”与“Notably, the sample implanted”解释共同退火条件及亮点密度的比较；这两处以及上述三处制备说明、下述两处粗估/总产率说明均按正则择一匹配各自英文锚点。不要把亮点全叫PL6，也不要因为氧剂量更低就声称得到严格等剂量产率倍数。“Second-order correlation measurements”中的三种代表PL6原始g²(0)应分别列出，以原始g²(0)<0.5作单发射体指标，说明它们对单发射体与信噪比的意义及其非总体统计的限制。

用main.tex的“four dominant color centers”明确2511的家族范围和新增标签，与2504仅统计PL5/PL6的对象范围分开。SI.tex的“Six fluorescence scan images were randomly selected”保留前后各1行，交代随机取图、面积、稳定发光中心和零场室温识别；“In total, 149 single defects”给出总样本与有/无可测ODMR的比例；“A rough estimation indicates”和“Considering an implanted ion dose”给出四类单位面积粗估及作者总产率。分别报告149受检总体、四类占比、四类每100 μm²粗估和约23%总产率。用六张图面积及149×已识别比例计算家族总转换率，再用单位面积粗估计算四类各自及合计转换率，说明近似口径为何会产生小差别；尤其不能把家族总产率写成PL6专属产率。main.tex的“yield saturation count rates”请列四类代表单中心的饱和计数率及不确定度，并计算最亮/最弱比；“Continuous-wave optically detected”用于说明四类零场识别特征与对比度差异，点明哪类低对比度、哪类较暗可能造成分类选择效应，不必重建完整温度指纹表。

离子选择的因果解释须有边界：2504 main.tex的“intrinsic oxygen concentration”说明背景氧与主动引入氧的差别，“six-fold hyperfine splitting”给出PL5/PL6同位素核自旋证据，请报告场向、峰型、耦合及不确定度，区分化学形成效应、氧参与和唯一晶格位置的证明力度。2511 SI.tex的“average number of vacancies”请保留前后各4行，列30 keV既有SRIM的碳/氮/氧每离子平均空位数，说明作者用形成能解释家族选择性的观点，与空位生成量解释分开；这属于既有理论而非本轮计算。解释同量级生成量为何不能单独说明目标家族选择性，不能当作退火后残余损伤。SI.tex的“Comparison of low-temperature fluorescence spectra”和“Because all implants were performed at the same fluence”用于解释等剂量三离子的低温光谱比较，分别给出剂量、温度、整体荧光和家族相对谱学占比结论；无数值就明确只能定性。main.tex的“Increasing the oxygen dose”用于说明氧剂量变化及电子辐照参照的结果，准确区分ZPL贡献变化与中心数量变化，不把PL6成为主要ZPL贡献者写成单中心计数占多数。

末尾给出明确的机时选择，以及下一轮碳/氮对照应保持哪些共同条件、为什么仍需等剂量比较和相同单中心产出目标下的比较。列出应分别记录的中心计数/面积、逐类转换率、荧光、分类覆盖与未识别比例，并说明什么同口径结果会改变优先离子的选择。推荐限定在已报道条件内，不给未经测量支持的跨论文绝对产率提升保证。

</details>

## S81：区分不同测量方法支持的主张

- 任务ID：`peled_interfacial_analysis_program_009`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_07_03_04_0439/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_07_03_04_0439/tasks.json)

> 不要把结构张压、化学组分和 Bragg 强度降低混作同一个测量

标定数值可检查；主张与方法、区域、时间、剂量之间的证据关系，以及禁止外推的边界需要语义审查。

<details><summary>完整任务原文</summary>

这是一个面向钙钛矿LED可靠性与失效分析的截面电子显微工作环境。研究者结合偏置老化记录、4D-STEM衍射和STEM-EDX化学证据定位界面或发光层的主导衰退。

图稿组要决定蓝光 PeLED peled_cs_pb_br_cl_blue_001 的界面应变叙述到底可以保留到什么程度：对象是零时点 ETL/发光层原子像标定 gpa_pristine_etl_emitter；对照是同器件无偏置、相同视野连续两次采集的束照记录。请形成一段可以直接交给撰稿人的测量适用性决定：给出零时点 GPA 可使用的晶面与无应变参考区、两方向倒易标定是否自洽、像素视野和剂量/掩膜约束、有效像素筛选与最低可辨应变；把 Pb-rich 亮带附近、ETL/发光层交界和发光层中心各自能支持的应变方向及定量证据界限分开写，不要在没有局部数值时声称测到了超过阈值的具体百分比。结合无偏置复扫的 Bragg 强度损失和晶格变化界限，明确保留哪一种新鲜区域单次采集的零时点解释、哪些二次扫描强度变化不可归为偏置诱发，且不要把这一 4D-STEM 复扫的强度损失冒充同剂量 HAADF-GPA 校准。还要交代为何 3、6、15、25、35 分钟的应变/取向与 Pb-rich 相分离及阴极侧化学演化不能沿用零时点原子像 GPA 的可辨阈值或视作相同像素的纵向应变轨迹；这些论断各应归属哪类原始观测，不要把结构张压、化学组分和 Bragg 强度降低混作同一个测量。

使用 research_materials 区域的 supplementary/supplementary.txt 中补充图 3、4、5、6、7、9、10、11、16 的完整图注，以及补充说明 1—3 的工作流程、空间分区、时序应变和固定视野复扫叙述。为便于和英文图注稿逐段核对，说明中的对应段落依次涉及 “defect-free”、“GPA here reveals”、“Heatmaps of”、“To elucidate”、“This sequence”、“At longer biasing times”、“These electromechanical signatures” 和 “To quantify”。最后明确哪些主张仍需独立的新鲜区域或原始测量数据才能升级为定量偏置界面 GPA 结论。

</details>

## S94：判断客户故障究竟复现到哪一步

- 任务ID：`semiconductor_quality_investigation_program_006`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_08_02_w1_0520/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_08_02_w1_0520/tasks.json)

> 可以对外确认的最窄故障模式

案件、温度、样本和页码可核对；不能把某件某条件的现象泛化为全部返件或物理根因，需要判断条件和量词。

<details><summary>完整任务原文</summary>

半导体供应商处理客户返件的跨部门质量调查工作空间，连接故障复现、物理失效分析、制造与量产测试记录，分别核实缺陷发生原因与未被检出的原因。

客户质量团队要给六起返件投诉安排同一批客户回访，先形成一份可以直接使用的故障确认口径：NEOPHOTONICS 的 case_2002_00903（开关通道不导通）、Hanhwa 停线关联的 case_2007_01291（板级功能失败及引脚短路）、case_2104_00998（客户在−30℃报告 SDO 恒低）、case_2207_00202（电表不充电并报告 pin20 低阻）、case_2404_01399（两件室温失效投诉）、Wistron Medical 的 case_2508_00719（放电后输入归零引发持续振荡）。需要确定每案哪些返件确有与投诉相符的已观测电性异常或条件故障、实验室究竟复现到哪一步，哪些仍不能称为客户应用故障复现；给下一轮客户沟通明确尚需补齐的故障触发条件或单体测试证据。请逐件标识返件，区分客户现场描述、返件分析和生产/台架结果；特别注意 2104 客户−30℃与实验室−40℃不相同，2207 的第三件不能沿用前两件的 pin20 曲线结论，2404 的冷喷和 EEPROM 对照不等于在客户实际板上排除了故障，2508 并联 10pF 的频响迹象不是放电后故障复现。可以用 quality_reports 区域的 reports/qem_ccr_2002_00903_fa.pdf、quality_reports 区域的 reports/qem_ccr_2007_01291_fa.pdf、quality_reports 区域的 reports/qem_ccr_2104_00998_8d.pdf、quality_reports 区域的 reports/qem_ccr_2207_00202_unit2.pdf、quality_reports 区域的 reports/qem_ccr_2404_01399_bench.pdf、quality_reports 区域的 reports/qem_ccr_2508_00719_final.pdf 对照原件。交付一份按案件组织的文字结论，说明每件的实际观察和测试阶段/温度、可以对外确认的最窄故障模式、未复现或缺少证据的界线及后续需要客户提供的条件；注明每份原件的总页数和支撑结论的页码。不开展或声称已开展新的实物测试，不把电性确认直接等同于物理成因确认。

</details>

## S91：生成有证据支持的公开性能说明

- 任务ID：`semiconductor_scenario_02_10_01_0181_program_002`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_02_10_01_0181/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_02_10_01_0181/tasks.json)

> 单项通过并不意味着三项已在同一器件上同时测到

阈值和样本关联可确定性检查；JSON证据矩阵无指定schema，Markdown发布措辞有多种正确表达，需识别联合声明和跨样本外推。

<details><summary>完整任务原文</summary>

硅光及氮化硅平台微转印薄膜铌酸锂调制器的试验线设计与验证工作空间，供光子器件、工艺与测试团队共同评估变异下的器件表现。

客户准备为下一轮微转印调制器样品发布电光性能说明，首先要决定哪些说法已有实测支撑、哪些必须先补测。请分别处理2026年首片硅光晶圆 target_2026_wafer_1、2025年7 mm样机 target_2025_ol_200mm 和2020年SiN样机 target_2020_sin_platform：半波电压须≤4.0 V，3 dB电光带宽须≥70 GHz，消光比须≥20 dB；凡报告为“>70 GHz”或“70 GHz+”且无有限上界的结果只按其已报告下界及开区间说法处理。另将有记录的 VπL 单独按≤3.0 V·cm 判定，不能用它冒充未测的 Vπ。单项通过并不意味着三项已在同一器件上同时测到；只有记录明确指向同一受测样品才能作联合性能声明。

2026年使用 meas_2026_vpi、meas_2026_eoe_bandwidth、meas_2026_rdl_500nm、meas_2026_rdl_1um、meas_2026_low_il，比较500 nm与1 μm RDL的响应，确定后续优先补测哪个厚度组以及差多少GHz才能达到带宽底线；低插损记录的600只调制器不能替代高速受测数。2025年使用 meas_2025_vpi、meas_2025_vpil、meas_2025_eoe_bandwidth、meas_2025_extinction_ratio、meas_2025_yield、meas_2025_eo_section_loss：区分7 mm器件的电压、部分器件的高频响应、已制MZM的消光比、混合结构打印良率及损耗表征结构的有源区损耗，不能从其中的打印规模推算高速合格率，也不能把结构损耗称为同一高速器件的总插损。2020年使用 meas_2020_vpil_measured、meas_2020_vpil_simulated、meas_2020_extinction_ratio、meas_2020_total_loss：只以实测 VπL 判断，单独标出仿真对照和单个样机的插损口径，不拿它与2025结构级或2026参考归一化插损直接排名。

请从现有目标按受体平台、样片用途分别统计，说明2026年四片硅光晶圆中为何仅首片的数据不能形成其余三片的高速声明。交付逐条样本范围和单项资格、缺项与联测限制的证据矩阵到 mtp_materials 区域内 reports/eo_claim_evidence_2020_2026.json；将每代可公开的有限表述、500 nm与1 μm RDL的下一步测试优先级及不可宣称的跨器件/跨晶圆结论写到 mtp_materials 区域内 reports/eo_claim_release_2020_2026.md。答复请点明各代关键数值、500 nm组所需最小带宽提升、拟补测项和两个产物路径。

</details>

## S32：修订科学表述并交付有来源的预览

- 任务ID：`ingaas_gaas_qd_radiation_review_program_005`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_01_08_01_0071/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_01_08_01_0071/tasks.json)

> 给出可直接替换进综述的勘误文字

页码、图注、尺寸、路径可检查；勘误语义及证据不足时的保留意见不等于文本相似度。任务明确允许部分图面待人工核实，不能额外要求完整图像理解。

<details><summary>完整任务原文</summary>

面向半导体量子点堆叠研发的八带 k·p 电子结构与跃迁辐射审查环境，供理论建模、外延设计和光学测量协同筛选垂直出光几何。

综述稿中有一段拟配图说明：“图3是不含应变的基态成分；图6不同子图使用统一绝对尺度，颜色越深就代表该基态成分权重越高，可跨几何直接比较。图6的空间图也可直接作为顶向发射率图。”请为这段说明给出可直接替换进综述的勘误文字，并决定图3、图6哪些引用已经有来源支持、哪些图面细节还需人工阅图。重点是避免把基态成分、包络空间形状和辐射量混为一谈。

材料均在 scope_id=stacked_qd_reference。正文完整相对路径 source/Mittelstaedt_01_08_R1arxiv.tex；图3标签 fig:comp_with_strain，对应原件 source/Fig3a.pdf、source/Fig3b.pdf、source/Fig3c.pdf；图6标签 fig:OrbitalsA，对应原件 source/Fig6.pdf；辅助发表材料为 stacked_qd_thesis.pdf。建立两图的源码图号、标签与实际引用文件映射，逐项报告存在性，图件数以逐项清单为依据；如果摘要计数或图注摘要与清单、正文不一致，应指出异常，不能将其当作缺图证据。保留两图完整原文LaTeX图注，附来源行号。源码证据定位串为 “Diagrams are calculated including effects” 和完整标签定义串 \label{fig:OrbitalsA}，证据窗口取命中行及前后各10行。

请据图注与正文说明图3各类子图的量、应变条件，以及图6的投影面、积分方向、归一化基准和基函数分组含义；注明这些定义的出处。对上述三句拟稿分别给出保留或修改意见，说明若要定量比较基态成分权重或顶向发射率还需什么数据，不要由归一化颜色或文件相似性推导新的物理结果。

学位论文用 “Diagrams are calculated including” 定位图3对应图注，用 “normalized to the maximum” 定位图6对应图注；后一处可能涉及别的模型，只有包含 “three selected geometries” 且对应 A、B、C 系列的页面才能作为图6证据。恢复对应整页上下文，给出PDF物理页码、页面印刷页码、学位论文图号与正文图号的对应关系，比较两处图注的科学定义，并列出图6原件与对应论文页文字中的几何标签是否一致。不要求证明两个版本逐像素一致。

四份独立图件均查看第1页，记录PDF总页数及该页实际可提取文字。能打开、渲染且有页面文字支持者，可按已定位图注复用；若没有文字，保留为待人工阅图，不把空文字当缺图，也不声称曲线或内部子图排列已经核实。请把四份第1页以144 dpi生成PNG，完整相对交付路径为 reviews/normalization_Fig3a.png、reviews/normalization_Fig3b.png、reviews/normalization_Fig3c.png、reviews/normalization_Fig6.png。另将学位论文中上述规则选定的图6对应整页以144 dpi生成 reviews/normalization_thesis_orbitals.png，供编辑并排查看。最终意见包含勘误文字、完整图注、文件和页码映射、关键原文证据、异常及待人工事项，以及每份预览的来源、页码、尺寸和路径。保留原始材料。

</details>

## S21：聚类标签等价、图像预览及未穷尽的并列规则

- 任务ID：`semiconductor_scenario_07_07_02_program_000`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_07_07_02/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_07_07_02/tasks.json)

> 另生成原始晶圆状态和带簇颜色、噪声点及簇中心的两幅 PNG 当场预览（不要求另存文件）

数值统计可复算；簇编号可置换，绘图风格可变化，完整并列可能未给第三优先级（需实际计算确认）。预览不要求落盘，需确认执行证据包是否保留图像。

<details><summary>完整任务原文</summary>

这是一个面向晶圆检测与失效分析团队的晶圆图数据工作空间，用物理坐标分析失败 die 的空间分布并定位集中失效区。团队从检测结果导入带单位的 die 点位，按密度聚类、核验噪声和几何位置，再把可解释的区域结果用于复盘与工艺排查。

良率团队准备针对 WM-811K 批次 lot1 的第 20 片晶圆（晶圆图标识 wm811k:lot1:20）安排局部失效排查，需要先把散布的失败 die 分成可复盘的集中区域，而不是只报整片失败总数。请以完整 0/1/2 晶圆图中的失败单元为输入，先确认矩阵和计数可用于分析；位置一律按从 0 开始的行、列网格解释，不把网格距离冒充物理长度或推断设备根因。

分别查看失败点按欧氏距离、曼哈顿距离计算的最近邻尺度（报告各自的上四分位距离），最终使用欧氏网格距离做密度聚类。请在邻域半径 1.5、2.0、2.5、3.0 个网格单位与包含自身的核心点数门槛 4、6、8 的全部组合中，结合各方案的实际区域规模及噪声来确定一套可交接结果：只考虑非噪声簇数为 2 至 3、噪声不超过 75 个且最大簇至少 60 个失败 die 的方案；优先选择最大簇点数最多的方案，若并列则选噪声更少的方案。噪声继续单独保留，不并入邻近簇。将获选参数及逐失败 die 对应的聚类标签保存在该晶圆图上，并确认最终标签/计数与矩阵一致。

交付一段用于工艺讨论的简要区域说明：写清入选参数和与其他达标方案的关键取舍、失败总数/簇数/噪声数，并逐一列出每个非噪声簇的点数、行列质心、行列最小最大范围及其包围框离矩阵边缘的最短网格距离。另生成原始晶圆状态和带簇颜色、噪声点及簇中心的两幅 PNG 当场预览（不要求另存文件），说明两幅预览的像素尺寸和是否包含全部簇中心。

</details>

## S24：符号表达式与矩阵的数学等价

- 任务ID：`semiconductor_symbolic_analysis_1_program_005`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_symbolic_analysis_1/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_symbolic_analysis_1/tasks.json)

> 不以字符串差异武断宣称表达式代数不等价

可使用符号代数检查而非LLM猜测；但需解析自然语言中的公式、变量约定及矩阵未知量顺序。字符串不同可能等价，数值抽样也不能普遍证明恒等。

<details><summary>完整任务原文</summary>

面向模拟电路设计与验证人员的线性电路分析工作区，用符号网表推导传递函数、阻抗与极零点，并以 SPICE 数值结果核验设计。

可反接的传感器前端夹具将从现有单向夹具迁移。接口工程师需要替代接线的双向符号模型，还要留下原接线的正向信号路径和两端输入负载基线，避免在新夹具测试脚本中误认旧数据。请在项目 lcapy_network_regression 的设计变体 lcapy_twoport、分析案例 twoport_lsection 范围内工作；网表在 symbolic_analysis_projects 区域的 projects/lcapy_network_regression/lcapy/tests/test_twoport.py。其中按出现顺序编号 0 的 Circuit 网表是旧接线，编号 1 的是新夹具替代接线。

对于编号 1，分别以 P1 和 P2 为激励端求节点电压、支路电流及矩阵方程，并交付 P1→P2、P2→P1 两种方向各自的电压传递、跨阻及 P1、P2 各自的驱动端阻抗。为了标明旧夹具的兼容边界，对编号 0 另求 P1→P2 正向电压传递、跨阻和 P1、P2 两端驱动阻抗；旧接线不需要反向增益。保持元件符号和拉普拉斯变量，不作数值替换；分别标明每组表达式对应的网表编号、源文件相对路径与模型层级。把两个拓扑的阻抗基线并列供接口工程师判断负载，不以字符串差异武断宣称表达式代数不等价。

</details>

## S54：区分软件调用、设备通信和实验结论

- 任务ID：`semiconductor_scenario_04_04_02_program_006`
- 来源：[/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_04_04_02/tasks.json](/data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_04_04_02/tasks.json)

> 不能将软件 setter 被调用当成发送了设备命令

类型、初值、统计和固定实验预期可做确定性检查；实际客户端/设备拒绝阶段及会话条件依赖记录覆盖和工具语义。此项难度与日志内容有关，不能仅据任务原文断言不可静态验证。

<details><summary>完整任务原文</summary>

面向实验仪器驱动开发者的无硬件开发与回归测试工作区，以QCoDeS驱动、PyVISA通信层和PyVISA-sim设备定义共同验证仪器控制行为。

GS200 的启动配置迁移器正在接入新版参数接口。旧版只保存有文本读写命令的字段，类型转换层还会把布尔值写成整数，并允许零速率或负步长。请交付一份维护者能直接用于修订迁移器的字段契约，明确应该保存哪些字段、如何保留类型和校验输入。

范围仅限 `yokogawa_gs200` 在 `GPIB::1::INSTR` 上的主对象参数 `source_mode`、`output`、`auto_range`、`ramp_mode`、`ramp_blocking`、`ramp_rate`、`ramp_step`。材料位于 `qcodes_project` 区域：驱动源码 `src/qcodes/instrument_drivers/yokogawa/Yokogawa_GS200.py`，配套定义 `src/qcodes/instrument/sims/Yokogawa_GS200.yaml`。以独立新建模拟会话的初始配置为基线，不改项目文件，不设置源电平、量程或切换模式。

契约表需列出七项参数的目录读写命令、按目录文本命令推断的读写能力与运行时实际能力、目录与运行时单位、目录验证器及范围或枚举约束、运行时验证器类型。结合配套定义，说明哪些字段有设备查询和设置协议，哪些属于客户端缓存或手动配置；空文本命令能否作为排除保存字段的依据？逐项列出能力和单位差异，单位结论只适用于这次实际初始模式。

分别取得七项参数的实际初值及原始类型，作为迁移器的基线样例。对 `auto_range`、`ramp_blocking`、`ramp_rate`、`ramp_step` 四项，将各自刚读取的值保持原类型提交给同一参数的设置接口，记录是否接受及所处阶段，并给出布尔、数值字段的实际验证约束。再分别提交四个问题样本：`auto_range` 为整数 0，`ramp_blocking` 为整数 1，`ramp_rate` 为数值 0，`ramp_step` 为数值 -0.001，报告接受或拒绝、阶段和具体原因。每次读取和候选设置均使用独立新会话，候选设置响应等待统一为 200 毫秒；不能将设置未报错或没有回复当作设置后回读，也不能将软件 setter 被调用当成发送了设备命令。其余三个枚举字段只需给出目录约束、运行时验证器类型和实际初值，不做候选设置实验。

最终答复交付契约表、带类型的七项基线、四项原值提交结果及四个问题样本结果，汇总原值接受数、问题样本的客户端拒绝数、设备拒绝数和接受数。分别评价旧版策略与“按运行时可读写能力保存七项并保留原类型”的策略能否用于当前基线，给出字段选择、布尔值保存、枚举保持、数值校验、软件字段标记和模式单位记录的修订规则。区分实测的四项输入兼容性与另外三项的声明契约，结论限于当前模拟基线，不能声称验证了跨会话持久化、实体输出、斜坡时序或安全性。

</details>
