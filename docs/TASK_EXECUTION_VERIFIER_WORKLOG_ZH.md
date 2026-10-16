# 任务真实执行与独立验证：完整工作日志

## 1. 这部分解决什么问题

任务生成流程已经产出任务文本、可用工具、初态 workspace、参考调用链、参考终态和参考回答，
但这些材料只能说明“生成器认为任务可执行”，不能证明一个新的 Agent 真能完成任务，也不能证明
参考回答就是唯一正确答案。

本次工作的目标是增加一条独立评测流程：从已有 `runs/taskgen` 读取任务，让新的 Codex Agent
在初态副本中真实执行，再仅依据任务文本审核实际执行是否完成全部要求。它不进入 task generation
pipeline，不修改任何 step、contracts 或任务生成结果。

本次实现遵循四个已经确定的判断原则：

1. 任务文本是成功条件的唯一来源。
2. 参考执行只是一条已知完成路径，用于帮助生成和校准审核标准，不是要求逐字、逐调用复现的答案。
3. 被审核的实际证据只有工具调用轨迹、初态与终态 workspace、最终回答。
4. 所有必需子要求都通过，任务才通过；环境、服务或 verifier 的问题不能算成 Agent 失败。

## 2. 工作区、分支和提交

为避免影响正在使用的主工作区，本次开发在独立 Git worktree 中完成：

- 主仓库：`/data1/home/tianfang/agent-world-mini-zhiman`
- 独立 worktree：`/data1/home/tianfang/agent-world-mini-zhiman/.worktrees/task-execution-verifier`
- 分支：`feature/task-execution-verifier`
- 基线：`a7fab11 chore: ignore local worktrees`
- 功能实现最终提交：`114a826 feat: finalize task execution verifier`

开发过程保留了四个提交，以便回看中间版本：

| 时间 | 提交 | 内容 |
| --- | --- | --- |
| 2026-09-02 22:22 | `f93b75d` | 增加任务专用 verifier、PRD、基础测试和 Codex 自动授权参数 |
| 2026-09-02 23:48 | `4492721` | 根据第一批真实退步修正 verifier 生成与校准 |
| 2026-09-03 13:14 | `a83eca0` | 保留后续校准退步，增加反事实校准、语义复核和大量回归测试 |
| 2026-09-03 15:10 | `114a826` | 修正基础设施归因与 Agent 提示，完成最终分析文档 |

当前 worktree 已提交且无未提交修改。分支尚未合并到 `main`，也没有推送远端。

## 3. 相对 main 的全部文件修改

包含本日志在内，相对 `main` 共涉及 8 个文件，净变化为 2581 行新增、53 行删除。

| 文件 | 类型 | 实际作用 |
| --- | --- | --- |
| `task_gen/task_eval.py` | 修改 | 将原有“一次 Agent 执行 + 整体 LLM judge”入口升级为任务读取、verifier 准备、Agent 执行、证据审核、归因和结果持久化的总控 |
| `task_gen/task_eval_verifier.py` | 新增 | 生成、校验、沙箱执行、校准和缓存任务专用 verifier |
| `utils/search_agent/codex.py` | 修改 | 给 `CodexAgentClient` 增加 `approve_for_me` 开关，使独立评测中的 Codex 可在隔离任务 workspace 内执行工具 |
| `tests/test_task_eval.py` | 修改 | 覆盖证据、verifier、安全、校准、缓存、Agent 重试、任务读取和 MCP 边界，共 50 个相关测试方法 |
| `tests/test_codex_agent_client.py` | 修改 | 验证自动授权模式交给 Codex CLI 管理 sandbox，不再同时传冲突的 `--sandbox` 参数 |
| `docs/TASK_EXECUTION_EVALUATION_PRD_ZH.md` | 新增 | 固化任务执行审核目标、输入、证据和归因原则 |
| `docs/TASK_EXECUTION_VERIFIER_RESULT_ANALYSIS_ZH.md` | 新增 | 记录最终结果、主要退步、参数评估和剩余问题 |
| `docs/TASK_EXECUTION_VERIFIER_WORKLOG_ZH.md` | 新增 | 完整记录独立执行、验证机制、迭代过程、试跑结果和证据位置 |

