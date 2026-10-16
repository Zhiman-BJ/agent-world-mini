# Codex + 静态工具 verifier

按 `../codex_static_tools_verifier_prd.md` 实现的独立实验入口，不改变默认 verifier。

[首轮四例实测结果](../codex_tools_first_run_results.md)：均生成并完成主流程，未回退；参考满分 1/4，实际静态工具调用 2 次。S91 证据权限失败后补跑 S56。

生成阶段：Codex 先固定 rubric/权重/评分条件，再设计工具；逐工具写文件、自测修正，宿主复跑。每个任务生成 `verifier/{rubrics.json,tools.json,instructions.md,coverage.json,tools/T*.py}`；没有固定调度 main.py。

工具的 `scratch/self_test.py` 必须定义 `check_real_evidence(context)`。宿主提供与应用阶段相同的真实上下文并单独执行：至少调用一次目标工具，输入符合 schema，所有调用均返回正常状态；合法 `missing/not_applicable` 可接受。普通错误输入测试不能替代这项检查，真实样本报错即生成失败。正常输入的语义依据仍由生成模型负责。

应用阶段：唯一主 Codex 按固定 rubric 调查证据，优先调用适用静态工具。工具可对完整 rubric 直接评分，也可只提供事实。宿主校验所有分数、证据路径和当前会话工具引用，再加权汇总；预料外错误立即停主流程，调用原有 `verify_execution` 整任务回退。回退不伪造 rubric 分数。

```bash
uv pip install --python .venv/bin/python -r task_gen/test_verifier/runtime_requirements.txt

python -m task_gen.test_verifier.codex_tool_verifier validate-reference \
  --config task_gen/test_verifier/codex_tool_verifier/run_config.json \
  --cases S21 S24 S91 S81 \
  --output task_gen/test_verifier/runs/codex_tools_v1_20261015

python -m task_gen.test_verifier.codex_tool_verifier generate \
  --config task_gen/test_verifier/codex_tool_verifier/run_config.json \
  --cases S21 --output task_gen/test_verifier/runs/new_generation

python -m task_gen.test_verifier.codex_tool_verifier apply \
  --config task_gen/test_verifier/codex_tool_verifier/run_config.json \
  --verifier /absolute/path/to/verifier \
  --evidence /absolute/path/to/source_evidence \
  --output /absolute/path/to/new_application
```

`--evidence` 使用已有证据准备格式：`program_request.json` 为候选/参考索引，`request.json` 和 `fallback_input.json` 保留原有回退兼容格式。工具 context 中的路径均已解析为绝对路径。自定义候选可复用 `program_verifier.data.prepare_evidence(actual_state=..., actual_answer=..., actual_calls=...)`。

工具注册在 `tools.json`，主 Codex 通过宿主注入的 CLI/socket 调用。宿主独立记录调用并校验 schema；工具无网络和模型凭据，只能写其调用 scratch。主 Codex 只能写应用 scratch，不能修改包、证据、标准或宿主日志。`required_tools` 仅表示无条件调用；条件适用/替代证据写在 `evidence_plan`。

CLI 返回 `call_id`、`tool_id`、`scratch_dir` 和 `result`。工具相对产物路径基于该次调用的 `scratch_dir`，不是主会话目录；最终证据使用证据根相对路径或 `scratch/<call_id>/<文件名>`。

消融入口：对同一份已冻结 verifier 和证据执行 `apply --without-static-tools`，或在配置中设置 `"static_tools_enabled": false`。不重新拆分 rubric、不重新生成工具；评分目标、权重、档位、标准和缺失政策保留。主 Codex 只读取 `output/rubric_only/` 中的评分视图，自行读文件、查数据库和写脚本；没有生成工具源码、注册表或可用的调用桥。原包不修改，主流程结果标记为 `codex_rubric_only`，错误仍整任务回退，回退成本单独记录。

`rubrics.json` 的所有说明字段、顶层共享定义和 `instructions.md` 原样保留，仅移除 rubric 的 `method`、`static_tools`、`required_tools` 路由字段，避免遗漏 `evidence_plan` 等字段里的容差或评分细节。消融提示词明确使生成工具调用要求失效。评分文字中的工具名称仅指待核查事实，不要求取得工具返回。不用字符串替换或模型重写评分标准。结果、用量、模型日志、独立检查脚本沿用原格式；对比应用成本时不重复计入已有包的生成成本。`runtime_paths` 仅用于额外解析库，不应挂载历史运行或生成工具目录。

```bash
python -m task_gen.test_verifier.codex_tool_verifier apply \
  --without-static-tools \
  --config task_gen/test_verifier/codex_tool_verifier/rubric_only_config.json \
  --verifier /absolute/path/to/frozen/verifier \
  --evidence /absolute/path/to/source_evidence \
  --output /absolute/path/to/new_rubric_only_application
```

默认配置：模型 `gpt-6-astra`，推理 `medium`；每次生成/应用 900s，静态工具 120s；同时跑两个案例，工具并发上限两个。凭据沿用本机 Codex 配置，不写入实验配置。

结果在批次 `RESULTS.md` / `summary.json`，原始日志分别在每个任务 `generation/`、`reference_validation/`；失败不自动重生成。相同批次再次执行只读取同模式已有完成记录（含回退结果）；发现模式冲突即报错，保留旧报告并要求使用新输出目录。旧记录缺少模式开关时按原有工具模式识别，`codex_rubric_only` 除外。中断记录保留，不当作成功。重跑用新目录。

参考样本暂视为正确。首批检查生成与执行成功率，不测错误答案准确率；未设美元单价，只记录 token/耗时。缓存 token 包含在 input_tokens 中，不重复相加。

```bash
python -m pytest -q task_gen/test_verifier/codex_tool_verifier/tests task_gen/test_verifier/program_verifier/tests
```
# Final Codex Repair

生成默认增加最终 Codex 会话，直接检查、修改整个任务生成包并运行真实主 Codex+工具验收。
详细边界见 `../verifier_final_review_prd.md`。最终验收失败不 fallback；正式应用保留原回退行为。
修复前文件在 `generation/before_final/`，交付哈希在 `generation/manifest.json`。
`final_review_timeout_seconds` 控制最终会话和宿主试跑预算；`final_review_enabled=false` 只用于基线比较。
