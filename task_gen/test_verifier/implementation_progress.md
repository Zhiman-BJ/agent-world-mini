# Program verifier 实现进度

依据：program_verifier_prd.md；第二份 PRD 仅编写，不实现。

1. 生成与执行接口固定、宿主机制测试：30 项测试已通过；独立复核发现的路径隔离、签名及编译检查问题已修正。
2. 生成管线：实现完成；六案例首轮生成均已结束（案例并行 2，逐文件并行 2；S21 首次进程逐文件串行）。
3. 真实案例 + 异常回退闭环：完成。S21/S24/S91 真实回退均 pass；S81 program 完成 0.712。
4. 六案例真实运行、统计、结果报告：完成。4/6生成、1/6 program完成、0/6参考满分、3/4回退均pass；2个首轮误拒绝保留。
5. Codex + 任务专用静态工具 + rubric 新 PRD：已编写 codex_static_tools_verifier_prd.md，仅文档，未实现。

决策：在 feat/program-verifier-20261014 分支实现，所有新增文件保存在 task_gen/test_verifier；不修改原默认 verifier，不提交或推送。
接口：main.run(context)，context.sdk.execute(fn, context, inputs, rubric_id=None)，parallel([zero-argument callable])，call_llm/call_codex(prompt, call_file=..., function_name=..., rubric_id=...)，aggregate(results)。
辅助函数可返回路径/事实，不计分。模型 prompt 与 parser 单独同文件。所有依赖只由 main 编排。

配置：run_config.json；设计和实现都使用本机 gpt-6-astra/medium，普通 LLM 同模型 API；首版不新设弱模型默认值。批次 runs/program_v1_20261015。

首轮观察：S56 在阶段一被宿主对动态 import 的调用覆盖检查误拒绝。原记录保留，不自动重新生成；宿主现已允许显式模块表 + 动态导入 + SDK.execute 的编排，并由参考实跑核验实际全覆盖。

独立复核：宿主实现无阻塞项，30 tests passed；未把结构检查当执行正确性证明。S56/S71 首轮误拒绝原样保留，不自动重试。

最终核验：新增机制与原 verifier 回归测试合计 37 passed；六个 main.py 均与阶段一输出一致，全部证据副本与源文件内容一致，见 runs/program_v1_20261015/artifact_audit.json。结果、成本及失败原因见同目录 RESULTS.md、summary.json、DIAGNOSIS.md。

用户要求调整后：已移除生成阶段源码调用覆盖的阻断检查，覆盖由运行时真实执行记录及聚合器核对；新增测试后合计 39 passed。显式重试 S56/S71，沿用原设计和 main.py，另存 runs/checker_fix_20261015。S56 生成成功，因不完整证据路径回退 Codex 并 pass；S71 因模型生成 R5.py 缩进错误而生成失败。两例均已结束，首轮六例统计不改。详见该批次 RESULTS.md 和 DIAGNOSIS.md。

生成方式调整：根据用户最新要求，Designer 和实现 Codex 直接写私有工作目录、编写自测、运行并在同一次会话内修正；宿主独立复跑自测后接收文件。设计阶段冻结前测试 main 编排和数据传递，后续每次仅接收一个目标文件。43 项测试通过。S21 真实试运行保存在 runs/agentic_generation_20261015，结果待验收完成后更新。模型仍为 gpt-6-astra/medium。

最新实跑：按会话内写文件/自测/修正的新流程重跑 S21。Designer 首次自测发现 request 字段形态错误并在会话内修正；6 个 model_calls、1 个 helper、6 个 rubric 均由 Codex 写入并通过宿主复验。生成约 3008.9 秒；真实参考验收 program 完成、得分 1.0、无回退。详见 runs/agentic_generation_20261015/DIAGNOSIS.md 和 RESULTS.md。

用户改为全任务随机抽样：停止原定剩余五例及另选两静态任务的计划，新增 random_sample 批次入口；从 377 个环境、3770 个任务中按稳定排序后以种子 20261015 不放回抽 20 个，不预筛可用性、不补抽失败项。复用既有生成和应用接口，案例并行 4。新增抽样和准备失败保留测试，联合回归 45 passed。批次 runs/random20_20261015，实际结果以该目录 summary.json、RESULTS.md 为准。之前三个 remaining 启动批次在临时脚本导入阶段失败，未发生模型调用，不能当作模型生成质量统计；新入口从仓库根目录以 python -m 启动。

