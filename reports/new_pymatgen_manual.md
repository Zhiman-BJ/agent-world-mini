# pymatgen 独立复核

日期：2026-09-14。运行目录：`runs/pymatgen_sol_full/20260913_204215_909408_pypi_pymatgen_core_2_gpt-5.6-sol`，Qwen使用`qwen_trial_final`。以下路径均相对此运行目录；运行目录相对主仓库。只按任务要求及实际交付验收，环境工具失效也计FAIL。

## 结论

| 任务 | Qwen | 参考交付 | 决定性依据 |
| --- | --- | --- | --- |
| task1 | FAIL | FAIL | 要求完整DOS可由pymatgen重建，实际CompleteDos.from_dict报KeyError: 'efermi' |
| task10 | FAIL | FAIL | 要求完整、可交给外部VASP的输入，实际仅POTCAR.spec，没有POTCAR；DOS对象也不能如参考答案所称重建 |
| task17 | PASS | PASS | 最低凸包距离SiO2重建成功，身份核查和差异已报告，高对称线输入文件可解析且对应前序结构 |
| task19 | FAIL | FAIL | 明确要求转换成可由pymatgen重建的CompleteDos，双方新生成文件均重建失败 |
| task22 | FAIL | FAIL | 要求标准化结构，工具实际原样复制8位点源结构；真实标准化常规胞为4位点 |

Qwen 1/5，参考交付1/5。此前5/5的初评撤回。主要失败来自环境实现；不能因模型正确调用工具、工具自报成功就判任务完成。

## 实际做了哪些核查

读取五条完整任务文本、参考答案、Qwen最终答案，并检查最终状态中的结构、DOS及新建VASP文件。使用实际安装的pymatgen 2026.5.4、pymatgen-core 2026.8.30、spglib 2.7.0，独立调用Structure.from_dict、CompleteDos.from_dict、SpacegroupAnalyzer、Poscar.from_file、Kpoints.from_file和Incar.from_file。没有修改任务状态或代执行者补做交付，没有运行VASP。

环境工具源码来自`artifacts/upstream_20260911/agentworld-toolgen-data0910-20260911/environments/semiconductor/pypi_pymatgen_core_2/tools.json`。其validate_mson_objects只检查JSON顶层键及@class/@module，随后把声明类型填入reconstructed_type；没有调用pymatgen重建。standardize_material_structure也没有进行对称分析，仅加载原JSON并写到新路径。

## task1：完整DOS实际上不能重建

两侧的`filesystem_scopes/pymatgen_objects/electronic/mp_si_complete_dos.json`均以`total`嵌套保存efermi、energies及densities；实际CompleteDos.from_dict先调用Dos.from_dict，要求这些字段在顶层，直接报`KeyError: 'efermi'`。参考新生成的`electronic/mp_si_rebuilt_complete_dos.json`同样失败。Structure对象可以重建，不能据此推及CompleteDos。

这不是仅缺少一次验证调用：真实目标产物不满足任务明确的可重建要求。参考答案对元数据带隙0.8083与摘要0有保留，但仍断言原始CompleteDos重建成功，不能通过。

Qwen还有实际DOS解读错误：将整个[-2,2]窗口每自旋积分4.6729说成价带积分；将+0.90eV的DOS说成0.734，而采样+0.9015eV约0.3043，0.7344对应+0.9637eV；“单调上升”也不符合窗口曲线。介电主值与各向异性数值基本正确，不抵消DOS核心验收失败。

## task10：完整外部计算输入未交付

参考新目录`tasks/task10/final/filesystem_scopes/vasp_projects/aflow_si_dielectric_recalc/static`，Qwen对应`qwen_trial_final/task10/state/filesystem_scopes/vasp_projects/aflow_si_dielectric/static`。两者均只有INCAR、KPOINTS、POSCAR、POTCAR.spec，状态中没有POTCAR。

