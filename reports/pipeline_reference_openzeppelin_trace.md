# OpenZeppelin 参考交付失败：从目标、执行到转写与验收的证据链

核查日期：2026-09-14。当前主目录 HEAD 为 `cf2b592`。只读核查原始任务、LLM 日志、工具日志和最终文件；只新增本报告，不修改代码、任务数据或原有人工判定。

路径约定：`R = runs/openzeppelin_sol_full/20260913_090540_320853_smithery_openzeppelin_25_gpt-5.6-sol`；`F(n) = R/tasks/task<n>/final/filesystem_scopes/contract_projects/openzeppelin_contracts_v5`。下文 `llm_calls.jsonl:L` 为 R 下原始日志的物理行号；环境业务调用序号使用 `agent_result.json.execution.tool_calls`，不把 `review_select_plan` 算进去。原始 raw/tool_calls.jsonl 的序号可能不同。

## 结论与不重复计数口径

旧人工报告的四个 FAIL 不能简单全部归为“Step 3 执行器写错”。task10 的错误首次出现在 Step 4 的参考回答生成；task1/11/14 的文件缺口在 Step 3 已形成，后续又被文字表述和验收放过。task1/11 的旧验收还混入了需要收窄的标准：任务没有明说两种格式各自包含每一项分析，也没有明确要求 CSV 保存全部提案快照字段；这些不能当作无争议的独立失败证据。

| case | primary_stage | primary_category | 最早可证实缺陷 | downstream_misses | 判定边界 |
| --- | --- | --- | --- | --- | --- |
| task1 | Step 3 / 导出后完成声明 | 导出内容与交付声明不一致；持久化统计不足 | MD 702 bytes，仅汇总而无 67 条明细；执行答案却称两份文件“均包含 67 条投票证据及计票复核结果”；JSON 无描述统计/集中度 | Step 3 自报 completed；Step 4 延续两份均记录67条投票的说法；Step 5 全 true；后续 verifier 没有参考实跑校准 | “两份各自含全部分析”是偏强解释；不应以缺MD逐票明细本身直接判任务失败，但两份内容描述不准确是确证 |
| task10 | Step 4 / reference_answer | 源码事实归纳错误 | 新增“ERC1155 继承 IERC1155Receiver”，原执行答案没有此句，实际 Solidity 声明排除该接口 | Step 5 answer_matches_task=true；后续 verifier 没有参考实跑校准 | 源码声明比较是任务明确要求，错误无争议；不是工具执行造假 |
| task11 | Step 3 / CSV 导出及完成声明 | 产物只保存原始逐票表，没有审阅汇总/复核结果 | CSV 9列67行，无汇总、公布分数、容差或对账结论；Step 3 仍 completed=true | Step 4 反思将分析明确解释为CSV完成标准却认可成功导出，参考答案又把原始票记录字段与CSV描述混写；Step 5全true；无参考校准 | 按“CSV审阅产物呈现分析”解释为FAIL；“必须完整快照/策略字段”不是task_text明确要求，不另计失败；若允许分析在聊天呈现，须把该case标为口径争议 |
| task14 | Step 3 / Markdown 导出→归档 | 必需报告内容未落盘 | 只生成966-byte模板及源码路径/hash后归档，投票分析和源码审阅留在聊天 | Step 3 自报完成；Step 4 更明确报告内容但未指出缺口，长回答替代了文件；Step 5 全true；verifier准备失败且没有参考验收 | 明确要求Markdown报告包含分析、源码范围、限制等，FAIL无争议 |

统计时每题只计一个 primary_stage/category；Step 3 自报、Step 4 放大或失真、Step 5 放行和 verifier 无参考校准是传播链，不能重复算成四个新的失败样本。task1/11/14 共享一个格式模板能力缺口，不代表三个独立工具实现根因。task10 是独立的事实归纳根因。

## 实际运行路径：不是 frozen review/replay

主目录当前源码：