`task_gen/task_eval_mcp.py` 是此前已经存在的独立 MCP 网关，本分支没有修改它，但真实执行会
直接使用它。它复用了 step 3 已有的工具代码执行器、schema 检查、超时、内存和写入上限，
没有另外实现一套环境工具运行框架。

## 4. 一次完整评测到底怎样执行

下面按代码真实发生的先后顺序描述一项任务的生命周期。

### 4.1 读取任务

入口为：

```bash
python -m task_gen.task_eval \
  --input-root <已有 taskgen 运行目录> \
  --output-root runs/task_eval \
  --config config/tool_graph.yaml \
  --model gpt-5.6-terra \
  --max-tool-calls 20 \
  --max-concurrency <并发数>
```

`load_cases()` 扫描输入根目录下一层的 `tasks.json`，同时要求该 run 存在
`intermediate/step_5_bundle.json`。同一个 `environment_id` 如果有多轮结果，只选目录名最新的
一轮。本次实际读到 3 个环境、14 个任务：Bugagent 3 个、Finstat 2 个、Happyscribe 9 个。

每个任务读取以下材料：

- `task_text` 和 `available_tools`；
- 环境公开信息和公开工具契约；
- `initial_state` 指向的初态 workspace；
- `reference.final_state` 指向的参考终态；
- 参考工具调用和参考回答。

读取时会拒绝绝对路径、越出 source run 的相对路径、非法 task ID、环境 ID 不一致、缺失目录和
任何符号链接。任务中保存的公开工具列表还必须与环境当前公开契约完全一致，防止拿错环境执行。

### 4.2 为任务建立独立 workspace

执行前先计算来源初态的路径、文件模式和内容哈希签名，再用 `copytree` 复制出该任务专属
workspace。Agent 和工具只面对这个副本。执行结束后重新计算来源初态签名；如果来源目录被改动，
整项评测直接报错，避免测试污染金标准。

每个任务的副本保存在当前 run 的：

```text
workspaces/<source_run>__<task_id>/
```

### 4.3 先生成并冻结任务专用 verifier

如果任务带有参考终态，系统先将参考执行整理成证据包，包括：

- 参考最终回答；
- 有界的参考工具调用记录；
- 初态与参考终态的新增、修改、删除路径；
- 所有文件的相对路径、大小和 SHA-256；
- 在 65536 字节预算内纳入的 UTF-8 正文或 JSON 结构。

变化文件优先取得正文，其次优先 JSON 和较小文件。未变化的大文件仍保留路径、大小和哈希，
但不一定把正文送进 verifier 生成提示，从而控制上下文体积。

随后调用 LLM 生成一份任务专用 verifier。输出包含：

- `requirements`：把任务拆成可以分别判断的原子要求；
- 每项要求的证据通道、通过条件和失败条件；
- `verify(ctx)`：使用受限上下文读取证据并逐项产出结论的 Python 函数。

生成提示明确禁止把参考调用顺序、工具次数、动态 ID、时间戳、技术字段或参考措辞当作任务要求；
查询和计算任务不强求落盘，要求写入的内容也不能靠最终回答补足。备注、评论、摘要等自然语言
含义必须交给 `semantic_requirement`，不能用固定关键词硬判。

### 4.4 检查生成代码能否安全运行

LLM 生成的 Python 不会直接在评测进程中 `exec`。`validate_verifier()` 先做结构和 AST 检查：

- 文件只能定义一个 `verify(ctx)`；
- 禁止 import、文件打开、反射、进程调用、私有属性和双下划线名称；
- 只能访问列入白名单的 `VerifierContext` 方法；
- 每个 requirement 必须有唯一 ID、完整契约和合法证据通道。

通过静态检查后，verifier 复用已有 bubblewrap 工具执行器运行，默认超时 30 秒、内存 512 MiB、
写入上限 1 MiB。运行结果还会再次检查：每个 requirement 必须恰好返回一次，状态只能是
`pass`、`fail`、`indeterminate` 或待语义审核的 `semantic`，所有 `evidence_refs` 必须真实存在。

