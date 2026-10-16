# 批量任务可解性与验收稳定性

历史记录：本文保留旧流程的诊断和运行证据，文中旧版入口已移除，命令不能直接重跑。
当前流程见 [SINGLE_AGENT_HARDENING.md](SINGLE_AGENT_HARDENING.md)。

## 结论与范围

当前先检查两个共享环节，不继续靠逐题修改参考答案提高通过率：

1. 工具的公开描述、参数含义、实际实现与返回结果是否一致。
2. 验收是否完整覆盖公开题文，首次验收和独立复核是否使用同一规则。

这两项是必要条件，不是可解性的充分证明。还需要从冻结初态执行受限参考程序，
并让未见参考答案的模型通过同一公开工具接口独立解题。
代码回归测试通过不表示任何一批任务已经重新通过独立验收。

## 已核实的证据

### 20 道任务的判定差异

只读对账结果：
`agent_train/evaluation/reference-stability-audit-20261015/report-01.json`。
脚本：`agent_train/evaluation/audit_reference_stability.py`。
这两个路径相对于 `/mnt/beegfs-h100/users/sunshuo`。

- 20 对首次验收与后续复核中，6 对由通过变为不通过。
- 这 6 对的题文、任务标识、参考答案、工具调用参数、工具结果及错误内容均一致。
- 两轮的有效验收提示词不同。首次额外开启了计算权限约束和提交前格式自检，
  并提供候选程序；复核未开启这两项，也未提供候选程序。
- 因此不能把 6/20 当成控制变量条件下的随机误判率，也不能据此归因于工具随机返回。

后续同协议两轮复核仍在运行，目录为
`agent_train/evaluation/frozen-reference-validation-20261015-01/batch`。
截至 2026-10-15 04:41 UTC，完整选择仍为 20 项，其中 9 项完成两轮：6 项两轮通过，
3 项首轮通过、次轮拒绝；另外 4 项未取得已接受清单、7 项尚未完成双轮复核。
这些是旧参考解的判定稳定性观察，不是新 v10 的参考成功率，也不是 GPT/Qwen 准确率。
9 对已完成项的实际有效策略一致；3 对判定不同的项两次重放均记录原工具结果一致。
其余已完成项中有返回投影差异的记录保留原状，不能将全部 9 对声称为逐字节返回一致。

其中车队题 `semiconductor_scenario_08_01_05__task8` 的 R41/R42/R43 揭示条件适用性分歧：
原文要求排除事件不足或边界不合规车辆，并说明三个优先名额能否填满。首轮从实际数据核实
所有车辆合规，认为无需排除，且三车名单已表达名额填满；次轮要求答案明确写出排除集为空
和名额可填满，判定三项未交付。两轮最终答案、冻结清单及有效提示词的 SHA256 分别为：

- 答案：`6c7a549b36c4dc0f484bf645d9ce853988b4bd795819590e94beae0b7b446d4f`。
- 清单文件：`7f9ba5cc9f1a94b12d3e4e66d21f49b73059dd9f2c39d550df798fa0389c9201`。
- 有效提示词：`0ae8f88c0f5970af78f867cb81a7a21495d4eac9f8706b8f6651e77c15dbabaf`。

这证明相同输入上有不同判定，不直接证明哪一轮正确。不能将语义分歧全部算成任务无解，
也不能用首轮通过抹去次轮问题。后续统一协议需要先固定条件适用性、是否要求明确空集，
以及原有提交已唯一表达结果时是否还要求重复声明；不能靠审核者临时补充格式要求。
当前新批次和旧判定均未因此改写。

### 验收要求可能漏项

旧 `validate_verdict` 检查模型提交的条目是否合法，以及这些条目的布尔值是否与总判定一致，
但没有一份外部冻结的完整要求清单可供比对。

已复现的宿主级反例：题目要求交付 A 和 B，答案只有 A，判定也只列 A，仍能通过旧格式校验。
这是宿主覆盖检查的反例，不是声称某次真实模型输出恰好采用了这个反例。

### 工具测试未必验证业务计算

`env_gen/tool_gen/compiler.py` 的 `_run_tests` 允许不提供 `expected_data`。
即使提供，也只检查所列字段，没有强制检查核心计算结果；工具与测试还由同一作者生成。
`semantic_contract.py` 的静态检查不等于专业算法验证。

