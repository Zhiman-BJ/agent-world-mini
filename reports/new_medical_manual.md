# Medical 最终人工核查

运行：`runs/medical_sol_full/20260913_204215_876768_smithery_sidneybissoli_medical_terminologies_mcp_10_gpt-5.6-sol`，下文路径均相对此目录。Qwen仅使用`qwen_trial_final`。

只按task_text验收。参考链也独立核查，不假定它正确；answered、execution.success或verifier结果均不是PASS依据。允许探索错误后恢复，允许按任务要求诚实披露本地缺档。未调用工具代原agent补做交付，所有数据库检查采用SQLite只读连接。

## 最终结论

| 任务 | Reference | Qwen | 决定性结论 |
|---|---|---|---|
| task2 | PASS | FAIL | 参考完成制剂、成分、NDC、类别、ATC、MeSH审查；Qwen上下文失败无交付（主代理已核实） |
| task3 | PASS | FAIL | Qwen把两条同目标MMS映射解释成BA00/BA00.Z落点歧义，核心映射审查错误 |
| task4 | PASS | FAIL | 参考完成E11子类、限制、面板、药物/NDC及迁移风险；Qwen上下文失败无交付（主代理已核实） |
| task8 | PASS | FAIL | 参考区分I10单一MMS目标与Foundation关系、全章覆盖统计；Qwen上下文失败无交付（主代理已核实） |
| task15 | FAIL | FAIL | 参考漏掉E110显示码E11.0的三条真实过渡记录；Qwen最终答案中途结束，缺分类和映射交付 |
| task17 | PASS | FAIL | 参考保留直接成员证据及phenformin分类查询冲突/待复核；Qwen上下文失败无交付（主代理已核实） |
| task19 | PASS | FAIL | 参考实际取齐202401的281条NDC，观察/答案/MeSH及版本边界齐全；Qwen上下文失败无交付（主代理已核实） |
| task22 | PASS | FAIL | Qwen将Foundation Subclass及MMS空关系提升成“唯一跨术语权威等价” |

Reference：7 PASS / 1 FAIL。Qwen：0 PASS / 8 FAIL；其中本报告独立复核的三份answered交付均FAIL，其他五份为主代理已核实的上下文失败，不伪称本报告重新逐条完成该五份失败日志核验。

## 证据层及状态

已读全部8个task_text、`tasks.json[].reference.answer`与`reference.tool_calls`、各`tasks/taskN/agent_result.json.execution.answer/tool_calls`、各final数据库；参考实际调用数量依次为30、31、28、31、26、24、30、25。reference.answer是本次交付验收文本，execution.answer用于检查其早期摘要和证据是否支持最终参考答案；两版措辞不同，不强求相同。若最终参考答案补齐了execution摘要未展开但真实工具取得的资料，不把摘要简略单独算FAIL。

八任务initial及final的records.sqlite SHA-256前12位一致，均为`0ade17121f11`。这些任务要求只读术语资料/报告，不要求修改术语数据库；状态无变更不影响PASS。Qwen证据为`qwen_trial_final/taskN/result.json.answer`及`state.agent/tool_calls.jsonl`，下文Q序号按日志行从1开始，R序号按execution.tool_calls从1开始。

版本原表`terminology_release`只保存ICD-11 MMS 2025-01、ICD-10→ICD-11 mapping 2025-01、CID-10 V2008、RxNorm 03-Aug-2026/API3.1.355。MeSH/LOINC/ATC/NDC独立版本不在表内。LForms29.0.0与MeSH记录更新日期都不能当相应术语发布版本。

## task2 — Reference PASS

任务要求制剂核验、成分/NDC/类别/ATC、具备标签/范围/树位的MeSH候选审查，并提供来源版本、歧义缺口和非等价/非临床边界。

