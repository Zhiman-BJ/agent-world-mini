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
