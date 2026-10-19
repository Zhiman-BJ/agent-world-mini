# direct_codex_rubric

独立从原任务生成固定 rubric，使用原始 task_eval_verifier 调查流程检查；不复用其他方法生成的评分标准，不预生成静态工具。

- `generation.py`：独立生成入口。生成后由新的 Codex 会话复查、修改并实测，宿主复验成功后才冻结；公共实现只共享代码。
- `prompts/application_contract.md`：本方法的输出契约；共同设计要求见 `../codex_rubric_verifier/prompts/design.md`。
- `application.py`：应用固定标准；意外无法判断时整任务回退原始 Codex。
- `run_config.json`：6.1sol/high，生成1800秒、每次应用1800秒，记录 usage。
- `runs/`：本方法新生成包、冻结哈希、两次检查、API计量和报告。

```bash
.venv/bin/python -m task_gen.test_verifier.direct_codex_rubric \
  --config task_gen/test_verifier/direct_codex_rubric/run_config.json \
  --output task_gen/test_verifier/direct_codex_rubric/runs/independent20_20261019_gpt61sol_high \
  --concurrency 4
```

生成包：`runs/<batch>/direct_codex_rubric/Pxx/verifier/`，包含 `rubrics.json`、`instructions.md`、`coverage.json`、空的 `tools.json`（仅公共接口兼容，不含工具）。rubric冻结后各检查两次；重启只续跑当前方法自身已完成记录，不跨方法选择包。

报告为每批 `RESULTS.html` / `summary.json`。API金额未知时标记 unknown，保留输入、缓存、输出token和耗时；不根据参考得分宣称错误识别准确率。
