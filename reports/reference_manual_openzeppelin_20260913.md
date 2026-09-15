# OpenZeppelin / ENS：六条参考链人工验收

日期：2026-09-13。验收对象是参考链及其实际交付，不是 Qwen。按 `task_text` 检查 `reference.answer`、真实执行返回和 final 文件，不因为参考链被选作 reference 或 `execution.success=true` 就视为任务完成。未重跑、未修改代码和原始结果。

路径缩写：`R = runs/openzeppelin_sol_full/20260913_090540_320853_smithery_openzeppelin_25_gpt-5.6-sol`；`T(n) = R/tasks/task<n>`；`F(n) = T(n)/final/filesystem_scopes/contract_projects`。任务文本和参考回答位于 `R/tasks.json` 对应 task_id；执行结果位于 `T(n)/agent_result.json.execution.tool_calls`，完整日志为 `T(n)/tool_calls.jsonl`。

## 结论

| 任务 | 参考链判定 | 原因 |
|---|---|---|
| task1 | FAIL | MD/JSON 未持久化要求的投票权统计和集中度；MD 不含逐票明细，却声称两份产物均记录 67 条投票。 |
| task6 | PASS | 治理数值、异常边界、独立实现和依赖材料符合任务；没有 Qwen 的微型钱包时间聚集虚构。 |
| task8 | PASS | 三项目静态审阅与缺失依赖有真实证据；完整 OZ ZIP 六个成员的字节、大小和 SHA-256 均通过独立核对。 |
| task10 | FAIL | 错称 ERC1155 继承 IERC1155Receiver，把 import 当作继承声明；违反核心声明比较要求。 |
| task11 | FAIL | CSV 缺少分析/对账及完整提案快照，参考回答还声称逐票字段包含 CSV 根本没有的 space_id、策略权重。 |
| task14 | FAIL | 实际归档报告只有 966 字节，未包含要求的投票分析、源码审阅、依赖/工具链限制和完整性说明；长篇 reference.answer 没有写入文件。 |

合计 **2/6 PASS，4/6 FAIL**。六题的执行状态均为 success=true，说明“调用链跑完”不能替代交付内容验收。task1、task11 采用与 Qwen 审核相同的产物口径：任务要求审阅产物呈现分析，聊天回答不能补齐已导出的文件。task14 对报告内容和归档要求尤其明确，不存在此处解释空间。

## 核查方法与共同数值

读取六题各自的 final/records.sqlite，以目标 proposal_id 筛选 votes，重算三选项票数/权重。均为 67 票、67 个大小写归一后的钱包；For/Against/Abstain 为 60/2/5 票，权重使用 `math.fsum` 分别为 1191365.5341689042、156420.0868523988、66836.54296485189。与回答中的普通浮点累加差异约 2.33e-10，处于声明容差内，不作为失败理由。

目标提案为 `ens.eth` 的 `[7.1] [Social] SPP3: Marketplace RFP`，ID `0x943e585d1a4996525c5c7d229401d604ea56fe08c2c9c615c44f048ba42487b7`，closed，snapshot_block=25527225，start=1783988115，end=1784420115。Top5=838311.9426080291、占比59.260484%；HHI≈0.09163891793；非空理由6条，For/Against/Abstain分别2/1/3条。对 JSON/CSV 与 Qwen 同 hash 的发现来自实际字节重算，不是仅复制工具声称的 hash。

## task1：FAIL

已满足：实际执行 30 次调用，读取提案、分析投票、逐票检索、两种容差对账和代表性 vote context；聊天回答覆盖快照、分布、描述统计、集中度和理由边界，数值基本正确。第22次导出因源码路径失败，23、24次成功生成 MD/JSON，故不把已恢复的路径错误单独判作失败。

真实产物：

- `F(1)/openzeppelin_contracts_v5/reviews/ens-marketplace-review.md`：702 bytes，SHA-256 `e525fbd1e65f6221427cc8c29788ef4831793392cf68463905cba67aac638974`。
- 同目录 `ens-marketplace-review.json`：35587 bytes，SHA-256 `4f77bfd0972174709033313f2e75854c159dca50365f27c6704fbf4dc0a8cc49`。