### 4.5 冻结前进行三类校准

生成的 verifier 最多尝试 3 次。每次都必须经过以下校准：

1. **参考执行校准**：同一份 verifier 跑参考证据，每个原子项再按任务文本做语义确认；参考执行
   必须整体通过。参考本身不能满足任务时，任务标为 `indeterminate/verifier`，不启动 Agent。
2. **反事实校准**：改变参考中新生成的动态 ID 和技术字段承载方式，同时保留初态稳定 ID、任务
   明确写出的标识和业务含义。verifier 仍须通过，用来发现对参考实现的过拟合。
3. **空执行校准**：使用“初态等于终态、无调用、无回答”的证据。整项任务绝不能通过。只有
   “保持原内容不变”这种子要求，且同一路径初末哈希相同，才允许单项通过。

失败后下一次生成只接收截断后的失败原因，不再接收上一份 verifier 全文，避免模型被错误代码
锚定。三次都无法校准时保存全部尝试，并归因为 verifier 的 `indeterminate`。

校准成功后，将 verifier 和校准结果以任务、环境和参考证据的 SHA-256 指纹缓存。当前指纹版本
为 7；规则改变就升级版本，旧缓存不会误用。缓存目录现有 53 个历史条目，共约 836 KB。

### 4.6 让 Codex Agent 真实执行

每个任务启动一个临时 stdio MCP server，只暴露当前环境公开工具。Codex 工作目录就是该任务的
workspace 副本，网络关闭。Agent 可以直接读取 workspace，便于理解专业文件；所有业务状态
修改必须经过 MCP 工具，以便留存调用轨迹。

为了让 Codex 真正获得在隔离 workspace 中工作的权限，本分支给通用 `CodexAgentClient` 增加了
`approve_for_me` 参数。仅任务评测子类默认启用它：CLI 收到 `--approve-for-me` 后自己管理工作区
sandbox，不再同时收到一个冲突的 `--sandbox` 参数。其他调用者默认仍保持原行为。

Agent 能看到任务文本、环境公开信息和工具，不会看到 verifier。提示要求它：

- 自己发现内部 ID，不得猜测；
- 不止给计划，必须真正完成任务；
- 任务本身已经构成执行授权，不再向用户请求确认；
- 429 或 503 发生时，只要调用额度仍够，至少重试一次；
- 默认最多调用 50 次工具，并为必要的状态变更和结果验证预留额度；命令行仍可覆盖该值；
- 最终只返回用户可见答案。

### 4.7 MCP 工具调用如何落地

Agent 每次调用 MCP 工具时，网关依次执行：

1. 校验工具名、调用次数上限和输入 schema；
2. 再复制一份当前任务 workspace 到临时 candidate；
3. 在 candidate 中运行环境工具的 Python internal code；
4. 检查超时、内存、写入量、返回 object、`success=true` 和输出 schema；
5. 只有全部成功，才以目录 rename 原子替换任务 workspace；
6. 无论成功或业务错误，都把工具名、参数、结果和错误写入 JSONL trace。

这使单次工具失败不会留下半写状态，并且 verifier 能看到真实失败和重试，而不是只看到 Agent
最后声称成功。

### 4.8 基础设施启动错误如何处理

Codex 执行最多使用 `execution.retry_count`，当前默认 3 次，但自动重启条件非常窄：

- 没有任何工具调用；
- workspace 与初态完全相同；
- 最终输出明确包含 503；
- 同时说明 service 或 approval 不可用。

只有四项同时满足，才认为 Agent 根本没有开始业务执行，可以安全重启。已产生工具调用或状态
变化后不会自动重跑，避免重复创建、重复写入。连续三次仍是启动 503，归为
`infrastructure_error`，而不是 Agent fail。

### 4.9 审核实际执行

Agent 完成后，系统从实际 workspace 构建与参考相同结构的证据包，再运行已经冻结的 verifier。

