# Program-form TaskGen

本模块参考 OmniaBench TaskGen-Program 的核心方法，但不复制旧流水线的十多个中间步骤。它为一个已经包含工具的完整环境生成：

```text
现实业务任务正文
+ output_schema
+ 隐藏 Python 参考程序
+ 内部任务设计说明
+ 真实工具调用轨迹
+ 初始/最终工作区摘要和状态差异
```

## 输入

```text
<environment_package>/
├── environment.json   # 按 schemas/validation/complete_environment.schema.json 校验的完整环境
└── workspace/         # 环境初始状态
```

`environment.json` 必须包含 `tools[]`。只有以下信息会交给任务生成 Agent：

```text
环境名称、描述、resources、rules
tools[].name / description / inputSchema / outputSchema
workspace 初始数据的隔离副本
```

`tools[].internal.code` 不会交给任务生成 Agent。

## 生成 Agent Prompt

权威 Prompt 由 `prompts.py::build_program_generation_prompt` 构造。它不是简单罗列禁止事项，而是依次告诉 Agent：

1. 它是业务 benchmark 任务架构师，而不是工具演示或测试代码生成器。
2. 求解 Agent 最终能看到什么、看不到什么。
3. 合格任务应形成怎样的业务因果闭环。
4. 如何从真实 workspace 中寻找候选、证据、排除条件和唯一决策。
5. 每个工具调用如何对同一个业务结论产生独立贡献。
6. 用户任务正文、隐藏参考程序和结构化结果分别承担什么责任。
7. 如何使用真实执行反馈修复任务，而不是只修改答案。

复杂度的积极目标是：多个真实候选、至少两类证据、一个应被排除的表面候选、过滤后的唯一判断，以及必要时的业务写入。工具调用数量只是最低门槛，重复查询和无贡献调用仍会被视为不合格设计。

每个候选还必须提交内部 `design`：

```text
business_goal
evidence_sources
exclusion_basis
decision_rule
tool_plan[]: tool + purpose
```

`design` 不会展示给求解 Agent。Validator 会检查其中的工具都真实存在，并要求 `tool_plan` 与参考程序实际调用的不同工具集合一致；最终可解性仍由 Runtime 重放确定。

## 流程

```text
完整环境
  -> 公开环境投影
  -> Codex/Terra 联合生成 task + output_schema + solution_code
  -> 候选结构检查和工具名泄漏检查
  -> 受限 Python 参考程序执行
  -> 工具输入/输出 Schema 校验
  -> 工具失败原子回滚检查
  -> 全新 workspace 至少重放两次
  -> 答案、轨迹和最终状态一致性检查
  -> tasks.json
```

候选执行失败时，`validation_feedback.json` 会把真实错误反馈给下一轮 Agent。修复必须重新设计可执行候选，不能只改最终答案。

## 参考程序协议

参考程序只能使用：

```python
result = call_tool("tool_name", {"argument": "value"})
data = result["data"]

# 最后一条语句
final_answer = {"result": data["result"]}
```

禁止 import、文件访问、环境对象、内部状态读取、动态执行和双下划线反射。`final_answer` 必须是严格 JSON-native object，并通过候选声明的 `output_schema`。

这层限制用于约束生成参考解，不是通用恶意 Python 沙箱。环境工具代码仍应由 ToolGen Validator 和独立 Runtime 沙箱负责安全执行。

## 运行

使用 Codex/Terra 生成两个任务：

```bash
python -m task_gen.program_form \
  --environment-package artifacts/mcp_quality_3env_20260825/finstat \
  --output-dir /tmp/finstat-program-tasks \
  --task-count 2 \
  --min-tool-calls 6 \
  --min-distinct-tools 3
```

要求任务必须修改环境：

```bash
python -m task_gen.program_form \
  --environment-package <package> \
  --output-dir <output> \
  --require-state-change
```

离线验证已有候选，不调用模型：

```bash
python -m task_gen.program_form \
  --environment-package <package> \
  --candidates candidates.json \
  --output-dir <output>
```

## 输出

```text
<output_dir>/
├── tasks.json                 # 最终 Program-form 任务包
├── validation.json            # 接受数、拒绝原因和质量门
├── candidates.json            # 各轮原始候选
├── generation_request.json    # 本次确定性生成要求
└── prompt_round_*.txt         # 实际发送给生成 Agent 的提示词
```

`tasks.json.public_environment` 是 Agent 可见协议；`tasks[].reference` 是评测侧隐藏信息，不得暴露给求解 Agent。
