# 文件式生成模块实施报告

完成 `codex_tools_generation_brief.md` 指定范围，未提交或推送。

## 实现

- 新增 `codex_tool_verifier/generation.py`：沿用 `generate(evidence_dir, output_dir, config=None, model_call=None)` 调用方式，设计交付恰好四份文件，原样复制并冻结；工具顺序进入独立工作目录，仅收取当前目标文件，拒绝其他文件的修改或新增；支持零工具，不生成 `main.py`。生成失败留档，不在宿主开启重试会话。
- 新增 `codex_tool_verifier/check.py`：设计阶段校验四文件与设计自测；实现阶段实际 profile 目标入口，校验正常返回值的 JSON/schema，区分异常退出与真正返回 `None`。自测在交付包外执行，禁止字节码写入，检查前后交付文件不变；独立工作目录的 `tools` 导入缓存不互相污染。
- 新增两个文件式 prompt：明确先固定 rubric 后设计工具、任务原文精确引用、证据身份/路径/记录契约、完整 schema 与输出空间、正常缺失政策、工具直接评分边界及主 Codex 语义判断。参考可作恰当对照，禁止硬编码参考答案评分。`required_tools` 只表示无条件必须调用；条件适用或可替代证据写入 `evidence_plan`。
- 旧 `program_verifier/generation/model.py` 仅增加可覆盖的 `design_files`、`check_module` 两个类属性并使用它们，默认交付文件与检查模块保持原行为；新生成器使用子类。

## 测试证据

命令：

```bash
python -m pytest task_gen/test_verifier/codex_tool_verifier/tests/test_generation.py task_gen/test_verifier/program_verifier/tests/test_generation.py task_gen/test_verifier/program_verifier/tests/test_generation_workspace.py -q
```

结果：`37 passed in 5.77s`。其中新生成测试 19 项，旧生成相关测试 18 项。覆盖四设计文件冻结、逐目标/已有工具保护、零工具、失败不重试、设计自测执行、真实目标 profile、无效输出与 `None` 拒绝、预期异常分支、自测失败留档、主机独立复跑及证据写保护。

涉及 Codex 的单元测试只替换模型客户端；验收命令和 bubblewrap 隔离子进程真实执行，未跳过隔离测试。

```bash
python -m py_compile task_gen/test_verifier/codex_tool_verifier/generation.py task_gen/test_verifier/codex_tool_verifier/check.py task_gen/test_verifier/program_verifier/generation/model.py
```

结果：退出码 0。

## 限制

本模块未发起真实 Codex 生成或参考任务验收，未评估任务评分准确率；本报告的测试证明宿主生成/交付/自测机制。未修改 contracts.py、tool_runner.py、application.py 或 CLI。
