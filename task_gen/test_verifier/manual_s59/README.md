# S59 手写 verifier 第一版

范围：只实现 `aiida_dft_provenance_recovery_program_000`（18 份 NPY→NPZ），不接入原评测流程。原始任务数据只读。代码在 `task_gen/test_verifier/manual_npz_verifier.py`，测试在 `task_gen/test_verifier/test_manual_npz_verifier.py`。

## 实现

1. `prepare` 是一次性可信准备：读取任务的明确路径清单，查询指定节点的仓储映射，校验来源哈希、非空、数值有限性；逐元素确认参考终态数组与原始数据完全一致。冻结18份参考 NPZ 及元数据，同时记录全部三个文件区域中2008份原始仓储文件的哈希。没有把参考回答当作字符串标准答案。
2. `verify` 对18份 NPZ 使用 NumPy 解包，严格比较数组值、dtype、shape。接受不同 ZIP 压缩方式和不同数组成员名。禁用 pickle；不执行候选代码。对18份 JSON 报告解析字段，核实原始路径、哈希、字节数、类型和形状；输出字节数与待测文件比较，不与参考文件字节数硬比。原始仓储文件与冻结哈希比较。共37个文件检查项。
3. 一次不带工具的 LLM API 调用检查最终回复。模型获得任务要求、代码核实的事实和待测回答，不访问磁盘，不做数组运算。每个数组一项，另有记录数配对、交付状态、归档用途三项，共21个语义检查项。接受等价表达，不使用正则或关键词匹配语义。检查模型输出覆盖全部编号，且引用确实出现在待测回答中。
4. 输出 pass/fail/verification_error 及逐项证据。未获得有效模型输出或遇到不支持的报告结构，不能整体通过。暂不把检查项数量转换为 RL 分数或权重。

## 复现

使用当前项目 Python（NumPy、PyYAML及已有LLM客户端依赖）。在仓库根目录运行：

```bash
python -m pytest task_gen/test_verifier/test_manual_npz_verifier.py -q
python -m task_gen.test_verifier.manual_npz_verifier verify \
  --standard task_gen/test_verifier/manual_s59/standard/standard.json \
  --actual /data1/agent_world/task_program/complete_10_task_environments_20261005/environments/semiconductor_scenario_01_03_03_0023/tasks/aiida_dft_provenance_recovery_program_000/final \
  --answer task_gen/test_verifier/manual_s59/reference_answer.txt \
  --output /tmp/s59-new-verification \
  --config task_gen/test_verifier/manual_s59/llm_config.yaml
```

output 必须是尚不存在的目录。配置复用项目既有 API 配置和外部凭据路径，未复制密钥。固定标准目前含本机绝对路径，移动目录后需重新 prepare。待测 agent 不应获得标准目录的写权限。

## 实际验证

- 7个测试通过：参考文件及等价的不同ZIP包装；缺失NPZ、数值错误、dtype错误、伪造报告、修改原件各一个；模型结果覆盖性和引用有效性检查。
- 两次真实 `gpt-5.6-sol` API 调用，reasoning_effort=medium。正常回答通过；错误回答只在原回答末尾增加与前文冲突的配对和归档主张，A19及A21失败。两次的文件检查全部通过。
- reference_run：端到端61.15秒，模型59.85秒，输入16806 / 输出4406 / 总计21212 token。
- contradiction_run：端到端39.67秒，模型38.04秒，输入16844 / 输出2941 / 总计19785 token。
- token数来自接口usage，不是费用账单。没有跑旧Codex verifier，因此不能量化与旧版的加速比。

详细结果见 `reference_run/verdict.json`、`contradiction_run/verdict.json`；各目录保留 `file_checks.json`、`answer_request.json`、`llm_call.json`。

## 本轮遇到的实际困难

1. **不能直接比较压缩文件哈希。** 合法ZIP编码和数组成员名可能不同，必须比较解包后的数组；报告中的输出文件大小也必须针对实际产物计算。
2. **JSON结构化不等于字段名已被任务约定。** 现有工具输出固定键，但任务未指定键名。本版支持该工具实际报告结构；替代schema返回未覆盖/verification_error，不武断判错。未来如允许任意报告格式，需要给LLM增加字段映射，再由代码校验映射后的值。
3. **回复必须单独验收。** 文件完全正确也可能伴随错误或遗漏的交接说明。真实矛盾样本证明不能用文件通过替代回复通过。
4. **LLM输入依然较长。** 完整任务、18份核实事实及长回答使输入约1.68万token。当前完成的是可运行验证原型，没有达到充分低成本；后续可讨论去除重复输入，但未在本轮扩展优化或重复实验。
5. **静态参考只适用于同一固定任务和初态。** 输入变化后需重建标准；参考可信性在准备时由源文件核对。

## 边界

语义检查依然可能有模型误判，验证输出结构和引用只能约束证据格式，不能证明推理正确。只试了一个正常回答和一个矛盾回答，没有做大规模准确率评估。NPZ/JSON有面向本题的小文件大小限制，不是通用多任务服务或完整恶意文件沙箱。没有核查“过程曾经怎样”，也没有新增过程验收要求。未实现另外四个任务，未修改原有 verifier。
