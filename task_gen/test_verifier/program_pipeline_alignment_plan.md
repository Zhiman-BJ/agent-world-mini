# Program verifier 管线对齐修改计划

**目标：**复用任务原有工具及软件环境，交付可迁移的相对路径 verifier，让生成验收中的可修复问题交给 Codex 修改后再验收。

**实现方式：**保留设计、逐文件实现、最终 Codex 修复的结构。补齐原环境调用入口和宿主复验反馈，不建立另一套工具运行框架。

**依据：**本次用户要求；`program_verifier_prd.md` 的生成/应用分离、生成代码不可调用 `task_gen.program`、应用错误整题回退等约定。本轮实现与验收记录见 `runs/pipeline_alignment_20261019/REPORT.md`；历史批次未修改。

## 一、对照结论

|用户要求|现有实现|具体差距|
|优先用原环境已有能力，不能逐题给公共环境补包|`EvalCase.runtime` 已包含原交付绑定；`prepare_evidence` 没有传递。SDK 只有函数、模型与聚合入口|能读工具源码，但没有正式接通原工具和原软件环境。此前添加公共 `pyvcd` 是补症状|
|路径相对化，能够迁移|原始 program_request 为相对路径；runner 将其转为绝对路径；SDK 接受绝对证据引用；设计 prompt 仅写“优先”相对路径|生成器和运行时模型仍可能复制宿主路径到程序或结果|
|检查出问题后由 Codex 修好|会话内验收失败可继续修；验收标记出现后结束会话，再由宿主独立复验|宿主复验失败直接抛错；尤其最终复查后没有再反馈修复的循环|
|最终 Codex 能修整个生成包|最终会话已有整包修改权限，也会真实试跑|失败子文件只留在对应 scratch，未必进入整包；prompt 给出的前序日志目录未显式加入最终沙箱挂载，不能依赖仓库挂载偶然覆盖日志|

原环境能力已经找到实证：P04 的 `inspect_validate_vcd_file` 与 `audit_waveform_record` 使用 `vcd.reader.tokenize`；其原软件 profile 的 requirements 已声明 `pyvcd==0.5.0`、`vcdvcd==2.6.0`，对应 Python 和软件目录存在。现已实际调用，并在四版对应沙箱入口验证原环境解析器可用；详见实施记录。

## 二、按依赖顺序修改

### 1. 接通原工具与原软件环境

涉及：`program_verifier/data.py`、`application/sdk.py`、`application/runner.py`、`generation/model.py`；新增 `program_verifier/environment_runtime.py`，集中封装已有 Harness 接口。

- [x] 从 `case.runtime` 保存环境标识、工具版本与原交付绑定的相对定位；宿主从启动配置传入 delivery 根目录。沿用已修复工具文件的选择，记录实际工具摘要，不能悄悄换回另一版 tools。
- [x] 用 `harness.delivery.load_delivery` 解析原软件 profile；用已有 `harness.execution.call_environment_tool` 执行工具。传入被检查的初态、候选终态或参考终态，不能误用环境交付时的基线数据。
- [x] 新增 SDK 入口 `call_environment_tool(name, arguments, *, state='actual') -> dict`，返回原工具结果并记录耗时。状态支持 `initial/actual/reference`；生成代码使用工具名和参数，不接触宿主安装路径。
- [x] 同一适配层提供 CLI，供生成 Codex 和 verifier 内的 Codex 调用。生成、自测、最终复查、正式应用使用同一绑定；沿用现有沙箱，并挂载必要的原软件环境。普通 LLM 只收到调用结果，不能假定它有工具。
- [x] 检验所需的工具优先读取证据。工具若确实需要写临时数据，使用 scratch 中的独立普通副本，不能改动候选或参考证据。复制范围按实际工具需求确定。
- [x] 若模型需要原环境中的包直接计算，将对应 helper 在原软件解释器中执行并以 JSON 交换结果；不要通过把两套 Python 的 site-packages 拼接起来解决依赖。
- [x] prompt 改为：先检查原工具、包及适用范围；可复用则使用；不适用则选择现有静态能力或 Codex。工具调用成功不能直接等同于任务完成，仍须按 rubric 检查结果。
- [x] 接入通过后移除 verifier 公共 requirements 中为 P04 单独补的 `pyvcd`。不卸载共享环境已安装的包；验收要证明不依赖它。其他解析库逐项核实来源与用途，不因本次问题一并删除。

验收：P04 的一个真实 VCD，在 verifier 自身 Python 无 `vcd` 可用的条件下，通过原工具及原 profile 完成检查；记录使用的解释器/工具版本，并确认证据未改变。再保留一个没有原运行绑定的简单任务，确认普通静态检查仍能正常工作。

### 2. 统一可迁移的路径接口

涉及：`data.py`、`contracts.py`、`application/runner.py`、`application/sdk.py`、`generation/pipeline.py` 的 SDK_CONTRACT 及两个生成 prompt、`final_review_prompt.md`。