- `task_gen/tool_graph/pipeline.py:107` 调用 `execute_chains`，108/109 依次调用 compose/validate。
- `step_3_chain_execute.py:59` 将执行委派给 `execution_agent.execute_candidates`；旧 `execute_frozen_chains` 在63行定义，但不是这里的入口。
- `execution_agent.py:68` 每个候选创建 initial/final 副本；86行将 review-plan-selection 文本和执行提示合并；94行 `_ReviewClient.run` 在真实环境里边观察边执行。这里没有“先独立review、再冻结重演”两个业务阶段。
- `review_agent.py:16` 的 `_ReviewClient` 是运行客户端；21行 `_llm_arguments` 禁用 shell/unified_exec，只挂环境MCP。`review_with_initial_state`（34行）虽然仍存在，但不能仅凭文件名就认为本次 task1/10/11/14 走了该独立初态review函数。
- `execution_agent.py:123` 的程序成功条件是 `failure is None and payload.completed is True and bool(calls)`。没有产物内容验收；128行保存 execution 的调用/状态/答案，127行的 llm_review.reason 实际来自同一执行agent最终自述，而非独立审计。
- Step 3 提示（`execution_agent.py:17`）已经要求“提交前对照最终目标逐项核实实际结果和完成证据”“只有完整交付…才能返回completed=true”。因此不是完全没有质量要求，而是依赖自检的提示未转化成真实的产物证明。
- `step_4_task_compose.py:66` 启动任务文本、表达反思、参考回答三轮。`_build_prompt`（152行）中参考回答实际输入只有 task_text、tool_calls、review_guidance；虽然上下文字典存了 execution_answer，参考回答提示没有传入它。不能把task10说成机械复制执行答案。
- `step_5_task_validate.py:146` `_build_review_prompt` 输入工具契约、review_guidance、任务、参考答案和真实调用；没有读取 final 文件字节。模块说明明确写着没有 workspace replay 或 byte-level state audit。提示已写“成功响应本身不证明业务目标完成”，但单次LLM仍全判通过。

原始 Step 3 LLM 日志分别为 task1 L66（`5ec426c212084b49ad9d100a35408523`）、task10 L70（`9571c90a04b54d6a9964725ea9de76af`）、task11 L62（`f4089a6e01694d6c995474d29e36b419`）、task14 L61（`cf42832619724afa8dd9b31262c7fc84`）。逐项解析 answer JSON，均与各自 `agent_result.json.execution.answer` 一致；提示包含 Step 2 objective、原链和设计依据，后续 bundle 的新objective则来自执行agent返回。

## 共用导出工具：实际落盘与摘要返回的边界

本次 bundle 中 `export_governance_review_artifact.description` 宣称生成 Markdown/JSON/CSV 审阅产物，“包含提案快照、投票明细、统计和重算计票”。输入只有提案、格式、路径、容差、源码引用等，没有可注入完整分析正文的参数。

R/run.json 指向环境 `/tmp/data0911_current/agentworld-toolgen-data0910-20260911/environments/general/smithery_openzeppelin_25`。读取其 `tools.json` 对应 `internal.code`，代码文本内第79行只计算 vote_count、unique_voter_count、voting_power_total、reason_count；85行构造完整对象；86–87行JSON序列化对象；88–94行CSV只输出9列逐票表；95–102行MD仅输出标题、ID、状态、票数、重算总额、容差布尔值、选项对账表及源码引用。这里引用的是抽出的内嵌代码行号，不是tools.json的物理行号。当前代码与本次产物内容逐项吻合，产物事实本身无需依赖源码版本推定。

工具成功返回只有 size_bytes、sha256、vote_count、source_appendix_count、tally_within_tolerance 等摘要，没有把“文件实际包含哪些章节/字段”作为证据返回。`vote_count=67` 是输入投票集合大小，不能推出MD保存了67条明细；`source_appendix_count=2` 也只证明两个引用，不等于完成源码审阅正文。hash能够证明字节身份，不能证明语义完整。

