# direct_codex_rubric

独立从原任务生成固定 rubric，使用原始 task_eval_verifier 调查流程检查；不复用其他方法生成的评分标准，不预生成静态工具。

正式下游入口仍为 `eval.verifier.verify_execution()`（兼容旧的
`task_gen.task_eval_verifier`），现默认接入本方法，保留原证据参数、返回字段和日志位置；
完整评分另存 `direct_result.json`。迁移、冻结包复用及纯 Codex 切换见
[测评说明](../../../docs/EVALUATION_ZH.md#新版-direct-verifier-与旧入口兼容)。
以下为独立实验入口；其中回退策略可配置，正式 API 出错则抛出 `VerificationError`。

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

生成及最终复查通过本方法的 `prompts/application_contract.md` 要求每个 rubric 和各层 `subcriteria` 标注 `required_for_pass`、`necessity_reason`、`pass_condition`、`veto_condition`，明确原任务依据和必要性的适用边界。必要性与部分得分分开，必要子项不能被父项或其他项高分补偿；开放式设计和报告不预设唯一答案，也不把每处质量改进都作为硬指标。

`contracts.validate_package()` 在设计自测、宿主复验及冻结前递归检查上述字段、非空 `goal`、全包唯一的 `id`、精确的任务原文引用和各层 `subcriteria` 数组（叶子用空数组）。字段缺失、类型错误或子项结构不合规时拒绝冻结；必要条件是否合理仍由模型复查。此检查仅约束新生成包，已有冻结包的应用入口保持兼容。

应用 prompt 同样加载该专属契约，要求逐层初核、对决定性事实回到原始证据复核，并在提交前核对必要项状态与整体判断。过程写入 `scratch/investigation.md`，输出 JSON 格式和宿主汇总逻辑保持现有接口，最终判定由主 Codex 作出。旧冻结包不自动补写必要性字段，也不会因本次提示词调整被修改；比较新旧效果时应分别记录标准版本和应用版本。

报告为每批 `RESULTS.html` / `summary.json`。API金额未知时标记 unknown，保留输入、缓存、输出token和耗时；不根据参考得分宣称错误识别准确率。