确定性部分由 Python 检查，例如路径、文件哈希、JSON 字段、数量、金额、状态和工具是否返回成功。
自然语言部分只把该原子要求明确引用的回答、调用或文件片段交给 LLM，多项语义要求一次批量调用，
减少请求数。语义返回优先要求严格 JSON，同时兼容 CLI 明确输出的中英文单行格式。

如果生成代码先给出 fail 或 indeterminate，系统会对这些非通过项做一次窄范围语义复核：只看原
任务、原子要求和同一组 evidence refs，忽略 verifier 擅自添加的日期、格式、载体或操作方式。
它不能寻找新证据，也不能补足空交付物，作用只是纠正生成代码的偶然过拟合。

汇总不允许得分补偿：任一 required 为 fail，整项为 fail；否则任一 required 为 indeterminate，
整项为 indeterminate；全部 required 通过才是 pass。

### 4.10 结果怎样保存

每个任务完成后立即写入：

```text
task_results/<source_run>__<task_id>.json
```

其中包含任务、Agent 回答、工具调用、workspace 变化、逐项 requirement 结果、verifier、校准历史、
缓存命中情况、归因和错误。冻结 verifier 另存到 `verifiers/`；所有任务结束后再汇总成
`results.json`。逐任务即时持久化是为了长时间并发运行中即使后续进程失败，已经完成的结果也不丢。

## 5. 实际迭代过程和每轮结果

本次不是一次写完，而是反复运行 14 个任务、检查错误归因和真实产物，再调整实现。工作区保留了
13 轮完整 `results.json`：

| 运行 | pass | fail | indeterminate | infrastructure | 当时暴露的问题 |
| --- | ---: | ---: | ---: | ---: | --- |
| `20260902_222323_542981` | 8 | 2 | 4 | 0 | 首版任务专用 verifier，仍有参考过拟合和误判 |
| `20260902_230235_257891` | 0 | 0 | 14 | 0 | 新增的全局 coverage reviewer 把合理拆分全部当成遗漏 |
| `20260902_232037_981013` | 2 | 3 | 8 | 1 | 重试携带上一版 verifier，模型持续复制坏约束 |
| `20260903_003625_099405` | 10 | 2 | 2 | 0 | 删除上述两项后恢复，但仍存在假阴性 |
| `20260903_104842_584168` | 9 | 1 | 4 | 0 | 反事实处理会修改初态稳定 ID |
| `20260903_113748_546934` | 9 | 0 | 5 | 0 | 继续收紧校准，部分有效任务被判不确定 |
| `20260903_115225_059642` | 10 | 2 | 2 | 0 | 对日期、字段和引用载体仍有过拟合 |
| `20260903_122519_342768` | 8 | 1 | 5 | 0 | v2 校准基线 |
| `20260903_123823_359152` | 10 | 0 | 4 | 0 | 表面最好，但 Happyscribe task8 的空交付物被错误放过 |
| `20260903_125940_228420` | 8 | 2 | 4 | 0 | 修复空交付物后，固定日期和固定短语产生两个假阴性 |
| `20260903_140250_980062` | 9 | 0 | 5 | 0 | v7 首次全量，校准标准基本稳定 |
| `20260903_141547_254108` | 5 | 5 | 4 | 0 | 缓存复跑时 5 个 Agent 在零调用阶段遇到 503，却被算作 fail |
| `20260903_143133_576928` | 7 | 1 | 4 | 2 | 正确区分基础设施；剩余一个 Agent 429 后未重试 |

### 5.1 明确删除或回退的方案

- **删除全局 coverage reviewer**：它要求每个原子项单独覆盖整个任务，混淆“原子拆分”和“整体
  完整性”，直接造成 0/14 通过。
- **不再把上一份 verifier 传给重试**：只反馈失败摘要，避免下一轮围绕错误代码打补丁。
- **限制反事实 ID 修改范围**：只改执行中新生成的动态 ID，保留初态 ID 和任务明示 ID。
- **禁止最终回答补足空交付物**：任务要求简报正文时，`Open actions` 空标题不能靠回答说“已完成”
  变成通过。