二者分别与 Qwen task1 的 MD/JSON 完全同 hash，存在同样的模板缺陷。MD 只有标题、ID、状态、票数、重算总计、一致性布尔值、三行分选项对账与附录说明；没有钱包参与数、统计分位信息、Top5、HHI、投票权集中度，也没有 67 条逐票记录。JSON 有完整 proposal 和 67 votes，但 statistics 仅含 reason_count/unique_voter_count/vote_count/voting_power_total，没有 min/max/mean/median 或 concentration。

参考回答“**两份产物均记录 67 条投票**”不能成立：MD 只记了 vote count=67，没有逐票记录；“投票权统计、集中度以及 Markdown/JSON 产物已完成”把聊天分析与落盘内容混为一谈。按与 Qwen 一致的验收标准判 FAIL。

## task6：PASS

任务要求治理核验和另行的独立合约实现/依赖材料，没有要求将全部结果写入特定文件。因此本题不能仅因额外导出的 MD 简短就判失败；参考回答本身可以承载完整独立审阅材料。

实际执行26次调用，覆盖 proposal、votes、reconciliation、Governor、ERC20/721/1155、manifest、OZ与AA dependency audit、AA flow、ERC20测试、Circle源代码及代表性理由；第23次导出失败，第26次纠正路径后成功。回答明确绝对1e-6/相对1e-9容差，逐选项和总计差异正确，无孤立与重复钱包，集中度数值正确。

异常识别没有把集中度当作操纵证明。所引用“Fire Eyes has voted No”和“I'm on the committee”确实存在于 `T(6)/final/records.sqlite` 的 reason 字段，并明确只是投票者自述，不能据此证明利益冲突。与 Qwen task6 不同，参考没有“最后2小时约20个微型钱包”这一错误结论。

逐源文件扫描 import 并检查存在性：OZ29条、AA21条、Circle6条均在切片内未解析；参考对OZ/AA数量的描述正确，Circle的Blacklistable引用也真实存在。Solidity版本、Governor扩展点、代币表面、测试未执行、工程工具链限制均有调用返回/源码支持。独立材料反复明确不构成提案执行依据。

额外文件 `F(6)/openzeppelin_contracts_v5/reports/ens-marketplace-review.md` 实际966 bytes，包含两个源码路径和SHA-256引用；“源码附录”实际是引用，不是源码全文。任务未要求源码全文附件或持久化完整报告，故不额外扩大验收标准。

## task8：PASS

实际执行24次调用：先列三个工程，再对三个工程分别审计依赖，核验manifest、Governor、token、AA、两份测试材料、源码对比与读取，最后整个OZ工程打包。静态审阅覆盖SimpleAccount、EntryPoint、PackedUserOperation九个字段以及Circle初始化/黑名单迁移和代币授权表面。

直接读取源码确认：OZ四份合约为^0.8.20；Circle为0.6.12；AA三文件为^0.8.28。import及未解析数分别为OZ29/29、Circle6/6、AA21/21。SimpleAccount的 `_onlyOwner` 与 `_requireForExecute`、Circle的 `initializeV2_2` 和黑名单迁移，与参考叙述相符。清单版本5.0.2、43个开发依赖、21个脚本及静态测试材料的边界均有证据；未把缺失工具链解释成已完成动态验证。

真实归档：`F(8)/openzeppelin_contracts_v5/review/oz-contracts-v5.zip`，21558 bytes，SHA-256 `3c5183bbf2bed62ea917d962ece9c26e23eef9ed5e066da7eae05fded99cebbb`。清单同目录 `oz-contracts-v5.integrity.json`，1309 bytes，SHA-256 `29322776516c8d0831ac4beb64fcc34bc6cc3f85443181b292175d8a6a710cdf`。

独立解包后，六个成员集合与清单完全一致；逐成员大小、SHA-256、原文件字节均一致。文件为Governor、ERC1155、ERC20、ERC721、package.json、ERC20.test.js，大小分别31450、16932、11143、17703、3639、7715 bytes。归档真实、完整，未把依赖缺失的当前切片说成可编译完整上游仓库。

## task10：FAIL

已满足：31次实际调用中的治理检索、分布、Top5/Top3、双容差重算、理由覆盖和无异常结论有数据支持；独立合约静态记录覆盖四种合约、pragma、接口、导入和测试限制，也明确不是安全审计或提案执行证据。第25、26次导出失败后，第28、29次成功；额外ZIP与两成员完整性清单也真实一致。任务本身不要求这些额外文件完整承载分析，不借此判失败。

核心错误是 reference.answer 写道：