用户中途调整：随机20批次仅让已启动的 P01–P04 继续使用 gpt-6-astra 跑完；P05–P20 已预留目录并以 DEFERRED.json 标明暂停，不调用模型，等后续切换 gpt-6.1-sol。dispatch_control.json 保留边界；finish_started.py 仅等待并汇总四个已启动任务，不派发模型调用。旧派发进程之后可能出现预留目录 FileExistsError，是主动队列控制，不计入生成失败。未改变全局默认模型。

按用户要求，为当前 P01–P04 新增持续更新的 resource_usage.md / resource_usage.json：以文件系统 birth time 记录开始、完成记录 mtime 记录结束，保留单调时钟实测耗时；生成、应用和回退用量分列，缓存不重复相加，缺失保持 null/未知。独立观察进程每10秒刷新，不调用模型、不修改运行中的 verifier。用量格式归一及未知值处理2项测试通过。

2026-10-15 用户要求修复后重跑固定前10题：统一 gpt-6.1-sol/high，生成3600秒/次、program5400秒、普通LLM600秒/20000输出token。修复provider远端压缩协议不兼容（隔离配置改本地压缩）、自测通过后仍不交付（验收成功写提交标记并立即停止，宿主仍独立复验）、目录证据误拒绝（共享路径解析器+prompt契约）。生成单文件prompt不再重复嵌入整份设计。旧P03修复副本与原始模型返回重放测试保存在 runs/fixes_20261015/P03，原记录保留，目录可接受且不存在路径仍拒绝。新增固定manifest前缀读取，保留P01–P10而不重新抽样。机制与原verifier联合回归50 passed；正在做 gpt-6.1-sol 真实生成预检，预检成功后启动10题。

真实预检发现额外宿主误拒绝：Codex 显式 py_compile 产生 __pycache__/*.pyc，尽管源码仅修改目标文件，仍被快照判为越界修改。已添加失败复现，快照只忽略该类字节码（仍拒绝非目标源码和符号链接），真实生成文件及自测独立重放通过，见 runs/preflight_61sol_20261015/cache_fix_replay.json；联合回归51 passed。完整合成任务预检未跑完，原生成失败不改写。首次十题启动过早，发现该问题后中止其专属进程树，记录在 runs/random10_61sol_20261015/INTERRUPTED.json，不计入模型成功率。修复后新批次 runs/random10_61sol_20261015_r2 从头重跑固定P01–P10，全部6.1sol/high，并行4；时间用量由独立启动器每30秒保存。

2026-10-15 提示词函数接口修复：P01/P04/P05 的 main 通过 SDK.execute 调用设计中已声明的 model_calls.assess.build_prompt，公共运行器只匹配 rubric/helpers 导致 undeclared_function。已在公共接口加入已声明 model_calls 的 build_prompt，不计分、仍核对文件和函数名、禁止冒用 rubric_id；parse_output(raw) 仍直接调用。先复现失败，再修复；联合回归55 passed。三个原 verifier 不改动、不重新生成，在 runs/prompt_builder_fix_20261015 使用6.1sol/high重新验收；均已通过原先的拦截点，实际模型调用正在进行。P09/P10原材料权限问题不在本次接口修复范围。

提示词接口修复真实复验完成：P01/P04/P05全部program完成、无回退，得分分别0.785714/0.97/1.0，耗时分别约103/72/125秒。三个生成包哈希确认未变；原失败记录保留。分数未调节，不代表与参考完全一致；详见 runs/prompt_builder_fix_20261015/RESULTS.md 和各任务 result.json。

2026-10-16 用户要求固定P01–P20各独立生成+验收两遍，已设goal。新增repeat_sample.py复用run_case、独立子进程/目录与4并发；metering.py本地透传原API请求并逐请求捕获Responses SSE和Chat JSON原始usage，覆盖提前交付的生成调用，不记录认证头。预检真实Codex+LLM均6.1sol/high，2/2用量完整；基础联合回归60 passed，之后新增汇总保留失败尝试测试3 passed。20任务3057个源文件权限预查均可读。批次 runs/repeat20_61sol_high_20261016 已启动40槽位，PID4025987，每30秒生成RESULTS.md/results.csv/summary.json。API费用待服务实际字段或用户单价；不能伪造零费用。计量预检在 runs/repeat20_meter_preflight_20261016。