- R7、R12、R15取得并核验860975，显示`24 HR metformin hydrochloride 500 MG Extended Release Oral Tablet`、TTY=SCD、suppress=N，匹配500mg/24HR/缓释口服片；参考区别品牌860977。
- R9直接subject查询为空，R12聚合成分6809 IN、235743 PIN；最终答案没有掩盖原始成分关系subject为空。只读`rxnorm_relation`确认两条IN/PIN的subject确为空字符串，故答案将其限定为本地聚合支持而非完整定向关系链，正确。
- R10、R14核实401条NDC，日期200910–202608；R26验证72162242409，R27筛202601返回395。最终给代码/日期示例、总体范围和NDC独立版本缺失，不宣称市场可用性。
- R8直接制剂类别为ATCPROD A10BA及VA HS502；R3、R29、R30支持6809成分级A10BA02和其他关系。答案区分制剂A10BA、成分A10BA02、复方A10BD，不将分类当等价。
- R16–18相关MeSH搜索0、19描述符窄样本；R22–25核验唯一完整D006973的标签、范围及树位置确是Hypertension，最终明确不作为metformin标引候选。没有拿不相关完整记录顶替目标。
- R13版本与最终来源文件、缺档、局部索引差异及非临床限制齐全。因此是按本地范围完成候选审查，并非要求补齐全世界MeSH后才可PASS。

## task3 — Reference PASS；Qwen FAIL

Reference完整覆盖三部分。R4、7、8两条I10 MMS映射均到BA00.Z；R9、11、13核验CID I10及目标，最终正确区分“两个文件中的同目标重复记录”和多个不同目标。R12的pdfinfo缺失如实列为未核对打印版，不影响已有结构化核查。

R14、16、18、19核实D006973、M0010859、T020937、C14.907.489，35个允许副主题中只Q000503有详情；最终逐列其余34个URI及人工复核、MeSH版本缺失、标引排除语义。R21及R22–28取得7个面板成员并逐项查观察；7项均不在本地6观察样本内。R29–31确认单位与面板关联，特别是13457-7单位名称mg/dL但code/system空；4条受控答案属于44250-9，不能移植到血脂面板。最终明确所有7项观察属性和答案关系缺口、LForms不等于LOINC版本。因此reference满足发布前审查、未解析项目转人工的要求。

Qwen确实取得标签、概念、35个副主题、7项面板、6观察和另一问题的4条答案，相关主体没有伪造来源。但核心映射解释错误，不能只把它解释为“查询BA00节点和映射BA00.Z节点不同”。

`qwen_trial_final/task3/result.json.answer`原文：

> 同一 ICD-10 类别既落入“一对一”又落入“一对多”映射文件，落点（父类 BA00 vs 残余项 BA00.Z）构成歧义

并要求：

> MMS 落点应取 BA00（父类）还是 BA00.Z（unspecified 残余）。

其Q2实际返回：

| mapping_id | target_basis | icd11_code | icd11_title | relationship_type |
|---|---|---|---|---|
| 10To11MapToMultipleCategories.txt:4982 | mms | BA00.Z | Essential hypertension, unspecified | null |
| 10To11MapToOneCategory.txt:3851 | mms | BA00.Z | Essential hypertension, unspecified | null |
| foundation_10To11MapToOneCategory.txt:3851 | foundation | null | Essential hypertension | Equivalent |

两条MMS线性化URI也相同，均结尾`/761947693/unspecified`。BA00来自Q1/Q15的一般概念检索，确实是存在的父节点，但**不是此两条MMS过渡行的另一个目标**；Foundation URI关联也不是第二个MMS代码。Qwen错误把文件名不同及目标基础不同拼成BA00/BA00.Z映射落点冲突，还在最终人工复核清单第1项重复。这是任务明确要求的“识别ICD映射歧义”被实质误判，归因为agent证据解释/答案错误，非来源伪造、非工具失败。单独查询BA00本身不构成失败，错误在声称原映射给了上述歧义。

## task4 — Reference PASS

R5–8核实DataSUS E11→E110–E119十子类、第四章E10–E14分组；`classif`在E112/E113/E114为+，性别/死因/参照/排除字段未填充。最终逐列显示码、名称、层级及限制空值，未把空值解释成没有规则。

R9、10、19、27给24331-1、七项REAL成员、5个mg/dL及2个ratio、13457-7单位编码空、item限制/答案空。R16通用代码核验不识别panel记录，但专用面板记录真实存在，最终正确区分两种记录集，未把未命中当无效。