本题明确要求“一套完整、可交由外部VASP求解”的文件；POTCAR.spec仅含Si规格，不能代替实际势文件。参考诚实说明还需外部配置POTCAR，因此比Qwen声称完整更准确，但仍未完成这项交付。这里没有把所有输入准备任务都强加实际运行要求：task17只要求准备并校验输入，不要求完整可提交包，另行判定。

参考还声称能带及DOS对象均可重建，但原始CompleteDos实际失败。Qwen把1原子FCC硅与常见金刚石硅的性质直接比较后推断金属性必然是数据质量问题，证据也不足；两种晶体已判不同，不能只由元素同名否定其计算结果。

## task17：核心交付成立

双方选择standard集合中凸包距离最低的SiO2条目4085622，报告0.0591776406 eV/atom，未把它声明为全库凸包上的零距离稳定相。其输出分别是`structures/standard_sio2_lowest_hull_4085622.json`和`structures/SiO2_zt1_lowest_hull.json`；实际Structure.from_dict均成功，组成O24Si12，36位点，体积526.4621035立方埃。

双方报告AFLOW的材料、relax2、bands及电子结果归属，也指出能量记录-4.84518728与原生-4.84516873的约1.855e-5 eV差异。审计布尔值因各自容差不同而不同，不能只因Qwen显示true就判错；最终已披露实际差异。

新目录分别为`aflow_si_verified_nonscf_line/nonscf`和`si_nonscf_line_from_relax2/nonscf`。实际pymatgen成功解析INCAR/KPOINTS/POSCAR，KPOINTS为Line-mode的Γ到X段，结构为1位点FCC原胞，晶格长度2.74194938埃，坐标及矩阵与前序relax2终态在归档精度内一致。用户未要求完整布里渊区路径，也未要求实际运行，不能因为只有一段或没有新计算结果就判失败。

Qwen把矩阵分量1.938851误称晶格长度，并把一条Γ—X线称“两段”；属于文字误差，实际输出结构正确。参考对电子对象可重建的附加断言不成立，但本题要求重建的是所选SiO2，电子结果部分要求身份和对应关系；不将task1的更强要求移植到本题。判PASS不意味着每句描述均无误。

## task19：新转换CompleteDos同样损坏

参考文件`tasks/task19/final/filesystem_scopes/pymatgen_objects/electronic/mp_hex_si_static_verified_complete_dos.json`与Qwen文件`qwen_trial_final/task19/state/filesystem_scopes/pymatgen_objects/electronic/mp_si_static_converted_complete_dos.json`均直接报`KeyError: 'efermi'`。两侧新结构可重建，两套输入确实存在，张量主值也对应原生OUTCAR；但任务明确指定可重建CompleteDos，故双方FAIL。这是环境生成工具和假校验共同导致的实际交付失败。

## task22：所谓标准化实际是复制

参考交付`structures/mp_si_static_trace_conventional.json`，Qwen交付`structures/mp_si_hex_conventional.json`，均与源8位点结构内容相同。真实SpacegroupAnalyzer在symprec=0.01、angle_tolerance=5下识别P6_3/mmc、194，但get_conventional_standard_structure得到4位点常规胞，矩阵约为[[1.925716,-3.335437,0],[1.925716,3.335437,0],[0,0,6.365686]]；源结构则是对角[3.852381,6.668133,6.365686]的8位点表示。

只复制源结构不等于标准化。源码中standardization_type只用于返回标签，不参与计算，与实际产物吻合。因此两侧均未完成标准化结构这一明确要求。张量及初末结构审查基本正确；附带DOS损坏不是本题决定性扣分项。

## 责任边界

task1、19的决定性错误在DOS存储格式及校验工具；task22在标准化工具；task10存在实际势文件未交付的环境能力限制。它们并不都是Qwen规划失败，参考执行同样受影响。按用户约定，环境或工具问题导致任务没完成仍记FAIL；本次只复核记录，不修管线或环境。