生产实例：环境 `semiconductor_scenario_08_01_05` 中的 `estimate_nonparametric_mcf`。
原验收文件位于执行容器：
`/data/agent-world/toolgen-20261010/source_tree/toolgen_environments/delivery/environments/semiconductor_scenario_08_01_05/tools/tool_validation.json`。
两个成功测试检查了账本标识、对象数量、估计器名称等信息；连续观测测试还检查了回显的
`confidence=0.95`，但均未断言 `points` 内的 `mcf`、`variance`、`lower`、`upper`。
所以这里的工具测试通过，不能证明置信界的数值或单侧/双侧语义符合任务需求。

### 共享工具的最小执行反例

`agent_train/evaluation/reference-stability-audit-20261015/single-spot-probe-01/report.json`
记录了对现有 `detect_bragg_peaks` 的原样执行。仅在临时目录创建一个 32×32 的单亮点输入，
亮点位于 `(13,19)`，其他值均为零。三组参数均成功返回，但没有保留该真实亮点，
反而返回原图中没有亮点的位置。实验没有使用私有数值解算器，HDF5 库仅用于写入已知夹具。

保留的实际依赖源码显示：无模板分支把 `DP` 直接作为 `cc`，后续仍对 `cc` 执行 `ifft2` 后找峰；
衍射图平滑 `sigma_dp` 仅在有模板分支执行。公开工具将模板固定为 `None`，
因此其承诺的原图峰检测与实际计算路径冲突。这是环境级问题，不是某道题的答案遗漏。
该实验不是完整 MCP 重放，也不证明其他工具正确；不得将其作为整个环境的通过证书。

随后在克隆初态中加入同一夹具，经未修改的公开 MCP 接口再次执行，证据位于
`agent_train/evaluation/reference-stability-audit-20261015/single-spot-mcp-01/report.json`。
工具真实返回 `success=true`，仍返回同样的错误峰表且没有 `(13,19)`。
重放摘要中的结果差异来自返回文件名被资源投影为 `fast_mapping_files/single_spot.h5`，
不是峰坐标差异。原任务、工具代码和原初态均未修改；该夹具不计入最终 20 题。
第二组单变量实验 `single-spot-probe-02/report.json` 还确认：固定 `sigma_cc=0`，
把 `sigma_dp` 从 0 改为 1、2，返回峰表完全一致，符合源码中跳过该参数的路径。

### 全量静态盘点

`agent_train/evaluation/reference-stability-audit-20261015/tool-coverage-01.json`
覆盖同一批 20 道题的 20 个环境版本、640 个工具。其中 30 个工具的成功测试没有具体返回值断言，
610 个工具至少有一个公开返回叶字段未被断言覆盖。这是测试覆盖缺口，不是错误工具数量。
独立预期值来源及测试与当前实现的绑定尚未验证，报告不把任何工具标记为已完成语义认证。

## 本次通用实现

新增显式启用的验收协议 `frozen_requirements_v1`，旧协议与旧结果保持不变。

- `task_gen/acceptance_contract.py` 将编号要求清单绑定到任务标识和题文哈希。
- 每条要求必须引用精确原文区间，区间并集覆盖题文全部非空白字符。
- verifier 必须逐项返回相同编号、原文及要求文本，不能删项、合并、改写或弱化。
- 新协议强制开启计算权限约束与提交前自检，记录有效提示词哈希、模型和推理参数。
- 新协议拒绝复用旧协议的参考验收缓存，也拒绝未显式指定协议就复用新缓存。

启用入口为 `task_gen.program.direct_hardening_validation` 的
`--verification-profile frozen_requirements_v1 --acceptance-contract PATH`。
必须使用新的输出目录，不把旧验收结果改写成新协议通过。

限制：字符覆盖只是防漏的结构检查，不证明语义已经拆成完整、独立的验收项。
例如用一整段原文作为引用，但只解释其中一个条件，仍需要独立语义审核发现。
已新增 `acceptance_preflight.py`，以只含题文的隔离会话生成清单，再由另一隔离会话独立审核
原子性、完整性、忠实性、交付位置及背景约束。批量结果保留全部 20 个身份和失败项，
不提供参考答案或模型评测反馈。当前运行记录位于
`agent_train/evaluation/solvability-preflight-20261015-01/batch/manifest.json`。
是否全部通过以该批次最终记录为准；工具语义认证仍未完成。
计算权限和格式自检已固定，但模型、推理参数与证据类型仍须由批次配置统一冻结；
不能声称所有历史协议差异已经消除。