`browse_contract_project_files` 支持 read/list/search，说明执行阶段并非完全没有读取产物的工具。四条被查轨迹都未在导出之后回读对应生成文件内容。这里确认的是未回读，不主张必须使用特定工具才算完成。

## task1：导出自报与真实文件混淆，后续沿用

Step 2 的目标已经要求统计参与、分布、集中度，并“最终生成包含提案快照、投票证据和复核结论的审阅产物”。所以治理审阅和持久化交付不是Step4凭空新增。Step3微调后的目标仍要求集中度统计及持久化产物，输出格式具体化为MD+JSON。

实际业务调用22第一次导出因源码路径失败，23成功导出MD，24成功导出JSON（在原始含方案抽样的日志中分别为27/28/29）。之后继续查询高票权、理由和上下文，没有回读导出文件。

实际重读/重算：

- `F(1)/reviews/ens-marketplace-review.md` 702 bytes，SHA-256 `e525fbd1e65f6221427cc8c29788ef4831793392cf68463905cba67aac638974`。内容是上面的简表模板，无67条明细、unique voter、描述统计、集中度。
- 对应 JSON 35587 bytes，SHA-256 `4f77bfd0972174709033313f2e75854c159dca50365f27c6704fbf4dc0a8cc49`。顶层 proposal/reconciliation/source_appendices/source_appendix_notice/statistics/votes；statistics恰为 reason_count=6、unique_voter_count=67、vote_count=67、voting_power_total=1414622.1639861548，votes有完整明细，但无描述统计/集中度汇总。

Step3 execution.answer 已说两份“均包含67条投票证据及计票复核结果”；Step4 L94（call `5da762adf4434335ae4295bc0b5c6da3`）生成“**两份产物均记录67条投票**”，并宣布统计、集中度及产物已完成。原始工具摘要的投票集合数量被当作文件内容证据。

Step4任务草稿 L73 规定MD+JSON；L83反思认为各项要求均有调用支持且 need_revision=false。格式具体化有真实执行依据，并未新增一种未执行格式；真正的问题是未区分完成分析、导出摘要和产物覆盖。

Step5 L103（`75ea987ff73b4b8b862a482c717514a9`）原始answer：`{"execution_matches_task":true,"answer_matches_task":true,"task_is_usable":true,"errors":[]}`。没有reason字段，也没有自由文本解释，不应编造“Step5理由是hash通过”。能证实的只是全部布尔通过，且输入没有MD/JSON正文。

口径修正：最终task_text写“同时呈现…最后生成…MD和JSON审阅产物”，自然解释支持持久化分析，但没有明确两份各自逐项重复完整分析。旧报告直接要求MD保存全部逐票记录也比字面更强。“MD只有67票计数，所以‘记录67条投票’必然是撒谎”也需考虑这句话可指汇总；应准确写成“执行答案明细覆盖措辞过强、文件与分析覆盖不足”，而非捏造任务要求。即使采用较宽验收，JSON/MD能力缺口和未做内容自检仍为确证，整体FAIL应标注所用产物口径。

## task10：Step4参考回答首次引入错误继承关系

Step2目标已有“可见接口表面和版本/导入/声明差异记录”；Step3定稿objective明确要求独立OpenZeppelin静态记录，声明比较不属于事后扩大。

执行第14次 review_token_contract_surfaces、15/16次 compare_contract_sources读取ERC20/721/1155表面及差异；19次review_governor_source等读取Governor。第16次工具返回 `right.imports` 确含 `./IERC1155Receiver.sol`；`right.declarations` 只有kind/name/line，不含完整继承列表；max_diff_lines=100的返回没有 `abstract contract ERC1155 is ...` 这一实际声明。遍查本任务所有工具结果，也没有ERC1155完整继承行。因此更准确的描述是：工具给出了import，执行环境真实源码可读取，但本轨迹给Step4的结构化摘要不足以支持其新增继承细节。

