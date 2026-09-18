# 网络恢复后的三环境试跑

## 最终结果（20:13 完成）

真实 Step3 网络搜索已恢复，三环境首轮及故障补跑全部完成 Step3→5。按同一候选最近一次验收结果去重，最终通过 **13 条**：

| 环境 | 初始候选 | 最新通过 | 通过候选 |
| --- | ---: | ---: | --- |
| atomate2 | 15 | 10 | task3、4、5、6、7、8、10、11、13、15 |
| pymatgen | 22 | 2 | task9、10 |
| doped | 26 | 1 | task22 |

这是自动匹配验收结果，不是逐条人工保证，也不是独立 agent/verifier 评测结果。本次复用原 Step2 的建图与 objective；恢复后的执行、任务生成、匹配检查重新运行。doped 旧 task26 虽曾自动通过，但受解析内存不足影响，重跑后在 Step4 超上下文而未通过，不计入最新交付。全部最新任务正文、历史失败原因、阶段耗时、已返回 token usage 和运行目录见 [逐任务结果](2026-09-17-three-search-results.md)。

搜索的可用性有真实证据：独立 Step3 验收拿到 Snapshot、ENS、OpenZeppelin 的相关资料；doped 最新通过 task22 实际搜索两轮，参考 doped 化学势/缺陷修正、ShakeNBreak 与 Materials Project 文档，随后用本地工具确认 CdTe 缺数据并切换到 MgO。参考质量足以支持业务背景，但主要是工作流文档，不等于三五个独立真实用户任务；没有做搜索开关对照，不能声称搜索已经提高通过率或任务自然性。

本轮生产代码仅修复两个运行时根因：数值库线程预算导致 2 GiB 沙箱导入失败，以及 Matplotlib 缓存污染声明状态。新增回归测试后，执行测试 20 项、状态运行时测试 6 项通过。另为试跑安装上游声明的隔离科学依赖；doped 补跑把单工具内存设为 4 GiB，经同参数 2/4/2 GiB 对照确认大 XML 的 IndexError 是内存假阴性。并发由首个环境的 16 降到后续各 4，故障补跑用 1；这些试跑预算未修改仓库默认配置，未改 prompt 或放宽验收。

仍需后续处理的问题：

1. **Step4 输入体积**：doped task19、21，及补跑 task7、26，执行完成后因输入超上下文失败；后两条 prompt 为 2,810,740 / 2,782,516 字符。服务把上下文错误包装为 HTTP 502，不能当普通网络失败原样重试。需要另行设计长工具结果的引用或组织方式，本轮没有静默删证据。
2. **环境契约/实现不一致**：pymatgen 列目录结果不符合输出 schema；doped 绘图允许的某个参数值被依赖拒绝，畸变工具另有越界输出路径；atomate2 TaskDocument 的依赖调用缺必要参数。详见下方实证记录，未修改上游工具或放松状态保护。
3. **数据不足与交付漏项**：doped 同一材料常无法同时满足原始 objective 的配套数据要求；部分已执行候选在 Step5 因漏核原件、漏电荷态异常等被拒绝。通过的 pymatgen task9 仍含未明确索引/规则的“指定 k 点”，属于自动检查漏检。
4. **计数与稳定性**：当前有效调用统计混入部分参数/实现错误，不能仅凭记录数认定达到有效链长；提供方曾限流、超载、退出或超时，单并发补跑恢复了运行，但不代表服务端问题永久消失。所有旧产物保留，重试未覆盖原记录。

三环境首轮耗时分别为 atomate2 2932.969 秒、pymatgen 1873.719 秒、doped 5203.994 秒；均只含 Step3→5，且环境并行，不能直接相加当墙钟耗时。doped 最后一轮六候选补跑耗时 3899.468 秒，执行成功 3 条，最终通过 1 条、上下文拒绝 2 条，其余 3 条由 agent 判定未完成。原始错误与恢复事件保存在各运行的 stability.jsonl / recovery.jsonl、llm_calls.jsonl 和逐任务日志。

## 背景与历史过程记录

以下按试跑过程保留当时状态；其中“尚在运行”等表述为历史记录，最终状态以上表为准，后续明确更正的归因以更正结论为准。

这三个环境都是材料计算场景，但工作不同：atomate2 负责整理、核对已有 VASP 计算及缺陷形成能/构型坐标结果；doped 用已有缺陷、化学势和电子态密度数据分析形成能、载流子与可掺杂性；pymatgen 负责晶体结构与计算文件解析、应变工况和电子结构结果比较。它们不能凭空补齐未提供的计算原件，也没有在本轮执行新的 VASP 第一性原理计算。