> ERC-1155（ERC1155.sol）继承 Context、ERC165、IERC1155、IERC1155Receiver、IERC1155MetadataURI、IERC1155Errors

`F(10)/openzeppelin_contracts_v5/contracts/token/ERC1155/ERC1155.sol` 第19行实际为：

```solidity
abstract contract ERC1155 is Context, ERC165, IERC1155, IERC1155MetadataURI, IERC1155Errors {
```

`IERC1155Receiver` 只在第7行被 import，用于安全接收检查，并没有出现在继承列表。这会把代币合约自身与接收者接口实现混淆，属于任务明确要求的“合约声明及相关声明差异”的实质性错误，故 FAIL。相对地，Governor 第27行确实继承 IERC1155Receiver；不能把 Governor 的继承关系迁移到 ERC1155。

## task11：FAIL

实际21次调用包括逐票检索、参与分析、两次对账与最后CSV导出；回答中的67票和分布/浮点差异基本正确。文件 `F(11)/openzeppelin_contracts_v5/review/ens-marketplace.csv` 真实17506 bytes，SHA-256 `f4b522740cccde8f04c2dd24c23f2c9e01d804b181eccd74eecadf981174f52f`，与Qwen CSV逐字节同hash。

标准库CSV解析得到67条记录，表头只有：

```text
proposal_id,title,vote_id,voter,created,choice,choice_text,voting_power,reason
```

不存在space_id或voting_power_by_strategy，参考回答却称“逐票记录包含…space_id…按策略分解的投票权…”。若其意图是描述工具返回的原始记录，则也没有说明这些字段未进入CSV，交付说明误导了读者。

与Qwen相同，CSV无参与/选项汇总、无公布scores、无重算对账/容差/一致性结果；提案信息也只有ID和title，没有空间、快照区块、作者、窗口和状态。聊天给出统计不等于CSV内已呈现完整审阅。不能一边将此内容缺陷用于Qwen判FAIL，一边对同hash参考产物判PASS。

## task14：FAIL

与Qwen task14准备失败不同，本条参考实际执行了31次调用，有真实报告和ZIP。因此失败归因是**报告内容不完整**，不是没有执行。

任务明确要求“生成一份包含提案、投票分析、计票复核、源码审阅范围、限制条件和完整性校验信息的 Markdown 治理报告”，并将报告和package.json、两份源码归档。

真实MD：`F(14)/openzeppelin_contracts_v5/reports/ens-marketplace-review.md`，仅966 bytes，SHA-256 `8083f424f59f44a8cb56b33cb6051c48d71d5d7cfaf9e60a3dd809b3541f52c3`。全文是task1的702字节模板加上两个源码路径/SHA引用，没有reference.answer的十节报告内容。检查缺失项：

- 提案空间、作者、快照区块、时间窗口；
- 67参与钱包、分选项占比、描述统计、Top1/HHI、策略贡献、理由覆盖；
- 孤立投票和重复钱包检查结果；
- Governor/ERC20的Solidity版本、可见接口和实现范围；
- 缺失依赖数量/边界与编译测试工具链限制；
- 完整归档清单/自身报告之外交付物的校验信息。

MD保留了票数、分选项计票表、重算总计和一致性布尔值；两条源码路径及hash只能证明引用对象，不能代替源码审阅。聊天中的长报告虽然覆盖许多要求，但没有持久化到要求归档的报告文件。

归档本身检查通过：`F(14)/openzeppelin_contracts_v5/archives/ens-marketplace-review.zip`，12706 bytes，SHA-256 `5e908d00512ee1c4049feaab5694bfd40b5b517f930417ecc85bf0ba90f86097`；清单同目录 `ens-marketplace-review.manifest.json`，970 bytes，SHA-256 `09cd0e2be3c4d2e5cddd58aa6ad8bf90948c897448630da0f916f8b55b99b180`。四成员集合正确，逐文件大小/hash与final源文件完全一致，且ZIP内报告也正是上述966字节短文件。完整性校验只能证明忠实打包，不能证明被打包报告符合内容要求。

## 对参考质量的含义

参考链不是天然的黄金答案。本组六题中，同模板导出缺陷同时出现在参考与Qwen的task1/task11；task14进一步暴露“回答写得完整但落盘报告极短”的问题。task10则是独立的源码事实错误。task6和task8可以通过同一验收标准，不需要把每个工具调用都照搬，也不因任务已明确允许的静态/工具链限制机械扣分。