Step3 execution.answer 只说“Governor、ERC-20、ERC-721、ERC-1155的导入和声明差异已比较”，没有错误继承列表。

Step4任务草稿L80/反思L87保留声明比较。L96（`64281704ccdf4c3c8e5ff057c31b7385`）参考回答首次写：

> ERC-1155（ERC1155.sol）继承 Context、ERC165、IERC1155、IERC1155Receiver、IERC1155MetadataURI、IERC1155Errors

独立读取 `F(10)/contracts/token/ERC1155/ERC1155.sol:19`：

```solidity
abstract contract ERC1155 is Context, ERC165, IERC1155, IERC1155MetadataURI, IERC1155Errors {
```

第7行只是import IERC1155Receiver。把import升级成继承是实质性错误；Governor的确实现接收者接口，不能迁移到ERC1155。不存在“库版本可能不同”的免责空间，因为被审阅的是本次文件。

Step5 L111（`2eb86483446240558afe203a91f2c625`）原始输出三个true、errors=[]，没有reason。它接收了相同源码摘要和新增错误句，却未要求补证或指出unsupported assertion。

本题额外导出MD/JSON较短不是主失败原因：最终任务不要求这些附加文件承载完整记录。故其 primary_stage 必须为Step4.reference_answer，不得把task1的文件问题重复强加到task10。

## task11：逐票CSV与分析交付的范围混淆

Step2要求“生成一份可复核的审阅产物：准确呈现提案及其投票明细，统计参与和选项分布，复核聚合计票…”。Step3通过方案选择落到“逐票明细CSV”范围，定稿objective仍有参与、选项分布和计票复核。因此统计和复核已有前置目标；具体CSV格式来自执行期选择，不是Step4额外发明。

实际业务21次调用，最后一次导出（原始含方案尝试日志第29条）返回success=true、vote_count=67、17506 bytes、tally_within_tolerance=true。独立用csv.DictReader读取 `F(11)/review/ens-marketplace.csv`：67行，表头恰为：

```text
proposal_id,title,vote_id,voter,created,choice,choice_text,voting_power,reason
```

SHA-256 `f4b522740cccde8f04c2dd24c23f2c9e01d804b181eccd74eecadf981174f52f`。没有单独汇总/对账行或字段；没有reported scores、容差、一致性标记。CSV可重算选项分布，但单凭文件没有公布聚合分数，不能独立复核“与公布计票一致”。这比“少几个快照字段”更接近可复核审阅的核心缺口。

Step3的分析结果在execution.answer里基本具备，但把产物链接写到了 `tasks/task11/agent/review/ens-marketplace.csv`，实际文件在final/filesystem_scopes/...下。Step4修正为项目内路径，没有必要把这个已纠正链接单独计为最终失败。

Step4草稿L82、反思L85（`7e044318559e400a878c8c1c691461c7`）尤其关键：反思明确把“CSV…包含…参与情况、各选项数量与投票权分布，并复核计票”解释为完成标准，仍因为查询、分析与导出分别成功而称调用支持；只删除可选源码附录。参考回答L93（`c7b819a28d3446a489a0e439ea5010b7`）随后说逐票记录包含space_id、按策略分解投票权；原始治理记录确有这些，CSV确实没有。没有清晰区分“查到的原始票记录”和“交付CSV字段”，但不能断言该句语法上只可能是在描述CSV。

Step5 L107（`064fbcc760e54dc98d8fccf9b636210e`）三个true、errors=[]，无reason。

判定修正：最终task_text只说“提案信息”，不列空间、作者、快照区块、窗口、状态的完备字段集；ID+title至少是提案信息，因此旧报告以“没有完整提案快照”直接判FAIL过严。原始逐票明细也不必默认等于所有数据库字段。保留FAIL需要明确采用“CSV本体承载参与分布和复核”的交付解释；若认为后半句允许聊天呈现，现有证据不能把其判成与task14同强度的无争议失败。共享工具模板内容缺口与Step4字段表述不清仍成立。