### 执行与异常分类

批量入口：

```bash
python -m task_gen.program.acceptance_preflight \
  --manifest /path/to/frozen-twenty/final-manifest.json \
  --output-root /path/to/new-preflight \
  --codex-home /path/to/codex-home --concurrency 5
```

`public_tasks.json` 是作者实际接收的完整公开输入；每题 `author`、`review` 分别保存隔离会话的
只读输入、响应和日志。只有经过结构检查及独立审核的清单才保存为 `acceptance_contract.json`。
批次保留全部 20 个身份，只有全部接受才会写 `checklists_ready=true`；这仍不表示参考解已通过。
运行中以 `process.json` 的 PID 和真实进程状态确认存活，不能只根据旧的进度文件认定运行或停止。

格式错误和语义拒绝不混同。例如首批 DSTEM 审核的原始响应明确将 `atomic=false`、
`accepted=false`，指出 R27 同时合并交付结论和禁止造峰，但 `issues` 返回对象而非协议要求的
字符串，故宿主记录为格式错误。原始审核事实保留在 `review/response.json`，没有将其算作通过，
也没有偷偷拆改该清单。后续若改进通用格式提交或有限重试，须另记版本，不能覆盖这批证据。

首轮已经结束，最终为 16 份清单接受、4 份宿主检查错误。四份中三份的独立审核同时提出了
非原子要求，另一份审核 JSON 声称接受，但调查记录没有逐项覆盖所有要求；不能把它们都解释为
无关紧要的格式错误。原批次及其已加载实现保留。新目录
`agent_train/evaluation/solvability-preflight-20261015-02/batch` 绑定首轮输入及证据，
复用 16 份已接受清单，其余统一最多追加一次结构化编制和独立审核。
修订只使用公开题文、旧清单及其审核意见，不提供任何参考解或模型成败，仍保留全部 20 个身份。
该有限修订批次已结束，最终 19 份接受、1 份拒绝，`checklists_ready=false`。
没有第三次重试，也没有用其他题替换拒绝项。

修订中的 fab 清单没有被放行：首轮认为“确定当前位置及以后首个光刻段”同时包含两个可核验结果，
建议拆分；修订将当前位置和以后首个光刻段分开，第二轮则将原文理解为检索范围包含当前位置，
认为拆分遗漏这个关联。原文引用不变，两次解释都保留在各自证据中。
这说明字符覆盖和字段原子性不能自动保留范围关系，也暴露出短语本身的解释风险；
不能仅据此断言参考答案错误、题目无解，或继续第三次修订直到通过。

`solvability_reference_batch.py` 将已接受清单送入两轮相同配置的参考重放及验收。
当前批次位于 `agent_train/evaluation/frozen-reference-validation-20261015-01/batch`，并发 3。
它记录实际有效验收配置并要求两轮一致，使用原样 MCP 反例隔离已知工具问题。
这只是参考判定稳定性检查，不是模型同协议盲解，也不是所有工具的语义认证；
该批次即使参考全通过，仍不会自动写 `release_ready=true`。

## 参考程序与模型执行条件

新增只读盘点 `reference_protocol_audit.py`，实际报告为
`agent_train/evaluation/reference-stability-audit-20261015/reference-protocol-02.json`。
它绑定冻结选择、题目文件、评测协议、全部 20 份真实轨迹及各自 `harness_policy.json` 的哈希，
并核对轨迹内实际题文、任务标识、上游模型、推理强度和上下文/输出配置；
不修改题目、答案、环境或历史判定。前一版 `reference-protocol-01.json` 继续保留。

- 20 份实际策略均只开放公开 MCP 与原生 Read/Grep/Glob，原生 Bash 被禁用。
- 参考程序可在宿主中遍历已返回的完整记录，做排序、分组和简单累计；模型没有同一宿主执行入口。
  这不等于模型不能借助领域汇总工具完成工作，也不证明专业计算工具缺失。