- **增加原任务语义复核**：解决 verifier 把参考日期或固定中英文短语擅自升级为硬要求的问题。
- **撤销逐原子“必须覆盖整项任务”审查**：原子要求本来就只负责一个目标，其他目标由其他原子项
  覆盖。

这些调整不是放宽任务标准，而是删除 verifier 自己添加、任务文本没有要求的限制。空执行校准和
所有 required 必须通过仍然保留。

## 6. 最终全量运行与补跑

最终全量运行目录为 `runs/task_eval/20260903_143133_576928`，模型为 `gpt-5.6-terra`，输入为
`agent-world-mini-zhiman-latest/runs/taskgen` 中三个环境的最新正式 run。

原始全量 `results.json` 必须如实解读为：

| 结果 | 数量 |
| --- | ---: |
| pass | 7 |
| fail | 1 |
| indeterminate | 4 |
| infrastructure_error | 2 |

随后只针对这三个非最终状态执行了定向补跑：

- Bugagent task9：全量时 Codex 进程在业务执行中断，保存为 infrastructure；低并发补跑第 1 次
  以 9 次工具调用通过。
- Happyscribe task10：全量时同类基础设施错误；低并发补跑第 1 次以 8 次工具调用通过。
- Finstat task7：全量执行 15 次工具调用后，报表调用遇到 429，Agent 没有重试。第一次提示补跑
  又只给计划并请求确认，仍正确判 fail；明确“任务本身已授权、不得请求确认”后，第 1 次以
  13 次工具调用通过。

因此，把同一批任务各自的最新有效尝试合并后，结果为 10 pass、4 indeterminate、0 fail、
0 infrastructure_error。这是“全量结果 + 三个有记录的补跑”的合并结论，不是单个
`results.json` 自己记录的统计。

## 7. 最终 14 个任务逐项结果

| 环境 | 任务 | 最新有效结果 | 调用数 | 关键判断 |
| --- | --- | --- | ---: | --- |
| Bugagent | task1 | pass | 9 | 缺陷、严重度、复现关联、评论和调查状态均完成 |
| Bugagent | task9 | pass | 9 | 原全量为基础设施错误，低并发补跑通过 |
| Bugagent | task10 | pass | 6 | 状态、复现关联和审计评论完成 |
| Finstat | task6 | indeterminate | 0 | 任务写 `78154807RG994149`，环境与参考为 `78154807RG994149F`；参考无法满足任务，未启动 Agent |
| Finstat | task7 | pass | 13 | 修正授权和错误重试提示后完成复杂账务与报表任务 |
| Happyscribe | task2 | indeterminate | 0 | 参考简报主要是引文罗列，`Open actions` 为空；未启动 Agent |
| Happyscribe | task3 | pass | 5 | 三份转写统一整理且原始证据保持不变 |
| Happyscribe | task4 | pass | 3 | 三份目标说话人转写完成归档且内容不变 |
| Happyscribe | task5 | pass | 9 | 重命名、建目录、移动和 WebVTT 导出完成 |
| Happyscribe | task6 | indeterminate | 0 | 参考简报含转写之外的断言，且 `Open actions` 为空；未启动 Agent |
| Happyscribe | task7 | pass | 12 | 标题、研究备注、证据引用和英文术语规范通过 |
| Happyscribe | task8 | indeterminate | 0 | 参考是引文堆叠，`Open actions` 为空且没有明确说明无；未启动 Agent |
| Happyscribe | task9 | pass | 6 | 五条引文、身份、软删除和行动项说明完成 |
| Happyscribe | task10 | pass | 8 | 原全量为基础设施错误，低并发补跑通过 |

四个 indeterminate 都发生在冻结 verifier 之前。系统没有为了提高通过率而让 Agent 在错误参考
上继续执行，也没有把参考质量问题记到 Agent 名下。

## 8. 与旧评测相比具体改变了什么

`main` 原有 `task_eval.py` 已经可以复制 workspace、通过 MCP 让 Agent 执行并保存调用，但最后
只调用一次整体 LLM judge，输入实际回答、参考回答、调用和路径变化，输出 `passed/score/analysis`。

