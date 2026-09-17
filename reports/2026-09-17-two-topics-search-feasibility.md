# klayout 与 doped：网络参考可执行性试跑

## 范围与记录方式

本轮按用户确认只包含 klayout、doped 两个主题，不再把更新编号的 pymatgen、atomate2 计为新主题。使用 `c19e91c` 的网络参考 prompt：采用外部任务要求之前验证当前环境可执行性，不能满足的外部内容舍弃，可仅借鉴语言、情境和结果表达；改链后复核最终任务覆盖。其他完成标准不变。

每次模型调用由原管线保存 prompt、answer、所在阶段、耗时、usage、错误；业务工具调用和 Codex 搜索事件保存于任务目录。stability.jsonl 记录管线启动、中断和重试。本报告补充诊断、处理依据和结果；旧运行不覆盖。失败调用未返回 usage 时不能当成零成本。

## doped

- 2026-09-17 20:55 启动；从原 Step2 复用全部 26 条候选，从干净初态重新执行 Step3→5，没有复用原任务答案。
- 来源：`runs/three_search_recovered_20260917/pypi_doped_6/20260917_174048_671021_pypi_doped_6_gpt-5.6-sol`。
- 新运行：`runs/two_search_feasible_20260917/pypi_doped_6/20260917_205555_110038_pypi_doped_6_gpt-5.6-sol`。
- 模型 gpt-5.6-sol，温度 0，最大输出 131072 tokens，API/执行并发各 4，搜索 1–3 次，20–30 次有效业务调用。工具内存 4 GiB，沿用上一轮复现验证后的预算。
- Python 3.11 隔离运行时：atomate2==0.1.5、doped==3.2.1、shakenbreak==3.4.4，及原管线 openai/pyyaml/jsonschema 依赖。默认主环境未修改。
- 状态：运行中。最终通过数量及故障将在完成后更新。

## klayout

- 来源：`/data/agentworld-toolgen-results/environments/pypi_klayout_7`。
- 20:55 前置检查仍失败：`tools/tools.json` 和 `tools/tool_validation.json` 所有者为 sunhenghui、权限 600，当前账号读取返回 PermissionError。已告知用户需要可读文件，未绕过权限、未用旧版种子代替新版工具。
- 环境说明可读：IHP SG13G2 版图验证与修复，KLayout/gdstk 处理 GDSII/OASIS、DRC 规则与报告；没有 Record Set，仅 filesystem scope。因此不存在 records.sqlite 本身不是错误。
- 约 21:03 检查确认两个必需文件已可读；Python 3.10 隔离依赖 klayout==0.30.12、gdstk==1.0.1、numpy==2.2.6 导入成功。
- 21:05 将上游 environment 目录及 tools/tools.json、tools/tool_validation.json 原样复制到独立运行包，按现有 Step0 的目录要求放置，未改内容。从 Step0 启动全流程。
- 新运行：`runs/two_search_feasible_20260917/pypi_klayout_7/20260917_210511_783380_pypi_klayout_7_gpt-5.6-sol`。
- 使用 config/tool_graph_sol_low.yaml，环境/产物路径另设，网络参考开启，API/执行并发各 4。其余沿用该配置，包括 100000 次采样、初始链长 20–30、30 条 review 候选、最终最多 10 条、tau=0.4、权重采样概率 0.1/0.2/0.7、工具内存 2 GiB。
- 状态：运行中；与 doped 并行。
- Step0 0.396 秒，Step1 686.788 秒，Step2 818.513 秒；17 个工具、152 条边（Level3 68、Level2 55、Level1 29）。已进入 Step3。这些是本次实测，不复用其他主题的图。

## 对照解释边界

复用 doped 的 objective 和原链可以检查本次搜索段落修改后的行为，但仍有模型随机性；不能把结果差异全部归因于 prompt。原 objective 中已有的不可执行要求不会仅因舍弃搜索内容自动消失，也不能通过放宽原任务完成标准提高通过率。

## 过程观察

- 约 21:08，doped 已出现提供方断流及 overloaded 自动重连事件，未因此终止管线。事件原文在任务 stdout.log；不能把自动恢复事件都当成失败任务。
- doped task3 执行者返回未完成。它明确未将环境不具备的新弛豫计算纳入交付，说明搜索内容取舍发生了；但原 objective 要求复核已有研究并明确标出缺失，reason 却以缺少独立修正复算、畸变结果作为整体拒绝条件。这里可能是执行者扩大完成标准，不能仅凭其自述把全部失败归因为原目标不可执行。
- doped task2 的原 objective 明确要求关键缺陷最低能构型核验，当前选定 CdTe 条目没有畸变候选；task4 原 objective 明确要求两端自洽费米能级及可接受电中性残差，执行发现参照能缺失和显式计算/预计算网格不一致。这两类要求在本轮搜索之前已经存在，与 task3 的标准解释问题须分开。
- klayout 进入 Step3 时也出现提供方 overloaded 自动重连事件；目前保留原会话继续观察，不通过重启覆盖记录。
- klayout 首轮四条全部执行未完成、最终 0 条通过，总阶段运行约 1997 秒。共同阻塞是 `run_maximal_drc`/`run_eco_regression` 访问 `context.software_root` 时 AttributeError；上游交付契约确实要求此字段并提供了绑定 software profile，属于我方运行时缺少接入。
- 修复：增加可选 `execution.tool_software_root`，经 MCP 传到共用工具沙箱，指定目录只读挂载为 `/software` 并通过 context 暴露。原来已挂载的系统库补充 `/usr/lib` 路径供原生程序 RPATH 加载；未开放网络、未放宽状态写入检查、未修改上游工具。MCP 与 ReAct 共用调用入口均支持该可选参数。
- 首次重放软件目录接入后错误变为 KLayout 退出 127。进一步最小探针确认缺 `libpulsecommon-15.99.so`，它在本机 `/usr/lib` 中；补齐只读库路径后 `klayout -b -v` 返回 0.30.12。再次重放原 task3 的 metal2 baseline DRC 输入，3.307 秒返回 success=true，生成报告/日志，8 个 marker、237 个类别，通过完整 MCP schema 与状态检查。诊断使用临时初态副本，没有更改首轮结果。
- 验证：新增只读软件目录测试修复前报接口缺失、修复后通过；执行测试 21 项，task_eval 与状态运行时测试 21 项均通过。将用同一 Step2 的四条候选另开运行补跑。