- 7 道题的至少一次参考返回，序列化后的 JSON 字符数超过单次 Read 的 128K 字符上限。
  这是数据体量线索，不是实际提示词 token 数，更不是上下文超限或不可解的直接证据。
  原生结果外置、Grep、分页和领域工具的紧凑汇总都可能避免全文读取。
- 晶圆限值地图题参考程序实际调用 31 次，随后在 Python 中遍历两个约 300 万字符的完整观测结果，
  完成按头/站点分组、尾部计数和晶粒选择。GPT 真实轨迹记录了 74 次 MCP、0 次原生文件调用，
  包含多次汇总及分站点查询，最终触发 65 次总调用预算终止，未交付答案。
  实际调用超出上限属于原轨迹的已记录现象，不能把上限改写成 74。
- 晶圆工具确有 `summarize_observations` 等紧凑接口；尚未证明其能以相同定义和预算覆盖题文全部要求。
  因此当前结论是参考执行不能单独证明同协议可完成，而不是该题已被证明无解。

旧 GPT 结果按数据体量分组如下，仅用于诊断关联，不是新的准确率口径：

| 参考结果体量 | 题数 | 通过 | 内容失败 | 预算耗尽 |
| --- | ---: | ---: | ---: | ---: |
| 至少一次 JSON 返回超过 128K 字符 | 7 | 1 | 2 | 4 |
| 所有参考 JSON 返回均不超过 128K 字符 | 13 | 12 | 1 | 0 |
| 全部冻结题目 | 20 | 13 | 3 | 4 |

四个预算终止案例全部位于大返回值组，但小样本相关性不能证明因果；
参考调用数量、业务工作量、工具可用性和模型路径也可能共同影响结果。
不得只报告 12/13、移除这 7 题或用该分组宣称最终 GPT 85% 目标达到。

实际路径还反驳了“只要大返回值就一定读不完”的解释：Backblaze 的参考结果最大约 35 万字符，
GPT 旧协议判通过，实际为 14 次 MCP 加 15 次文件检索，共 29 次；其中 Grep 13 次。
STDF 则是 52 次 MCP 加 21 次文件检索，共 73 次后预算终止。
另一个晶圆参数题在预算终止前调用了 50 次 `browse_test_results`，没有原生文件调用。
因此需要检查公开汇总/筛选能力能否覆盖业务要求，以及模型路径是否有效，不能统一归因为
Read 上限、上下文溢出或模型不会算术。上述旧协议“通过”尚不替代新的参考语义验收。

此检查的后续门禁应是：给出相同公开接口、计算权限、读取规则和预算下的成功证据，
或者按统一新协议验证受限编排能力；不能仅提高某一模型预算，也不能悄悄新增执行工具。
审计报告保留原 GPT 通过/失败/预算分类，不据此改判准确率。

## 不改接口的交互参考采集

`interactive_reference.py` 新增显式启用路径，直接复用正式求解的 `run_k3_distillation`，
不新增 MCP、Python 或 Bash 接口。求解 Agent 只接收公开题文和原有公开环境；
旧参考答案、宿主程序、原参考调用、验收清单和盲测反馈均不传给它。

输出 `reference_interactions.json` 来自实际轨迹，包含工具参数、模型可见返回、
CLI 开始/完成事件、最终答案及全部原生读取。并行调用按开始事件记录，不伪造串行依赖。
原始 MCP 记录必须与轨迹逐项完整对应；不存在私下分组、累计后直接补进答案的宿主步骤。
检查实际上游请求载荷的模型、high、输出预算与文件哈希，而非只信任配置或模型别名。
题目、工具、binding、bundle 和初态逐文件哈希在采集前后核对，禁止覆盖旧输出。

采集结束后，使用独立审核过的固定清单验收本次答案，不把旧参考程序答案当标准答案。
预算终止、环境终止、上游终止、证据错误和语义拒绝分别保留。
`interactive_reference_validated` 只表示本次交互及清单验收通过；仍不证明真实增难、必要链增长、
重放稳定性或 20 题最终性能，因此此模块始终保留 `release_ready=false`。
此采集模块不切换旧作者或旧发布入口；下述 v10 分支显式采用新路径，
不能据此宣称整个旧生成管线已迁移。

运行方式（认证只从环境变量读取，不写进证据文件）：