- [x] 新生成的 verifier、模型输入输出路径表和评分 evidence.path 只使用相对标识：`evidence/...` 相对证据根；`scratch/...` 相对 scratch 根；原工具参数相对其声明的 scope。工具与模型调用实现文件使用 `helpers/...`、`model_calls/...`。
- [x] 文件读取可以由 `context.resolve_evidence_path(relative)` 在运行时取得 Path；宿主在本次进程中解析绝对位置是正常实现细节，不把这些位置持久化回交付代码或交给模型抄写。
- [x] 使用已有解析函数统一做存在性与边界检查，提供统一的相对路径序列化入口。删除“绝对或相对都可以”的新生成接口说明，不让每个 helper 自行做字符串替换。
- [ ] 通用原工具返回路径转换尚未加入：本次 P04 的业务路径原本就是相对路径。已提供 context.evidence_path 序列化入口，并检查最终 evidence 边界；不猜测修改原工具自由文本或日志。
- [x] `fallback_input.json` 中的文件入口改为证据包相对引用，真正调用旧 verifier 时才解析成当前路径；继续使用原整题判定逻辑。机器上的解释器、认证和挂载配置由宿主提供。
- [x] 给 Codex 稳定的工作目录与相对证据入口。安装位置仅由启动器解析；若发现生成物残留旧根目录，作为诊断交给生成 Codex 修改，不静默猜测修正路径。

验收：把同一 verifier 和证据包复制到另一目录，隐藏原目录后再次运行，评分与证据引用保持有效；迁移后触发一次受控应用错误，确认旧 Codex 回退也读取新位置。证据目录本身可以作为合法引用。

### 3. 补齐“宿主复验失败 → Codex 修改”的闭环

涉及：`generation/model.py`、`generation/pipeline.py`、`final_review.py`、`final_review_prompt.md`。

- [x] 保留检查与诊断，改变失败后的控制流。模型执行验收失败时继续在会话内修；宿主独立复验失败时，将 stderr、对应文件和真实试跑结果交回 Codex，继续修同一生成包后再验收。
- [x] 最终交付标记以宿主复验成功为准；模型侧成功标记仅表示“准备好复验”。可以续会话，也可以携带原工作目录与诊断开启下一次会话，不能重抽任务或把它计成新的独立生成。
- [x] 逐文件阶段确实无法自行修复的设计/跨文件问题交给最终 Codex，保留该文件最后一次实现供最终 Codex 参考。不得只传一个缺文件错误、丢掉已有实现。
- [x] 最终会话显式只读访问必要的前序日志与失败工作目录；修复在当前可写 verifier 副本中进行。这样日志可见性不再依赖输出是否恰好位于挂载的仓库目录内。
- [x] 每个生成阶段的修复共享该阶段既定调用预算，记录每轮诊断、改动、耗时和模型 usage；预算耗尽或原环境服务不可用时如实记失败。不能保证 Codex 能修复外部服务故障，也不能用假通过掩盖。
- [x] 应用阶段保持既有规则：交付后运行出现无法评分的错误，直接整题回退原 Codex，不在候选评分过程中修改 rubric 或生成代码。合法零分、低分仍是正常完成，不为满分改变标准。

验收只保留关键例：模型自测通过但宿主复验首次失败，Codex收到同一个包和诊断并修复，第二次复验成功；以及预算耗尽时保留失败与全部费用，不发布成功。另验证一个失败子文件和前序日志在最终沙箱确实可读。

## 三、实施与结果口径

- [x] 先完成原环境接入，再统一路径，最后补齐宿主复验反馈；每项通过相应小测试后再进入下一项。
- [x] 补充 `program_verifier/tests/test_environment_runtime.py`；扩展现有 `test_data.py`、`test_runtime.py`、`test_generation_workspace.py` 和 `test_verifier/tests/test_final_review.py`。共享 final review 的改动需同时检查 codex+工具分支，避免修改只适用于 program。
- [x] P04 真实原环境集成和迁移已验证；无原运行绑定的结构化 JSON 场景由真实沙箱回归覆盖。没有启动新的20题模型批次；旧快照、生成物、配置、原始结果全部保留。
- [x] 报告同时列“逐文件阶段结果”“最终自动修复结果”“交付后应用结果”；所有生成内修复计入生成成本，不能混称首次代码已正确。

本计划不把三类问题改成一串新的拒绝规则；检查的作用是给 Codex 提供准确修改依据。原工具适配仍需上述真实接入测试，不能只凭 import 成功宣称完成。

## 2026-10-19 实跑后的补充

P04 两项原工具已在原软件 profile 和 verifier 同类外层沙箱中实跑成功，未补包、证据未变。新增确认：外层不仅要挂载 profile，还要挂载其 Python 符号链接指向的实际解释器目录，否则会报文件不存在。20题绑定均为 python_profile，本次无需启动 Docker；Docker 后端未验证。

验收需要再调整两项：最终包检查应与阶段检查一致地忽略普通 Python 编译缓存；README 等无害交付说明由最终 Codex 整理，不应因白名单错误直接结束生成。保留真实评分结果和证据有效性检查，失败仍交回 Codex 修复。

实跑与复现证据见 `runs/runtime_acceptance_probe_20261019/REPORT.md`。本轮20题中的3个生成失败都发生在最终Codex第一次真实验收成功、宿主第二次复验失败之后，进一步确认必须补齐反馈闭环。未修改运行中的或已完成的批次。