R11、12、17给860975显示/TTY/suppress、成分聚合及直接ATC/VA类别；R18、20、28确认401 NDC、起止200910–202608和71610061394的202202–202608。R14、22–25核验E11到5A11两条同目标及5A11分类路径；R21源IV章421码、69一对多、1无目标，最终用于背景风险而非子类映射结论。任务要求分析E11迁移风险，并没有要求给十子类全部完成的目标映射；最终明确尚未逐子类核验、并发症可能丢失/需要额外编码，满足风险审查。

R1版本、R26打印失败和词面候选/本地未命中状态均清楚披露，任务要求各域均有实证交付。

## task8 — Reference PASS

R10、11、15得到860975、500MG/24HR/缓释口服片、6809与235743成分及ATC/VA分类；最终明确ATC制剂类A10BA与成分成员A10BA02、非临床适应证推论。R14、24支持401总包装、202501日期覆盖345及NDC示例；来源版本R20明确RxNorm/CID/ICD版本和其他版本缺失。

R7、21、24、27核实DataSUS V2008 I10及历史分类；R3、8、9、30取得高血压MeSH词汇候选和完整D006973定义，其他副主题/人口学范围不自行补造。R13、17、25、31直接确认I10 MMS两行同目标BA00.Z、Foundation Equivalent/URI，不把Foundation的空MMS代码当未覆盖，不声称I10有两个不同MMS目标。R18第IX章464源码365单目标/99多目标（21.34%）有据；execution.answer特别说明该分母仅含过渡表已出现源码，不能宣称DataSUS全目录覆盖。最终没有凭统计直接断言所有历史CID已迁移。

最终解释BA00.Z未特指及原始血压表型不足导致的歧义，保留不同术语定义/临床应用不等价，版本范围和PDF失败均披露。早期chapter_num=IX失败后改9成功，不扣分。

## task15 — Reference FAIL；Qwen FAIL

Reference对六代码本地5命中/1未命中、名称、已知版本、E11/E110/5A11层级，以及E11三来源候选均有真实R5、7、13–16、21–24支持。但任务明确要求同时给DataSUS **E110** 的WHO候选，参考只做R25 `icd10_code="E110"`，返回0便停止。

`reference.answer`原文：

> 对 E110，精确查询返回 0 条，本地 WHO 2025-01 过渡资料中未找到候选。

`execution.answer`还称“E110 没有独立 WHO 过渡行”。只读最终库反证：`cid10_subcategory`明确`code=E110, display=E11.0, category_code=E11`，因此这里不是臆测格式转换。WHO `icd11_mapping`实际保存：

| mapping_id | icd10_code/title | target_basis | 目标 | 关系 |
|---|---|---|---|---|
| 10To11MapToMultipleCategories.txt:2579 | E11.0 / Type 2 diabetes mellitus with coma | mms | 5A11 | null |
| 10To11MapToOneCategory.txt:2104 | E11.0 / Type 2 diabetes mellitus with coma | mms | 5A11 | null |
| foundation_10To11MapToOneCategory.txt:2104 | E11.0 / Type 2 diabetes mellitus with coma | foundation | entity/119724091，MMS码null | Subclass，precedence=0 |

工具`query_icd_revision_mappings`把icd10_code原样作为等值filter，不会去点或补点。因此原始字符串E110未命中成立，推导“该DataSUS对象无候选”不成立。正常查询E11.0就可得到三条，Qwen本题Q10也实际取得它们。Reference遗漏任务要求的E110来源候选并误报缺档，归因为agent未使用本地显示码关联，不是数据库没有资料。仍不可由这些候选推导自动重编码，但这不影响应报告候选的义务。

Qwen `result.json`虽status=answered，answer只有682字符，只输出六代码状态/名称/版本表和一段未完注释，末尾是：

> 2339-0 在完整 LOINC 中是广泛使用的

没有E11/E110/5A11分类位置，也没有任何WHO2025-01候选、来源范围或映射限制交付。Q7–10已取得映射/层级及E11.0候选，却没有进入最终答复。判FAIL因最终交付截断/缺项，不能从日志代其补完后算PASS。不是把answered当完整，也未把不完整片段臆断成已经形成的医学错误结论。

## task17 — Reference PASS