```bash
python -m task_gen.program.interactive_reference \
  --variant /path/to/frozen-variant --protocol /path/to/gpt-protocol.json \
  --checklist-root /path/to/accepted-checklist --output-root /path/to/new-reference \
  --codex-home /path/to/codex-home --api-key-env OPENAI_API_KEY
```

首个端到端连通试跑另存为 `agent_train/evaluation/interactive-reference-smoke-20261015-01`。
它只验证新路径接线，不是新一轮 20 题测评，不能报告为 GPT 通过率或用于逐题修复。
此试跑已完成：同一冻结 GaN HEMT 任务，实际 10 次 MCP 加 2 次原生文件调用，
3 次实际上游请求，GPT high；固定清单 62 项全部通过，验收不提供旧参考答案。
`result.json` 为 `interactive_reference_validated`、`sources_unchanged=true`，
`release_ready=false`。这只证明该题上的交互采集及验收接线可用，不代表所有题可解或已经增难。
此路径与共享清单/协议门禁的测试结果为 73 passed，原始 JUnit 保存在
`agent_train/evaluation/reference-stability-audit-20261015/interactive-reference-tests-02.xml`。
单元测试只验证实现与拒绝条件，不能代替这次真实试跑的最终答案验收。

## v10 交互参考生成分支

`interactive_hardening.py` 与独立的 `interactive_hardening_prompts.py` 提供新分支，
旧作者 prompt、原题及旧批次不变。它从相同 20 个冻结来源直接改写，不重走原 Step 1/2。
作者接收公开环境、原参考观察、完整保留原则、真实交互条件及版本级已知工具问题，
只提交任务与现实依据，不能提交参考程序、答案或虚构成功调用。

每题统一最多两轮：作者提案、公开题文清单、独立交互参考及语义验收、业务与依赖链审核、
实际轨迹发布副本、MCP/原生调用重放及再次语义验收。只用本轮生成验收反馈，不提供历史盲测成败。
所有失败身份与尝试保留，不用其他题填补拒绝项。
新最长链相邻调用必须满足前序结果完成早于后序开始，否则宿主直接拒绝虚假的并行依赖。
17/18 边界沿用 v9；数量增长、必要工作和业务真实性仍是独立门禁。

`candidate_reference_validated` 仅表示候选走完上述流程，仍需完整批次证据审计、工具语义
核查与独立 GPT/Qwen 盲测。此分支不自动启动正式测评，`release_ready` 保持 false。
已证实的工具反例按工具版本隔离，不改工具接口或实现，也不把参考调用成功视为修复反例。
相对路径 Read/Glob 的真实重放已由下述试跑验证；原生结果外置文件和绝对路径的
生产者、内容及路径迁移仍需单独绑定，失败时保留，不绕过或伪装为通过。

首个完整来源批次为 `agent_train/evaluation/realistic-interactive-v10-twenty-20261015-01/batch`，
保留原冻结选择的 20 个来源，作者/参考/审核阶段按最多 5 个工作项并发执行。
已完成的实现测试为 159 passed，包括旧版本 prompt 哈希、17/18 边界、交互证据、
并行依赖拒绝、工具反例隔离、有限重试及批次身份保持；JUnit 位于
`agent_train/evaluation/reference-stability-audit-20261015/interactive-hardening-tests-02.xml`。
运行进度以批次 manifest、各项 progress 及真实进程为准，不把 queued 状态当作已经生成或验收。

### 生成失败的交互能力反馈

只读诊断入口 `interactive_generation_feedback.py` 汇总生成阶段的真实公开工具拒绝，
保留全部 20 个来源、统一两轮上限、工具版本及原始记录哈希，不读取正式盲测结果。
同类错误分别记录调用次数和不同参数数量，最多附两组真实参数示例；不会合成答案，
也不将工具报错直接认定为实现错误或题目无解。运行中未完成的尝试和未写完的 JSONL
尾记录不会标为可用于后续生成反馈。

首份实际报告为
`agent_train/evaluation/reference-stability-audit-20261015/v10-generation-diagnostics-01.json`。
热力学题首轮中，`compare_scissor_corrected_dos` 的 11 组不同参数均收到同一非重试错误：
缩小带隙 0.2528 eV 会超过网格分辨率并截去占据态。公开说明列出目标带隙非负等条件，
没有说明此网格限制；错误路径却标为 `fermi_energy_ev`。这证明这些实际输入被拒绝，
不是所有输入失效的证明，也不据此修改工具、原初态或标准答案。