这会产生两类已在真实数据中出现的错误：

- Happyscribe task2、task8 的简报只是引文堆叠并留有空的 `Open actions`，旧 judge 仍判通过；
- Finstat task6、task7 的报表工具本来只计算并返回数据，没有写文件能力，旧 judge 却因为没有
  报表文件而判失败。

新实现把判断拆成“任务原子要求 + 明确证据引用 + 确定性代码/窄语义判断”，并在 Agent 执行前先
用参考、反事实和空执行校准，因此既能发现空交付物，也不会虚构工具没有承诺的落盘行为。

为了兼容没有参考终态的旧任务，代码仍保留原整体 judge 作为 fallback；本次 14 个正式任务均走
任务专用 verifier 路径。

## 9. 测试与验证

最终提交前执行：

```bash
pytest -q tests/test_task_eval.py tests/test_codex_agent_client.py tests/test_tool_graph_llm.py
```

结果为 65 passed，耗时 4.67 秒。另执行了：

```bash
python -m py_compile task_gen/task_eval.py task_gen/task_eval_verifier.py task_gen/task_eval_mcp.py
git diff --check
```

两项均通过，最终 worktree 干净。

全仓 `pytest -q` 和 `uv run pytest -q` 在收集阶段均被 7 个基线问题阻断：4 个 data_gen 测试
加载到不含 `Draft202012Validator` 的 `jsonschema`，2 个测试缺少 `task_gen.program_form`，以及
`seed_gen/scripts/fetch_smithery_servers.py:55` 有未闭合 f-string。本分支没有修改这些模块，也没有
把无关修复混入当前工作。

## 10. 参数结论与当前边界

- `max_concurrency=16` 对纯 LLM 审核吞吐可用，但同时启动真实 Codex Agent 会触发审批服务 503。
  Agent 阶段更稳妥的并发为 4-8。
- 历史试跑使用 `max_tool_calls=20`：Happyscribe task9 曾用满 20 次，Finstat task7 复杂执行需要
  13-15 次。当前默认值已提高到 50，以减少较长任务被额度截断的风险；命令行仍可按需覆盖。
- `timeout_seconds=1800` 本轮没有产生任务超时，当前足够。
- `tool_result_max_bytes=65536` 对三份长转写偏小，后面的文件可能只有元数据或被截断。下一步应按
  requirement 定向加载证据，或只提高评测证据预算，不需要扩大整个生成 pipeline 的工具返回量。
- 缓存固定的是审核标准，不是 Agent 行为；Agent 仍有随机性，单次结果和补跑结果需要同时保留。
- 已产生业务修改后不会自动重试基础设施故障。未来若要支持，必须先保证工具幂等，或重新从初态
  建立一个全新 workspace，不能在半完成状态上盲目重跑。
- Happyscribe task7 的“中文窗口”是否违反英文术语 `WINDOW` 仍有文本边界。当前 verifier 把它
  理解为英文写法规范，不把普通中文词视作英文变体；任务若要求中文正文也必须写 `WINDOW`，应在
  任务文本中明确。

## 11. 可直接检查的运行产物

- 最终全量：`runs/task_eval/20260903_143133_576928/results.json`
- 每任务结果：`runs/task_eval/20260903_143133_576928/task_results/`
- 冻结 verifier：`runs/task_eval/20260903_143133_576928/verifiers/`
- Agent workspace：`runs/task_eval/20260903_143133_576928/workspaces/`
- 两个基础设施补跑：`runs/task_eval/20260903_143133_576928/infrastructure_retries/`
- Finstat 首次提示补跑：`runs/task_eval/20260903_143133_576928/agent_prompt_retry/result.json`
- Finstat 最终提示补跑：`runs/task_eval/20260903_143133_576928/agent_prompt_retry_v2/result.json`
- 所有历史 verifier 缓存：`runs/task_eval/verifier_cache/`

这份日志描述的是当前分支实际存在的代码和已经落盘的运行结果。最终汇总没有覆盖原始全量文件，
所有中间退步、补跑和失败尝试仍保留在各自目录，可逐项回放和比较。
