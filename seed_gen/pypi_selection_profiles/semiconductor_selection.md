# 四个半导体 Python 包的精选记录

本轮目标：保留相互可组合的核心能力，每个包 `all_func` 在 50–100。数据仍为来源参考 API，
尚未封装为最终环境工具，未安装包或执行仿真。

## 文件布局

- 全量索引：`seed_gen/pypi_outputs/ori_all/{name}_{version}.json`
- 精选索引：`seed_gen/pypi_outputs/{name}_{version}.json`
- 显式清单：`seed_gen/pypi_selection_profiles/{name}_{version}.json`
- 逐项报告：`seed_gen/pypi_outputs/selection_reports/{name}_{version}.json`

四份全量文件由根目录移动到 `ori_all/`，移动前后 SHA-256 一致，内容未变。
全量生成脚本默认输出也已改为 `ori_all/`。原先的 pymatgen-core、atomate2 文件没有参与本轮筛选。

## 数量与能力范围

| 包 | 全量 all_func | 精选 class | function | class_func | all_func | 能力族 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DEVSIM | 198 | 0 | 75 | 0 | 75 | 7 |
| nextnanopy | 400 | 15 | 5 | 67 | 72 | 6 |
| Sesame | 140 | 4 | 16 | 34 | 50 | 6 |
| Solcore | 547 | 17 | 27 | 56 | 83 | 7 |

`all_func = function + class_func`，类本身不额外计入函数总数。沿用全量解析口径，
`__init__` 和公开 property 计入 `class_func`，继承/导入别名不重复计数。

- **DEVSIM**：设备生命周期、网格、材料参数、物理模型、方程、求解保存及简单电路。
  采用原生 Python 函数式入口，故精选类数为 0。排除 UMFPACK Python 绑定类、调试打印、
  自定义矩阵装配和高阶元素模型，保留一维/二维和 Gmsh 导入能力。
- **nextnanopy**：输入文件、配置、扫描编排、输出发现与读取、数据容器、CV 后处理。
  只保留 ExecutionPool 一个并发实现；排除产品专属重复 DataFile 别名、内部 Parser、
  InputAssistant 的大量文本构造方法、GDS/绘图和优化器循环。InputFile 是工厂，
  其公共操作记录在 InputFileTemplate；DataFile 的继承操作分别记录在 Output/DataFileTemplate。
  `execute`、`execute_sweep`、ExecutionPool 的执行需要 nextnano 程序及许可。
- **Sesame**：Builder/Scaling、Solver、Analyzer 构成主链，辅以局部 observable、存取、
  几何定位及基本二维绘图。排除 Qt GUI、MUMPS 绑定、异常类、雅可比/残差/导数函数。
  少量几何函数支持缺陷定位和网格校验。`check_equal_sim_settings` 只比较实现覆盖的数组，
  不能作为完整环境状态验证器。图形函数需在无界面后端中另行验证。
- **Solcore**：单位与材料、层结结构、光源、介电振子、薄膜光学、太阳能电池 IV/QE、量子阱。
  使用高层 OptiStack/RAT 和 solar_cell_solver，避免暴露大量底层矩阵函数；排除下载、
  材料数据库写入、SPICE、SMARTS、S4/RCWA 等外部程序入口。
  统一求解器的参数可间接选择多种后端，后续封装须限制为已验收的 task/kind。
  solcore 包级 `material`、`si`、`get_parameter` 等别名在定义类中保留，后续封装可使用公共别名。

这些范围和数量来自逐项选择，不是全局分数排序后截断；清单中的证据分数不能当成运行可靠性评分。

## 空说明策略

一些核心构造器和方法没有独立 docstring，nextnanopy 尤为明显。依据用户前述“没有的填空”，
本轮允许显式例外：每个相关符号必须填写 `missing_description_reason`。
所选 JSON 原字段原样保留；报告记录空类/函数说明、空方法说明及例外原因。未声明例外仍会失败。
缺少符号、方法、源码哈希不匹配或 `all_func` 超出清单范围也会失败。

| 包 | 精选中空说明总数 | 严格通用 Schema 状态 |
| --- | ---: | --- |
| DEVSIM | 0 | 通过 |
| nextnanopy | 59 | 仅空说明违反 minLength: 1 |
| Sesame | 14 | 仅空说明违反 minLength: 1 |
| Solcore | 15 | 仅空说明违反 minLength: 1 |

未更改通用 Schema。这些例外是来源信息缺失，不是已完成的文档补全或运行验证。

## 重现和验证

从仓库根目录执行：

```powershell
# 全量索引：检查发布版源码与 ori_all 内容一致
python -W ignore::SyntaxWarning -m seed_gen.scripts.extract_release_python_seeds --check

# 按四份清单生成精选索引与逐项报告
python -m seed_gen.scripts.select_release_python_seeds

# 只重算比较，不改写文件
python -m seed_gen.scripts.select_release_python_seeds --check

python -m unittest tests.test_select_python_ref_tools tests.test_extract_python_ref_tools
```

本次验证结果：

- 11 项相关单元测试通过，覆盖 docstring 解析、来源哈希、缺失方法、显式空说明例外和预算计数。
- 全量索引与版本固定的源码重新提取一致，精选与清单重新计算一致。
- 每个精选函数/方法均为全量记录的原样子集；类只裁剪 `function` 数组，其余字段未修改。
- 全部计数、50–100 范围、重复项、顶层选择/排除总数及方法选择/排除总数一致。
- 清单内引用的本地证据文件均存在；严格 Schema 检查仅有上述空说明错误。

逐项报告中的 `selected_methods`、`excluded_methods`、`missing_method_descriptions` 支持复核。
验证等级带有 planned/required 含义：表示预定验证方式或所需运行条件，未实际运行数值仿真。

## 后续任务验证方向（设计示例，未执行）

| 包 | 可组合流程 | 后续可验证条件 |
| --- | --- | --- |
| DEVSIM | 网格与接触构建 → 参数与模型 → 方程 → 求解 → 电流/电荷 → 保存恢复 | 网格节点、方程集合、收敛状态、有限值、边界条件和电流守恒 |
| nextnanopy | InputFile → 修改变量 → 保存 → Sweep 生成输入 → DataFolder 定位固定输出 → DataFile 读数 | 文件内容、变量组合数、目录结果、坐标/变量数组及 CV 结果；真实执行另验 |
| Sesame | Builder → 掺杂/接触/光生 → Solver → Analyzer → 保存加载 | 网格和掺杂、残差与边界条件、载流子/复合/电流以及保存恢复；不能只看返回成功 |
| Solcore | 材料/单位 → 层结/光源 → OptiStack → RAT/吸收 → 电池 IV/QE | 单位换算、层宽、光谱积分、R+A+T、数值容差以及限制模型下的参考曲线 |