该尝试的 CLI 预算记录为 67 个观察到的调用开始事件，而原始 MCP 日志只有 49 个完成结果；
二者不是相同口径，不能把两者之差算作成功调用或将不同参数请求一概称为完全重复重试。
旧 v10 的下一轮作者收到状态和结果路径，但预算终止时没有收到这些具体公开工具拒绝。
隔离作者不能凭该路径读取外部证据，因此反馈不完整是真实的共享管线缺口。
诊断模块目前只输出证据，不注入正在运行的冻结批次，也没有补开第三轮或手工修题。
后续统一生成版本接入该反馈时，仍须先冻结版本和规则，并保持生成验收与正式盲测隔离。

同批 DSTEM 第二轮在参考求解前被清单审核拒绝：R12 将原文“无须读取”升级为
“不读取”，R56/R60 分别合并了可独立满足的限制。不能把清单质量错误报告为模型解题失败
或题目无解。当前 v10 将清单拒绝直接进入任务作者下一轮，没有在同一题文上调用已有的
有限清单修订能力；这会让任务作者为错误阶段重新改题。后续统一流程应分开清单修订和
任务修改的预算、记录及证据，不借此为这道已结束的候选追加第三轮，也不覆盖原拒绝。

新增可显式调用的 `interactive_checklist.preflight_bounded` 为后续流程提供此阶段边界：
先过滤为公开题文，在新目录中编制和独立审核；只有真实语义拒绝才允许同一题文的一次
清单修订。已接受项不重复，第二次拒绝即终止，基础设施或证据错误不盲目重试。
修订输入只能来自原公开题文、原清单和其独立审核；通过时再调用 `reuse_accepted`
核对实际输入、清单、完整审核与调查记录，不能只提交通过标签。返回已接受的清单目录，
不改题文，不调用任务作者，不接收参考答案和模型结果，也不代表任务本身已可解。
当前运行中的 v10 未接入此辅助步骤，原批次的版本和两轮结果不变。
相关清单、交互、证据审计和独立评测回归为 155 passed、6 skipped，原始记录为
`agent_train/evaluation/reference-stability-audit-20261015/interactive-checklist-tests-02.xml`。
其中新增 11 项测试覆盖有限修订、不改题文、不泄露参考、独立证据绑定和拒绝后停止；
不是一次真实模型参考求解或 20 题成绩。跳过项沿用 MCP-only 参数下不适用的原生分支测试。

晶圆题 `semiconductor_scenario_05_05_01_0355` 首轮也说明“清单通过”不等于候选接纳：
实际 42 次 MCP、8 次 GPT high 请求通过 78 个检查点，独立业务审核随后拒绝。
原参考基线为 40 次，数量增长豁免成立，审核没有拒绝其业务用途或认为它只是凑调用；
审核给出的必要调用为原 27 次、新 34 次，最长业务依赖链为原 5 次、新 4 次。

一项拒绝针对空间统计口径：对 W01 传 `test_number=1001` 的真实空间工具返回
边缘 `failed_count=20`，而该单项只有 6 个失败；工具公开说明本身写明基于末次分档。
最终答案明确没有把该图当作单项失败簇，而是从 48 条实际单项失败记录关联坐标，
按坐标差不超过 1 的规则另行构造了单项相邻分组。审核将其认定为应由领域工具完成的
专业计算；原规则同时允许简单比较和数值推理，因此是否禁止这类少量点的基础逻辑推导
仍有边界歧义，不能直接称为已证实的工具实现错误或题目无解，也不据此临时改判。

另一项拒绝涉及选路时序：配对调用 17 在事件 54 开始，而两个候选依据调用 11/12
分别在事件 56/57 才完成。这些后返回的数据确实不能证明 11/12→17 的因果依赖。
但无法证明依赖边和业务答案错误是不同结论；若公开题文未强制执行顺序，合法预取
不能自动视为错误，只能排除对应的链增长证据。当前原始审核理由与拒绝记录完整保留。

