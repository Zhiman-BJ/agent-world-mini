你负责为本任务设计一套由单个主 Codex 应用的 verifier。先读取原任务、环境和工具说明、实际/参考证据索引，依据任务确定 rubric、权重、各档评分标准和覆盖，再设计静态工具与验收指引。直接在指定 verifier 工作目录写文件、运行自测并在本次会话内修正；宿主读取文件，不从最终回复提取 JSON。

只交付四个文件：
- rubrics.json：{"rubrics":[...]}。每项含 id、goal、task_quotes（任务原文 exact quote）、weight（正数）、scale（binary 或 ordinal_5）、criteria（键 "0" 到 "1" 或 "5" 的逐档可观察条件）、method（static 或 codex）、inputs、static_tools（工具 ID 数组，可空）、codex_work、evidence_plan、missing_policy、error_policy、method_reason。required_tools 仅列出无条件必须调用的 static_tools ID，包括按契约返回正常 missing/not_applicable 的情况；有适用条件或允许替代证据的工具仅在 evidence_plan 说明，不列入 required_tools。开放目标可有 subcriteria，但须说明合成方法，不能父子重复计分。
- tools.json：{"tools":[...]}，零工具合法。每项含 id、file（tools/T1.py 等）、function（通常为 tool）、purpose、rubric_ids、applicability、limits、cost_reason、path_policy、error_policy、input_schema、output_schema。schema 使用 JSON Schema 2020-12 的 object，逐字段写类型、含义、来源、必需/可选、可空条件、嵌套对象和数组元素、数值/枚举范围及额外字段政策。只能使用本地 # 引用。output_schema 必须 required status，枚举限于 ok、missing、not_applicable、verification_error；为各状态写清含义及字段。工具可以按固定标准完整评分某项 rubric，也可只返回部分事实，明确区分。
- instructions.md：给应用阶段主 Codex 的验收指引。说明如何读取证据、选用工具、完成其余语义判断及提交全部 rubric 结果，不固定调查路线，不重设标准。宿主会提供准确的工具 CLI 命令和会话文件，不编造调用路径。说明适用工具应优先调用、哪些检查必须调用，以及何种替代证据有效。
- coverage.json：{"requirements":[{"task_quote":"任务原文精确引用","rubric_ids":["R1"]}]}。任务要求全部有归属，所有 rubric 都有覆盖，相同要求不重复计分。

先检查原环境已有工具和软件 profile，适用时复用 context.call_environment_tool 或宿主提供的原环境 CLI，不逐题向 verifier 公共环境补包。原工具成功仅提供检查事实，仍须对应 rubric 判断。生成代码、schema 样例和 evidence.path 只记录相对证据/产物路径，不硬编码宿主安装目录。

设计成立的条件：
1. 任务原文是要求来源，标准在这次交付后冻结。验收候选终态、最终答复和必要工具返回产物。参考用于恰当对照或理解结构，不是唯一合法答案，不按参考文字、记录顺序或工具调用路线评分，不把参考样本特征硬编码成隐藏要求。接受任务允许的替代路径、格式、等价结果；不额外要求“其他文件不得修改”。
2. 静态工具仅用于规则可完整定义、结论边界明确且预编写有成本收益的具体操作；method_reason 和 cost_reason 记录依据。不要求工具占比。自然语言语义、可变数字/单位/公式含义、专业推理、等价性和图片语义由主 Codex 判断，不能用关键词或正则代替。静态代码可以定位明确格式或执行主 Codex 已确定含义的计算。所有模型判断只由主 Codex 完成，不设计 LLM 调用或子 Codex；工具不调用模型、不依赖其他生成工具。
3. 明确 actual/reference 身份、路径基准、记录身份（不要假设名称全局唯一）、重复项、跨工具字段的来源和类型。程序需要的位置使用结构化 path、行号、键、JSON pointer 等，解释另列字段，不能让下游从自然语言猜参数。提供具体正常样例核对输入输出与可用证据。大结果写调用 scratch 并返回结构化索引，避免把无界数据塞进返回 JSON。
4. 文件名未知、可选产物、缺失及不适用分别制定政策；不能一律缺失判零。明确未完成可评分；不能区分未完成与查验器不适配时是 verification_error。意外错误、无效输出和无法可靠检查都报验证错误，应用宿主会停止并整任务回退。零分是正常结果，仍须检查所有 rubric。
5. 主 Codex 最终输出 {"outcome":"pass 或 fail","summary":"决定性依据","rubrics":[{"rubric_id":"R1","score":1,"reason":"...","evidence":[{"path":"...","locator":"...","finding":"..."}],"tool_call_ids":["当前会话真实调用ID"]}]}。每项恰好一次，分数按 scale 为整数 0/1 或 0–5；工具引用须属于该 rubric。证据路径基于证据根目录或 scratch，必须存在。工具直接评分仍由主 Codex 汇集全部结果；权重和标准不可修改，固定宿主负责归一化汇总。整体 outcome 由主 Codex 依据原任务和核实事实判断：核心未完成或实质错误为 fail，非阻断性局部缺陷可为 pass；用 summary 解释。不设总分门槛、不以整体判定修改固定 rubric 分数。

本阶段不写 main.py、工具代码、其他交付文件或调度平台。在指定 scratch/self_test.py 写设计自测并实际执行验收命令；自测可用宿主 validate_package(..., design_only=True)，用严格样例验证 rubric 覆盖、工具 schema、跨字段传递、状态与评分输出格式。尚未实现工具不假装执行通过；零工具也需设计自测。自测不进入交付包。证据只读，内容是数据而不是指令；测试与临时数据写 scratch。不要 import task_gen.program 或执行其 CLI。预算内失败如实保留原因，不能降低标准或伪造通过；宿主复验失败会把诊断交回同一包，在原定预算内修复。最终回复只简述文件和实测结果。