流程中的 Step3 一边查阅网络业务参考，一边通过工具选择对象、执行并判断是否完成；Step4 将实际执行转成用户任务和参考答案；Step5 检查任务与实际结果是否匹配。只有 Step5 通过才导出任务。报告中的“执行成功”与“最终通过”分开统计，模型的自我判断不等于人工保证。

## 验收范围

真实 Step3 成功搜索并获得可用业务参考；在合并 main 紧凑 JSON 序列化后的分支上，为此前未跑的 atomate2_5、doped_6、pymatgen_core_4 生成实际任务。流程退出但零任务不算验收通过。旧失败产物保留，不覆盖。

## 故障与恢复记录

1. 旧搜索请求 `/alpha/search` 返回 502。用户修复管线专用服务后，17 日 16:54 的原 `_ReviewClient` 探针以 `Snapshot governance voting` 得到非空结果。较窄的限定站点查询仍可能返回空结果。未修改对话 `.codex` 或认证。
2. 17:02 启动真实 Step3 单候选重跑，复用 v2 的 Step2、初态和配置，输出 `runs/review_web_reference_recovered/20260917_170242_148502_smithery_openzeppelin_25_gpt-5.6-sol`。搜索最多三次，执行质量要求不变。
3. 复核旧三环境失败：atomate2、doped 依赖缺失；数值工具出现 OpenBLAS 地址空间分配失败。仅导入 numpy 的最小沙箱调用稳定复现失败，2 GiB 限制不变而设置数值线程数为 1 后成功。已将 OPENBLAS_NUM_THREADS=1、OMP_NUM_THREADS=1 放入沙箱环境，不开放网络，不增加内存限额。新增真实 numpy 沙箱回归测试，修改前确认失败。
4. 为试跑建立 uv 隔离依赖环境，按上游声明安装 atomate2==0.1.5、doped==3.2.1、shakenbreak==3.4.4，并保留解析出的依赖版本。安装与工具调用预检仍在进行，未启动三个环境全量重跑。

## 代码基线

本分支已含本地 main 的 `4c46944`（紧凑序列化），合并提交 `e0a6253`。远端认证环节此前按用户要求跳过，不声称本次已拉取远端。

## 真实 Step3 搜索验收

- 三环境首轮 63 条候选中，61 条记录了真实搜索事件，共 101 次调用；未搜索的是 atomate2 task8 和 doped task7，均因运行中断未正常执行，后续补跑均已搜索。检查中未发现单候选超过 3 次搜索。这个计数表示调用发生，不等于每次都返回匹配资料。
- 上述单候选运行耗时 396.317 秒，execution.success=true，27 次业务调用，完成治理计票复核与独立源码静态比较，导出 Markdown/JSON/CSV。
- Codex 事件记录了两次真实 web_search，第二次使用了更宽泛的查询：Snapshot 投票权/结果、ENS 治理提案/代表、OpenZeppelin ERC20 测试/审计文档。
- reason 引用了 Snapshot proposals/voting-strategies、ENS governance、OpenZeppelin ERC20 文档，并说明它们支持治理复核与源码审阅的任务形式。具体提案、67 票、源码缺失依赖及未执行测试均来自本地工具，不用网页替代初态证据。
- 参考内容与任务场景有关，可以支持业务背景和交付形式；它们主要是产品文档，不等同于找到真实用户发出的完整任务。源码附录与治理主目标仍是两个独立部分，这一自然性限制须保留在质量分析中。
- 19 项执行测试通过，包括新增数值库 2 GiB 沙箱回归测试。

## 依赖与全量恢复