实际证据为该题 `attempt_01/result.json`、`business-review/response.json` 与
`reference/reference_interactions.json`；汇总另存为
`agent_train/evaluation/reference-stability-audit-20261015/v10-generation-diagnostics-03.json`。
原流程已自动进入统一第二轮作者尝试，没有手工修答案、修改接口或追加第三轮。

独立诊断回归为 15 passed，原始记录为
`agent_train/evaluation/reference-stability-audit-20261015/interactive-generation-feedback-tests-01.xml`。
这些测试验证快照、身份、反馈范围和异常分类，不证明 20 题已达到最终验收标准。

```bash
python -m task_gen.program.interactive_hardening \
  --manifest /path/to/frozen-twenty/final-manifest.json \
  --input-root /path/to/frozen-original-inputs --protocol /path/to/gpt-protocol.json \
  --output-root /path/to/new-v10-batch --codex-home /path/to/codex-home \
  --concurrency 5 --api-key-env OPENAI_API_KEY \
  --counterexample /path/to/recorded-mcp-counterexample.json

python -m task_gen.program.interactive_generation_feedback \
  --batch /path/to/new-v10-batch --output /path/to/new-generation-diagnostics.json
```

### 只读证据与重放审计

`interactive_release_gate.py` 不改作者提示词、不改运行中的批次，也不修改工具或题目。
它从原始请求、轨迹、MCP 记录和实际状态重新核对证据，而非只接受通过标记：

- 原公开题目、清单编制输入、独立清单审核及有效验收提示词必须与冻结版本对应。
- 交互参考的实际请求载荷必须使用指定模型和 high；验收不能接受私下提供的旧答案或程序。
- 发布副本通过正式加载器重新读取，核对调用、答案、工具、初态和参考终态。
- MCP 资源参数须经现有 ResourceCatalog 归一化后仍指向同一资源，不允许借路径投影改业务参数。
- 对原始 MCP 返回、原生读取返回、错误和完整调用覆盖重新计算重放摘要。
- 比较采集运行、重建状态、重放运行及重放验收状态的常规文件路径和逐字节内容，包含私有数据库。

文件比较不包括权限、时间戳或空目录的等价性；返回字段明确记为
`final_state_files_identical`，不是通用语义状态认证。
原生绝对路径投影暂不放行，须另行核对真实生产者与内容，不能仅凭投影记录相信路径有效。
证据拒绝不等于题目已被证明无解，也不应记为被测模型答错。

真实试跑的只读审计记录：
`agent_train/evaluation/interactive-reference-smoke-20261015-01/collection-replay-evidence-audit-03.json`。
该记录核实同一 GaN HEMT 参考的 12 次调用参数、返回值一致，144 个状态文件一致，
首次独立验收的 62 项要求全部通过。它仍是接线与重放验证，不是新的增难题或 GPT 盲测成绩。
后续审计使用新文件名保留版本，不能覆盖旧记录。

```bash
python -m task_gen.program.interactive_release_gate \
  --collection /path/to/completed-reference --replay-root /path/to/completed-replay \
  --output /path/to/new-collection-audit.json

python -m task_gen.program.interactive_release_gate \
  --batch /path/to/new-v10-batch --output /path/to/new-twenty-task-audit.json
```

完整批次审计仍保留全部 20 个来源和所有未完成或失败项，不从通过项中另选子集。
批次 manifest 以单次读取的快照及其哈希绑定，避免并发进度更新被误记为本次审计输入。
`interactive_evidence_bound` 只说明一项生成证据绑定通过；工具的业务正确性、真实增难审核和
独立模型成绩不能由哈希或重放一致性替代。参考 Agent 的成功率也不当作正式 GPT 准确率。
当前共享回归为 210 passed、6 skipped；跳过项是仅适用于原生分支的测试在 MCP-only 参数下
不执行，同一用例在原生分支已执行。原始记录为
`agent_train/evaluation/reference-stability-audit-20261015/interactive-hardening-tests-03.xml`。

旧 `distill taskgen --verify-after-each` 入口目前没有接收本批冻结清单的参数，
其默认 `verify_distilled_case` 不使用 `frozen_requirements_v1`。因此不能直接复用旧启动命令
并声称正式模型验收与参考验收同协议；正式盲测前须显式绑定每题已接受清单、同一严格
验收配置，并保留完整证据。旧入口保持不变，尚未启动正式模型评测。