R6成员表及只读`atc_membership`明确仅有A10BA/6809/metformin/A10BA02/DIRECT与A10BA/8129/phenformin/A10BA01/DIRECT。R7–10核验两规范RxCUI和profile，R14核验类别、药物ATC码及RxCUI均命中。

R11、15支持6809的ATC分类和18类关系；R12、16对8129分类查询未找到/空，但R6与R10仍有DIRECT成员证据。最终参考明确将8129保留成员证据、标待复核并说明不同来源覆盖冲突；对6809可纳入，没把空NDC/成分资料解释成全球不存在。R13和R2限定本地A10BD空、不扩展复方候选；R24反例未命中亦限定本地。最终给规范码、药物级码、类别、成员、代码核验、可用资料和待复核原因，覆盖全部要求。

## task19 — Reference PASS

R13–19定义药物860975及6809/235743、ATC A10BA/A10BA02、VA HS502，精确对应500mg24HR缓释片，不混入复方/其他剂型。

R20、21、24实际对202401分页100/100/81，281条不同NDC且末页has_more=false。SQLite只读`rxcui='860975' AND start_date<='202401' AND end_date>='202401'`计数与distinct ndc均281，证明不是从401全量臆算。最终明确2024-01日期规则与包装范围，RxNorm2026快照支持历史字段筛选但不是2024当期术语版，市场行为不可推断。

R4、5、8、9核实LOINC44250-9“两周兴趣/愉悦缺失”和LA6568-5/LA6569-3/LA6570-1/LA6571-9分值0/1/2/3，R6、7披露观察属性/面板关联缺档，没有当抑郁诊断。R25–29核实MeSH D006973首选概念、树位、范围、允许副主题覆盖，明确用于文献标引而非患者诊断。R30版本及最终各术语角色/无权威等价边界完整。因此时间、暴露、观察、合并症、分类包装和版本要求全覆盖。

## task22 — Reference PASS；Qwen FAIL

Reference R11、13、19核验5A11名称、URI、05章分组；R5、20打印核对失败正确列缺口。R9、15、16给metformin成分候选及具体860975临床制剂，最终明确该制剂/401NDC不是所有metformin单药、剂型、复方的暴露全集。R18与24日期筛选分别345/401，最终给202501与202608条件、样例、起止日期、无独立NDC版本及外部核对缺失，范围明确。

R14、17、23、25支持A10BA/A10BA02及复方A10BD，明确空成员统计和其他来源分类差异。R8、12、22证明糖尿病MeSH本地未命中、仅19高血压相关描述符窄样本，最终不冒用MEDRT D003924/D008687关联为MeSH直接记录，亦不宣称全球无此描述符。R21版本、所有域本地核验与缺口齐全，符合task_text允许无法完成来源核验时如实标注的要求。

Qwen也交付了大部分代码与来源，但严重违反任务明确的“区分分类/关联和权威等价”要求。其最终A3将三行WHO E11过渡列入“③权威概念等价关系”，原文：

> 这是资料包中唯一明确标注为**权威等价关系**的跨术语连接

末尾D又称：

> 仅 ICD-10 E11 ⇔ ICD-11 5A11（WHO 过渡表 3 行，含 Foundation Subclass 关系）。这是唯一的跨术语权威等价。

实际Q10以及同库`icd11_mapping`中，两MMS行目标5A11但relationship_type=null；Foundation行`foundation_10To11MapToOneCategory.txt:2103`为**Subclass、precedence=-1、icd11_code=null**。来源权威性不意味着其中所有关系都是等价；Subclass不能升级为Equivalent，更不能直接用双向⇔表达。Qwen虽然保留了原Subclass字段并说“不等同临床判定”，仍在核心关系分类反复给出错误权威等价结论。归因为agent语义解释/答案错误，不是工具缺档或打印核对失败。

## 可复用的验收结论

三个answered Qwen的失败类型不同：task3是映射歧义误判，task15是最终文本截断缺项，task22是分类关系被提升成等价。参考task15同样不能凭精确查询零结果通过：本地已给显示码E11.0，正常工具可读到对应候选。这些判断均落在task_text明确要求，不以未调用指定工具、不联网、PDF工具失败或只读状态无修改判失败。