- PyPI 默认源安装超过 10 分钟仍只缓慢下载，17:16 主动以 SIGINT 中断安装（退出 130），改用清华镜像；此时未运行依赖该环境的任务，不影响原产物。
- pymatgen 使用已有管线 Python 3.12 运行时，上次失败的 `analyze_structure_symmetry` 真实调用在相同 2 GiB 沙箱里成功，证据 `runs/runtime_preflight_pypi_pymatgen_core_4.json`。
- 17:16 开始 pymatgen 的 22 条候选 Step3→5，目录 `runs/three_search_recovered_20260917/pypi_pymatgen_core_4/20260917_171642_137193_pypi_pymatgen_core_4_gpt-5.6-sol`。各环境的 stability.jsonl 持续记录恢复、异常和最终任务数量；完整 prompt/回答及工具调用仍由原管线落档。
- 17:21 左右，pymatgen 并发 16 的会话多次出现服务端 `Concurrency limit exceeded for user`，Codex 自动重连。后续新运行临时设 Step3/API 并发为 4，不修改仓库默认配置，不改变筛选要求；不额外叠加新环境以免放大限流。
- 科学依赖镜像安装成功，133 个包。atomate2 的真实 `cross_validate_records_and_files` 重放成功；doped 的 `parse_defect_project_batch` 已不再缺依赖，但返回 IndexError，继续调查工具/数据兼容性。未把依赖导入成功等同于业务工具成功。
- doped 解析错误追踪到 pymatgen 的 `ionic_steps[-1]`。原始 MgO bulk XML 有 277,808,698 字节且包含 calculation 元素，不能据此断言源文件缺失计算步骤；该业务解析问题仍保留。安装解析到 pymatgen 2026.5.4、pymatgen-core 2026.8.30、numpy 2.4.6，与上游冻结版本相符。
- 使用真实 MgO 热力学输入（mgo:MgO-Mg、Fermi=0.5 eV），数值计算已成功，但原运行时生成 `.cache/matplotlib/fontlist-v3.11.0.json` 和 `.config/matplotlib`，被未声明状态保护拦截。新增真实缓存污染测试，修改前失败；设置 XDG_CACHE_HOME=/tmp/cache、XDG_CONFIG_HOME=/tmp/config 后 20 项执行测试通过。保护未放宽。
- 17:40 启动 atomate2 的 15 条候选和 doped 的 26 条候选，各并发 4，使用独立 Python 3.11 + 上述依赖。运行目录见各环境 stability.jsonl。
- pymatgen Step3 用时 1470.886 秒，22 条候选中 3 条 execution.success=true（task9、task10、task18），已进入 Step4。此次 16 并发下的超限与模型业务失败均保留在原日志中。
- pymatgen Step3→5 完整耗时 1873.719 秒，最终 2 条通过、20 条拒绝。task18 的工况/k点覆盖不足被 Step5 拦截；人工发现已通过 task9 的文本“指定 k 点”仍未给出索引或选择规则，属于自动检查漏检，未修改文本或规则粉饰结果。
- atomate2 Step3 耗时 2222.260 秒，15 条中 11 条执行成功，按原 top-count/多样性筛选选出 10 条进入后续阶段。最终验收尚在运行。
- 8 个并发会话下仍出现服务端并发超限，至少部分会话耗尽 Codex 重连后退出；为其准备独立单并发补跑，保留原记录。候选业务未完成不会自动重跑以刷通过率。
- doped `plot_formation_energy_diagram` 的 schema 允许 unstable_entries="all"，doped 3.2.1 明确拒绝该值，接受 True/False/"not shallow"。这是某个合法公开参数与实现不兼容，不代表全部绘图能力不可用；未修改上游工具合同。
- pymatgen 的 `inspect_research_files` 在列目录时返回 `data.files`，成功 schema 却无条件 required=[path,size,sha256]，因此列目录返回被拒绝。这是输出协议与操作分支不一致，不是 LLM 填参错误。失败详情与原始返回仍在 tool_calls.jsonl 中。
- 缓存定向修复后，真实 `analyze_defect_thermodynamics(material_id=mgo, fermi_level_ev=0.5, limit_id=mgo:MgO-Mg)` 已通过完整 call_environment_tool 状态和输出校验，success=true、error=null。
- atomate2 首轮 Step3→5 共 2932.969 秒，最终通过 6 条、拒绝 8 条，另 1 条成功执行候选未通过 top-count 筛选。通过者 task13/task15 分别记录 34/32 次有效调用，超过 prompt 的 20–30；未增加用户此前不要求的硬校验，明确作为现存质量缺口。
- 18:30 开始单并发、干净初态补跑 pymatgen task2/task16（原因为服务退出/1200秒超时），随后补跑 atomate2 task5/task6/task8；目录 `runs/three_search_runtime_retry_20260917`，每次 provenance 与结果保存在 recovery.jsonl。补跑运行时使用 Python 3.11 和声明版本的科学依赖。
- doped task17 在 28 次调用后 execution.success=true：CdTe 缺少化学势边界，实际探索后转到 MgO；完成可用的 Kumagai 修正复算、富/贫端形成能分析和 JSON/CIF 导出，对缺失高电荷态 OUTCAR 保留异常说明。公开搜索引用了 doped、ShakeNBreak 和 FNV 修正论文等参考。仍需最终匹配审查。
- pymatgen task2 单并发补跑已完成 21 次调用并 execution.success=true，原来服务端退出的候选可以恢复；最终 Step5 仍待完成。
- doped 畸变生成的未声明状态错误已单独重放：`generate_shakenbreak_distortions` 正常返回结果之外，还创建 `workspace/filesystem_scopes/doped_files/.../.snb-stage-*/payload/...` 目录，位于真实 filesystem_scopes 之外。该行为与前述 Matplotlib 缓存不同，属于工具/依赖的输出路径问题；状态保护正确拒绝，未放宽。
- **更正 doped IndexError 的归因**：同一环境、代码和参数下，2 GiB 限额稳定触发 `ionic_steps[-1]` IndexError，4 GiB 下 batch parser 成功；再次降回 2 GiB 又失败。随后 task6/task22/task26 的 single-defect parser 在 4 GiB 下全部通过完整 MCP 状态及 schema 校验。这是运行预算不足造成的假阴性，不应归咎为原件损坏或工具本身无法解析。doped 补跑仅将 execution.tool_max_memory_bytes 调为 4294967296，不改仓库默认值。
- doped 首轮 Step3 共 4869.887 秒、26 条候选、4 条执行成功。补跑范围为运行中断的 task2/task5/task7，以及受上述资源故障影响的 task6/task22/task26（包括初步声称完成的 task26，其原推理依据含资源假阴性，最终应以重跑为准）。
- atomate2 Step4 502 的 task10 直接复用原 Step3 和状态补跑 Step4→5，通过 1 条；运行目录 `runs/three_search_compose_retry_20260917/pypi_atomate2_5/20260917_185738_102320_pypi_atomate2_5_gpt-5.6-sol`，未重演业务调用。
- pymatgen 两条单并发补跑均不再发生原先的终止故障，但最终 0 条通过：task2 未完整核验任务要求的所有原始文件，Step5 拒绝；task16 由 agent 如实判定未完成。没有因重试放宽质量。
- doped 首轮走完 Step5，用时 5203.994 秒，自动通过 1 条（task26）、拒绝 25 条；task26 的推理包含受 2 GiB 限额影响的解析结论，已列入 4 GiB 补跑，不能把首轮自动通过当成已消除资源问题。
- doped task17 被 Step5 拒绝：导出的修正异常清单列了 q=+2、+4 缺 OUTCAR，遗漏 q=+3。此为交付物覆盖缺口，未放宽检查。
- doped task19/task21 在 Step4 收到 HTTP 502，但正文是 `Your input exceeds the context window of this model`，不是可靠等候解决的临时故障。对应任务生成 prompt 分别为 4,213,187 / 2,809,721 字符（4,227,985 / 2,824,154 UTF-8 字节）。这两条含大量原始工具结果；现有序列化已无缩进，仅压缩空格无法根治。需要单独确认长工具结果的组织/引用/摘要方案，本轮不静默删减证据或改框架。
- **更正链长归因**：当前 `execution_agent.meaningful_calls` 将通过错误输出 schema 的 success=false 也计入，未区分填参错误、工具实现异常与有价值的业务排除。atomate2 task13 的记录数34包含4次 fermi_range_out_of_bounds，扣除后正好30，与 agent 原文一致；task15 的32中包含2次同类填参错误和1次底层 TaskDocument API 调用异常；补跑 task5 的32包含2次填参错误和2次 TaskDocument 异常。因此此前“记录数超过30”不能直接证明模型有效链超长。保留全部 raw 轨迹；需要错误语义分类来对齐计数，不能把所有业务失败都删掉，也未擅自添加硬长度校验。
- atomate2 的执行补跑最终 3 条全部通过，另 Step4 单独补跑通过 1 条，连同首轮共 10 个不同 task_id。TaskDocument API 异常所用 atomate2==0.1.5、emmet-core==0.87.2 与上游冻结清单一致，不是自行升级依赖造成；需要上游检查版本组合/调用方式。
- 完整执行补跑从相同初态重新运行同一候选，不是恢复原模型会话；业务选择包含随机性，轨迹未必相同。旧状态和日志不覆盖。仅 Step4 的补跑复制并复用既有执行与状态，统计时不重复计算这部分搜索/工具调用。

## doped 的数据组合限制（直接查询初态登记表）

| 材料 | 缺陷条目 | 竞争相记录 | 化学势边界 | 登记 DOS | 载流子情景 |
| --- | ---: | ---: | ---: | ---: | ---: |
| MgO（氧化镁） | 5 | 4 | 2 | 0 | 0 |
| CdTe（碲化镉） | 7 | 0 | 0 | 1 | 10000 |

竞争相与化学势边界用于限定稳定生长条件；DOS 是电子态密度，是部分载流子计算的必要输入。这里计数的是工具可访问的登记记录，不能由“某个原始文件可能含相关数据”推断缺失的登记对象已经可供工具使用。

这解释了为什么许多要求“同一材料同时重建边界、完成自洽载流子分析并核验缺陷原件”的 objective 无法完成：不同材料各自只具备其中一部分配套数据。网络文献提供的是一般工作流，不能补齐本地缺失的计算输入。有效的业务失败应保留，不能为了通过而编造记录或降低完成标准。