## task14：文件内容缺口在Step3已形成，Step4又将结果写成要求

Step2 objective已要求把提案快照、投票明细、统计结果和明确范围的源码审阅引用，以指定格式生成、哈希归档；Step3 objective又写“治理报告及工程归档”。归档和持久化统计的主要求不是Step4第一次出现。

执行调用9得到描述统计、Top1/HHI、策略贡献、理由覆盖，13完成计票复核，15/16读取Governor/ERC20，19等进行源码表面审阅，22审计依赖。30导出报告，31打包。调用30只给导出器提案、路径、format=markdown、两条source_appendix_paths、容差，没有分析正文参数；调用31准确打包指定四文件，未替换报告正文。

独立读取 `F(14)/reports/ens-marketplace-review.md` 966 bytes，SHA-256 `8083f424f59f44a8cb56b33cb6051c48d71d5d7cfaf9e60a3dd809b3541f52c3`。完整正文仅含702-byte模板+两条源码路径/hash。无unique wallets、描述统计、集中度、策略贡献、理由覆盖、孤立/重复检查、Solidity版本、接口审阅、依赖及工具链限制。存在分选项对账表，不应误称完全没有计票复核。

ZIP 12706 bytes，SHA-256 `5e908d00512ee1c4049feaab5694bfd40b5b517f930417ecc85bf0ba90f86097`；清单970 bytes，SHA-256 `09cd0e2be3c4d2e5cddd58aa6ad8bf90948c897448630da0f916f8b55b99b180`。独立解压并逐成员比对，Governor.sol/ERC20.sol/package.json/报告四成员与final源文件逐字节一致；ZIP中确为短报告。问题是归档的报告不完整，不是ZIP未生成或打包错误。

Step3 execution.answer已列长篇治理/源码结果并声明归档报告完成，缺口没有被披露。Step4草稿L81把更多观察展开成要求，例如“67条”“明确报告无孤立投票或重复钱包”，其中“无异常”本是执行后结论，写成请求是结果泄漏；把统计明细、源码版本与限制明确放入报告也比Step3简短定稿objective更具体。L86反思删除额外背景，却保留这些。不能把最终每个细项全归咎Step3，但即便删去Step4新增细节，Step2所需统计持久化仍未完成。

Step4参考回答L99（`d0a92492946c4729ac31d39efdeaa2cf`）写成长报告，并声称“本报告确认…”治理分析和源码审阅完成，引用已生成的MD/ZIP，却没有指出文件与当前聊天不同。Step4仅产出文本，不会回写Step3 final；写再长的参考回答也不会补齐归档。

Step5 L108（`72ab6c8755ab438999158e9b9f5da2c2`）原始同样三个true、errors=[]，无reason。最终task_text对MD内容要求明确，此题FAIL不依赖task1/11的交付解释争议。

## verifier并未测参考：不是“测过参考且错误判PASS”

读取R/verifier_cache的5个文件，各 calibration 只有 `status=reviewed`、specification、evidence_plan，没有 reference_results。按缓存verifier与result.verifier对象相等核对：task1缓存前缀 `3dc8b7af`，task11 `b5f6a077`，task10 `3fc75a6a`；另两份对应task6/8。task14准备失败，没有成功缓存。

当前 `task_gen/task_eval_verifier.py:2723 prepare_verifier` 是四阶段构造路径：subtask_plan→evidence_plan→implementation→implementation_review；2740行明确不使用empty_evidence/initial_state/final_state/tools，2778行构造status=reviewed元数据。它不调用run_verifier在参考上试跑。旧 `_prepare_verifier_legacy` / calibrate_verifier 中存在参考验收，不是本次路径，不能拿旧函数的reference_results逻辑推断这次已测。

