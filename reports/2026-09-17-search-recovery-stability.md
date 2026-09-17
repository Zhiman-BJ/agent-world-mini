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
