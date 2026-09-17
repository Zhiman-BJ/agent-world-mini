# 网络恢复后的三环境试跑

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