### 冻结清单的独立盲测入口

`interactive_evaluation.py` 提供单独的新入口，不改运行中的生成器和旧评测命令。
准备阶段重新审计全部 20 个身份，只有全部为 `interactive_evidence_bound` 才复制同一组题目，
同时绑定发布题目哈希、初态、实际工具和各自清单。不允许用接受子集替代完整批次。
若仍有未完成或拒绝项，准备阶段报错且不创建评测目录。

正式求解使用原有公开 MCP 与 Read/Grep/Glob、256K 上下文、65 次总调用及 80K/128K
字符读取限制。GPT 保持参考模型与 high；Qwen 使用既有 thinking 协议。求解器只接收公开题目，
不接收参考答案、程序、调用列表、验收清单或评测反馈。实际上游请求按原始载荷及哈希核对。
独立 verifier 使用该题已冻结清单和 `frozen_requirements_v1`，同样不提供私有参考答案，
完整保留请求、策略、实际调用、答案、状态及逐项验收证据。

报告保留 20 项原始身份和状态；仅当全部 20 项完成内容验收时写出
`complete_content_accuracy`。预算、环境和上游终止单独保留为待分类，不偷偷删除或当作
纯内容错误。单元测试中的 17/20=85% 是合成夹具，只检查分母口径，不是任何模型实测成绩。
真实 Qwen 盲测前仍须检查部署权重、实际服务及 16 张 H100 资源；此入口不会自动部署或改题。

```bash
python -m task_gen.program.interactive_evaluation \
  --batch /path/to/completed-v10-batch --output-root /path/to/new-frozen-blind-inputs

python -m task_gen.program.interactive_evaluation \
  --prepared /path/to/new-frozen-blind-inputs --protocol /path/to/frozen-model-protocol.json \
  --output-root /path/to/new-model-evaluation --codex-home /path/to/codex-home \
  --api-key-env MODEL_API_KEY --concurrency 5
```

上述评测调用还须在既有 GPT provider transport 配置下运行；新入口不修改或另建代理。
入口与分母测试已完成 23 passed，记录位于
`agent_train/evaluation/reference-stability-audit-20261015/interactive-evaluation-tests-02.xml`。
包含新入口、证据审计和旧共享行为的完整本次回归为 234 passed、6 skipped，记录位于
`agent_train/evaluation/reference-stability-audit-20261015/interactive-hardening-tests-04.xml`。
本批仍未满足准备门槛，未进行新的正式盲测，也未达到 GPT/Qwen 最终目标。

运行方式：

```bash
python -m task_gen.program.reference_protocol_audit \
  --manifest /path/to/frozen-twenty/final-manifest.json \
  --solver-report /path/to/gpt-report-v2.json --protocol /path/to/gpt-protocol.json \
  --output /path/to/new-protocol-audit.json
```

## 批量门禁

1. **按环境版本检查工具。** 用有独立来源的最小已知输入/输出，检查关键返回值、单位、
   边界、状态变化，以及置信水平等容易歧义的参数。记录数据、实现、依赖和公开描述的版本。
   认证按版本复用；相关内容变化后失效。缺少独立预期值时记录未验证，不能默认通过。
2. **先冻结要求，再看答案。** 仅根据公开题文编制原子要求，独立审核完整性、可观察性、
   交付位置和是否擅自新增条件。固定清单同时用于参考验收与后续模型验收。
3. **受限参考执行与独立复核。** 同一冻结初态、同一公开工具接口、同一计算权限和证据格式。
   全部要求必须有实际证据；参考拒绝、判定冲突、工具问题分别报告，不记为模型失败。
4. **独立盲解。** 不提供参考答案或内部数据，使用已冻结协议测 GPT high 与 Qwen。
   预算、上下文、基础设施失败与内容错误分开统计，不通过筛除失败题美化准确率。

原有真实需求、计算权限、调用链增难、18 次门槛及其他原则继续生效，详见
`HARDENING_PRINCIPLES.md` 和 `HARDENING_PRINCIPLES_AUDIT.md`。
这些门禁不意味着可以无条件保证每道题可解；它们提供可复核的证据并阻止已知类型的误放行。
