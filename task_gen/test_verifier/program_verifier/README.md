# Program verifier（实验版）

依据 `../program_verifier_prd.md`，不改变原默认 verifier。第一步 Codex 在工作目录写 rubric 设计、函数契约、模型 prompt 和 main，运行接口自测并自行修正后交付；第二步逐文件实现和自测；候选证据只读，错误整任务回退现有 Codex。main 也可调用不计分的辅助函数。

从仓库根目录执行：

额外格式解析库固定在 `../runtime_requirements.txt`，需安装到生成自测与应用共同使用的 Python 环境，例如 `uv pip install --python .venv/bin/python -r task_gen/test_verifier/runtime_requirements.txt`。仅在自测 scratch 安装依赖不会随 verifier 交付；验收拒绝测试专用新增导入路径。

```bash
# 六个 PRD 案例：生成 + 真实参考验收 + 统计
python -m task_gen.test_verifier.program_verifier validate-reference \
  --config task_gen/test_verifier/program_verifier/run_config.json \
  --output task_gen/test_verifier/runs/program_v1_20261015

# 只生成单个案例，输出目录须是新目录
python -m task_gen.test_verifier.program_verifier generate --cases S21 \
  --config task_gen/test_verifier/program_verifier/run_config.json \
  --output task_gen/test_verifier/runs/new_generation

# 对已有 verifier 应用另一个证据包
python -m task_gen.test_verifier.program_verifier apply \
  --verifier <task_dir>/verifier --evidence <candidate_evidence_bundle> \
  --config task_gen/test_verifier/program_verifier/run_config.json \
  --output <new_application_dir>

# 宿主测试，不运行模型
python -m pytest task_gen/test_verifier/program_verifier/tests -q

# 全库均匀随机抽样：固定 20 个，失败不补抽；生成并验收各自参考终态
python -m task_gen.test_verifier.program_verifier.random_sample \
  --count 20 --seed 20261015 --workers 4 \
  --output task_gen/test_verifier/runs/random20_20261015

# 复用固定20题清单的前10题，保留 P01–P10，重新生成和验收
python -m task_gen.test_verifier.program_verifier.random_sample \
  --sampling-manifest task_gen/test_verifier/runs/random20_20261015/sampling.json \
  --count 10 --workers 4 \
  --output task_gen/test_verifier/runs/random10_61sol_20261015
```

随机批次的 sampling.json 在任何模型调用前保存母集数量、抽样种子、母集标识摘要、20 个任务的原文及身份。P01–P20 是本次抽样编号，和旧 S 编号无关。并行仅改变完成顺序，不改变样本；准备证据、生成或验收失败均保留原记录，不换题。各任务 row.json 和批次 summary.json、RESULTS.md 随完成进度写入。批次输出目录必须全新。

`run_config.json` 是本机本次实验配置；无内嵌凭据，换机器时调整 provider/model、凭据文件路径和预算。Codex 默认继承本机 provider 设置；调用日志保留 usage，缺失记 unknown。暂不换算美元成本。

当前实验模型统一为 `gpt-6.1-sol/high`（设计、实现、运行时 Codex、普通 LLM 和回退）。单次生成总预算3600秒，其中最多120秒留给宿主复验；运行时 Codex 单次3600秒、整 program 5400秒，普通 LLM 600秒/20000输出 token。Codex CLI 未提供独立输出 token 上限，使用时间与 reasoning_effort 控制其预算，不虚构 token 配额。

仅本实验的隔离 provider 配置设置 `capabilities.remote_compaction="unsupported"`，改用本地压缩，避开服务端不兼容的 v2 压缩返回；不修改用户全局配置。生成验收命令通过后写入 scratch/acceptance_ready.json，复用 CLI 的文件提交点机制立即结束会话，随后宿主独立复跑验收；标记本身不算通过。所有证据引用通过 `context.resolve_evidence_path(path)` 校验，可为文件或目录；具体任务产物需要文件时才限制文件类型。

证据包由 `data.prepare_evidence` 准备，含旧 verifier 的 `request.json`、program 的 `program_request.json`、整任务 fallback 的 `fallback_input.json` 及只读 evidence。`prepare_evidence` 允许显式传入 actual_state/answer/calls/execution；默认用已有参考运行作候选。完整 trace 优先读取 step2_task_solution.jsonl，以保留 PNG 等工具返回产物。

两个 JSON 索引因新旧接口不同而分开保留，任务文本相同，不混合 actual/reference。fallback_input 内含原始证据入口，调用现有 verify_execution，不带局部分数。

生成结构：`verifier/main.py`、`rubrics.json`、`rubrics/*.py`、可选 `helpers/*.py` 与 `model_calls/*.py`。主脚本只编排，评分函数独占文件。SDK 通过 context 注入；SDK 签名见 generation/pipeline.py 的 SDK_CONTRACT。模型文件包含 build_prompt(context,inputs) 与 parse_output(raw)。设计契约字段为 name、inputs、returns。

返回 JSON 对象的普通 LLM 调用可在 `model_calls` 条目显式声明 `response_format: "json_object"`；SDK 将此传给 API，并记录实际格式配置。旧设计或其他返回类型不自动开启；全局显式 `llm.response_format` 配置优先。该约束只保证 JSON 语法，字段及语义仍由生成的解析器与评分函数校验。

统计在批次目录 `summary.json` 与 `RESULTS.md`（首轮根因说明 `DIAGNOSIS.md`），逐项结果在任务目录 `reference_validation/result.json`，每次调用输入/输出/时间/token 保存于 generation 和 reference_validation/logs。生成会话内允许自行测试与修正；会话结束仍失败或真实参考验收非满分时原样留档，宿主不自动启动新修复会话。

重复 validate-reference 只读取已完成记录，未完成的中断记录保留为失败；需显式新目录才重新生成。不会静默重试业务流程。独立 apply 的 output 必须不存在。

生成/应用均必须有 bubblewrap；证据和宿主源码只读。生成 Codex 直接编辑其私有 scratch/verifier 并编写 scratch/self_test.py；宿主复跑验收时将交付文件只读挂载，通过后仅拷贝该阶段的目标文件。应用时生成包也只读，scratch/logs 可写。证据准备可用硬链接，任何计算写入需普通复制到 scratch。缺少隔离时拒绝执行，而不是退成未隔离的程序。

生成检查只核验文件、编译、契约和接口，不从源码推断调用覆盖。应用宿主核对每项 rubric 的真实执行记录与返回结果，漏执行或伪造结果均触发整任务回退。首轮失败记录保持原状；用户明确要求后的 S56/S71 重试另存 runs/checker_fix_20261015，复用原设计和 main.py，只补生成缺失实现并实际验收。

生成调用日志含 prompt.txt、response.txt、scratch/verifier（实际编辑文件）、scratch/self_test.py、acceptance.json 及验收 stdout/stderr；最终文字回复不是代码来源。自测须实际调用交付函数，main 自测使用契约替身，不能代替真实参考验收。模型调用文件优先实现，再生成 helpers 和 rubrics，使后续自测能读取已有依赖。
