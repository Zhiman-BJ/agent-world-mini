# 上游环境与工具问题

日期：2026-09-14。本报告只记录环境和工具实现层面的确定问题，供环境、工具及其验证脚本的维护者使用。问题不能靠任务 Prompt 或执行 Agent 规避；按约定，环境或工具导致任务无法真实交付时，任务判定为 FAIL。

## 1. 真实重建被替换成字段检查

工具公开描述明确说 `validate_mson_objects` 会调用对应的 pymatgen 类重建对象。实际实现只检查 `@class`、`@module` 和少量顶层字段，然后把声明的类型直接作为 `reconstructed_type` 返回，没有调用真实构造函数。

实际 CompleteDos 文件把 `efermi` 等字段放在 `total` 内部，而真实 `CompleteDos.from_dict` 要求顶层字段，调用直接报 `KeyError: 'efermi'`。因此“结构 JSON 字段完整”不等于“DOS 对象可由 pymatgen 重建”。

| 任务 | 暴露调用 | 工具给出的成功证据 | 实际问题 |
| --- | --- | --- | --- |
| pymatgen1 | `validate_mson_objects` | `all_valid=true`，CompleteDos 重建成功 | 原始 MP CompleteDos 及新转换对象均不能真实重建 |
| pymatgen10 | `validate_mson_objects` | 3 个对象 `all_valid=true` | AFLOW/MP CompleteDos 均不能真实重建；另有完整外部 VASP 输入缺少实际 POTCAR |
| pymatgen19 | `validate_mson_objects` | 新生成对象 `all_valid=true` | 明确要求交付的新 CompleteDos 仍报同样错误 |

### 修复要求

- 读取 MSON 后，实际调用对应 pymatgen 类的 `from_dict` 或等价构造函数。
- 对 `CompleteDos`、`BandStructure` 等对象分别执行真实构造，不以 `@class`、`@module` 字段声明作为成功证据。
- 返回每个对象的真实构造结果和异常；构造失败时不得返回 `all_valid=true`。
- 为当前失败对象增加最小回归测试，测试必须在真实 pymatgen 运行环境中执行。

## 2. 静态 VASP 输入工具没有交付完整势文件

工具 `generate_static_vasp_inputs` 的职责是根据材料结构准备静态能量或静态介电响应的 VASP 项目输入。实际交付文件为 `INCAR`、`KPOINTS`、`POSCAR` 和 `POTCAR.spec`，没有实际的 `POTCAR`。

这本身可以是合理的环境限制：真实 POTCAR 通常依赖受许可的外部势文件，工具不一定应该内置它。但如果公开契约或任务把该结果称为“完整、可直接运行的 VASP 输入”，就是能力与描述不一致。`POTCAR.spec` 只能说明所需势的种类，不能替代势文件，也不能据此声称项目可直接运行。

### 修复要求

- 明确区分“输入模板/规格”和“完整可运行输入”。
- 若工具继续只生成 `POTCAR.spec`，公开描述和输出字段必须明确说明需要外部 POTCAR，不能暗示已提供完整势文件。
- 若要支持完整输入，必须在合法的运行环境中实际生成或安全引用 POTCAR，并在回归测试中检查其存在和内容边界。
- 输出摘要应明确列出每个文件的类型，不要只返回 `input_files` 数量。

## 3. 真正标准化被替换成原样复制

Pymatgen task22 调用 `standardize_material_structure(conventional)`。公开契约承诺使用 `SpacegroupAnalyzer` 转换，实际实现只把源 JSON 原样写入新路径，`standardization_type` 只进入返回标签，没有改变结构。

在相同 `symprec=0.01`、`angle_tolerance=5` 参数下，真实 pymatgen 对该材料得到 P6_3/mmc 的 4 位点常规胞；工具交付仍是未变换的 8 位点源结构。之后再次调用同一个字段检查工具也无法发现这个问题。

### 修复要求

- 按公开参数实际调用 `SpacegroupAnalyzer`，并将转换后的结构写入交付路径。
- 返回标准化前后的位点数、空间群和结构摘要，便于独立核对。
- 增加一个已知结构的回归测试，至少比较标准化后的空间群和位点数，不能只检查文件存在或 `success=true`。
- 不要用返回字段中的标签冒充结构已被转换。

## 4. 治理审阅导出工具的内容能力不足

工具 `export_governance_review_artifact` 的公开描述承诺生成包含提案快照、投票明细、统计和重算计票的 Markdown、JSON 或 CSV 审阅产物。实际输入只有提案、投票、格式、输出路径、容差和源码引用等参数，没有可注入完整分析正文的参数。

实际实现只计算 `vote_count`、`unique_voter_count`、`voting_power_total` 和 `reason_count`，然后使用固定模板导出：

- JSON 包含提案、投票和少量统计字段；
- CSV 只包含 9 列逐票表，没有汇总、公布分数、容差或对账结论；
- Markdown 只包含标题、ID、状态、票数、重算总额、容差布尔值、选项对账表及源码引用，不包含参与分布、集中度、策略贡献、理由覆盖率或源码依赖分析。

OpenZeppelin task14 的实际 Markdown 只有 966 bytes。工具返回的 `vote_count`、`source_appendix_count` 和哈希只能证明输入数量、引用数量和字节完整性，不能证明报告正文包含了公开描述或任务要求的分析。该问题是导出工具的模板和契约能力不匹配，不是执行 Agent 没有查到数据。

### 修复要求

- 明确每种输出格式必须承载的字段和分析内容，公开描述不能超过实际能力。
- 让导出工具接收已经计算好的统计、复核结论和源码审阅摘要，或在工具内部完成这些计算并写入文件。
- 返回文件内容覆盖摘要，而不只返回文件大小、哈希和记录数量。
- 为 Markdown、JSON、CSV 分别增加固定样例的内容级回归测试，检查关键字段和分析章节确实落盘。
- `integrate_supplied_project_file` 当前只能写入有限的代码/配置类型；若不支持 Markdown 或 CSV，不应把它当作补齐审阅文件正文的通用途径。

## 5. 为什么现有环境准入没有挡住

Step0 当前检查上游 `tool_generation/tool_validation.json` 是否覆盖全部工具且 `status=passed`。该报告中的标准化和转换用例主要只期待 `success` 及状态变化；重建校验用例直接期待 `all_valid=true`。这些用例没有提供真实库独立重建或标准结构对照的证明。

因此根因是：**上游实现与公开契约不一致，而现有工具验证只验证返回结构和自报结果，没有验证科学对象的真实语义。**

Step0 不应被描述为已经完成了真实科学计算验收；在工具修复前，也不能让执行 Agent 通过 shell、内部代码或直接读取状态绕过工具边界来“补验证”。

## 6. 责任边界

- CompleteDos 重建失败、标准化实际复制、完整 VASP 输入缺少实际 POTCAR：上游工具实现或工具契约问题。
- 工具修复后，必须用真实 pymatgen 和已知样例重新验证，再更新 `tool_generation/tool_validation.json`。
- 在修复和回归测试通过前，不能把工具返回的“验证成功”作为下游任务的真实成功证据。
- 如果要在 Step0 增加真实语义准入测试，应另行定义测试环境、依赖和验收标准；不能用现有 `success` 字段继续代替。

## 7. 证据

- [Pymatgen 真实产物复验](new_pymatgen_manual.md)：真实库校验、标准化对照与逐任务责任边界。

运行目录（相对主仓库）：

`runs/pymatgen_sol_full/20260913_204215_909408_pypi_pymatgen_core_2_gpt-5.6-sol`
