# 手写 verifier 试验

- `manual_npz_verifier.py`：S59 verifier。
- `test_manual_npz_verifier.py`：关键测试。
- `manual_s59/`：固定标准、模型配置、输入样本、真实运行结果及详细说明。
- `verifier_two_task_data_inventory.json`：S59/S66 数据盘点。

在仓库根目录运行：

```bash
python -m pytest task_gen/test_verifier/test_manual_npz_verifier.py -q
python -m task_gen.test_verifier.manual_npz_verifier --help
```

历史模型调用日志保留原始内容。目录迁移只更新代码引用、说明和标准数组路径。原件保护范围仍待按讨论收窄至指定的18份数组。
# 最终修复与 20 任务重复应用

两种方法默认在生成尾部增加一个 Codex 会话，直接修复任务生成包、测试，再交付。
完整试跑和修复均计入生成成本。冻结后对同一份证据独立应用两次：

```bash
.venv/bin/python -m task_gen.test_verifier.final_review_experiment \
  --config task_gen/test_verifier/final_review_run_config.json \
  --output task_gen/test_verifier/runs/final_review_20x2_20261016 \
  --concurrency 4
```

默认使用 `runs/random20_20261015/task_ids.tsv` 的 20 任务、6.1sol/high、两种方法。
新增目录保留全部 API 请求计量、生成与最终修复、两次应用结果及 `RESULTS.html`。
同目录重启会复用已完成结果并核对冻结包；未完成的应用留档报错，不静默覆盖。
费用无供应方确认价格时显示 unknown，原始 API usage 和返回的费用字段保留。

本轮报告：[全部指标](runs/final_review_20x2_20261016/RESULTS.html)、[问题核实](runs/final_review_20x2_20261016/ISSUES.html)。问题页可展开任务原文和被验证回答；完整依据与基础设施恢复记录见同目录 `ANALYSIS.md`。

已完成两种方法各 20 任务：Program 生成成功 19/20，工具版 18/20，37 份冻结包各应用两次，共 74 次。原生逐项重复一致分别为 16/17、12/14；回退单列。155 项宿主测试通过；最终哈希与计量核对见报告目录 `audit.json`。