R/sol_verifier_qwen各result的verifier_attempts也只出现上述四阶段，与源码一致：task1 implementation曾因“必须返回source”重试，task11 subtask_plan因字段结构重试；这些是协议错误重试，不是参考验收失败后修正。

| task | 后续Qwen验收实际状态 | 对参考链的含义 |
| --- | --- | --- |
| task1 | outcome=indeterminate，`ValueError: verifier 返回了无效 evidence_refs`，verifier_tool_calls=[] | 非参考PASS；没有完成有效评估 |
| task10 | 同上 | 不能说verifier成功检测或放过ERC1155错误 |
| task11 | 同上 | 不能把calibration.reviewed当参考CSV合格 |
| task14 | VerifierPreparationError；第1次subtask_plan JSON解析失败，第2次“task clause必须被恰好覆盖一次：C1,C18”；Qwen未启动 | 尚未进入agent执行与验证，更没有参考验收 |

task1的冻结spec其实写了R9/R10要求MD/JSON本体完整承载所有审阅分析，evidence_plan甚至写“仅有导出调用或摘要不足以替代内容检查”。这说明生成规格可以意识到文件检查要求，但仅存在一个检查计划不等于实际执行该检查，且该规格的“双格式各自完整”也比task_text字面更强。不要用verifier生成的标准倒过来证明任务本来必然要求这么多。

## 对照task6/task8

task6最终请求“另行提供独立…审阅材料”，未指定将完整治理/源码报告写入文件；额外导出的966-byte MD不能自动触发task14的文件内容义务。旧报告据此通过是合理边界，不能因为工具模板相同就全判失败。本次对照确认task_text的不同，不把旧人工报告的全部数值复算当作本次新完成工作。

task8明确要求工程ZIP及逐文件路径/大小/SHA清单，静态分析可在回答呈现。本次独立读取 `F(8)/review/oz-contracts-v5.zip` 与integrity.json：六成员集合与清单一致，解压字节与final六文件逐一相同，清单记录路径/大小/hash，ZIP SHA为 `3c5183bbf2bed62ea917d962ece9c26e23eef9ed5e066da7eae05fded99cebbb`。没有“完整分析必须进归档报告”的要求。因此其交付与task14不是同一种承诺。

## 已证实根因与建议方向（不实现）

1. 格式能力与契约范围不齐：导出器说明把MD/JSON/CSV笼统说成含快照、明细、统计和重算，实际三个分支保留信息不同；模板不保存已查询的丰富分析。方向是明确格式级能力并让最终产物本身可证明所需内容，而不是把success/hash当完成。
2. 执行完成依赖自报：Step3已经有逐项自检提示，但完成状态没有独立证明，四条轨迹均未在导出后核对实际内容。方向是将最终交付要求绑定最终文件/记录内容证据；不需要为每种错误堆一个特判提示。
3. Step4新增事实没有可追溯依据：task10把import摘要提升成继承声明；task1/11把源数据/工具摘要和落盘内容混说。方向是参考回答保持证据粒度，不足则明确缺口；声明比较需要实际声明，文件覆盖需要实际字段/正文。
4. Step5证据只有轨迹摘要而没有最终产物：提示原则正确，但文件未观察时仍给了三个true，无法区分聊天报告和归档报告。方向是能观察必要的终态内容，并在证据不足时保留不确定/拒绝，不能只加“严格”措辞。
5. 后续verifier没有参考实跑校准：status=reviewed只是实现审阅，旧校准函数存在但不可达于当前prepare路径。方向是明确区分实现审阅、参考适用性验证、agent结果验收，不以缓存字段名造成已验收的错觉。
6. 审计标准也应受task_text约束：task1/11需区分合理交付解释与新增义务；“每格式各自全部分析”“所有快照/票记录字段不可缺”不是自动成立。跨参考/Qwen一致应用同一个标准是必要条件，但同样严格不代表标准就一定正确。
