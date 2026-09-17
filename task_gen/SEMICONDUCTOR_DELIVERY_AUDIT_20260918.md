# 半导体八环境：上游问题与 TaskGen 适配边界

日期：2026-09-18。检查对象：`/data1/agent_world/toolgen_semiconductor_0917_rich/delivery`。本地代码：`kimi` 分支，提交 `95fe49b`，位于 `.worktrees/kimi`。

## 1. 结论

这批环境的定义和资源基本齐全，但**目前不能直接通过我们的管线运行**。问题分为两层，两层都需要处理：

- **上游交付问题：**前五个环境的 `software/profile.json` 指向基础 Python，没有指向安装业务依赖的虚拟环境；另外，后三个环境的虚拟环境仍依赖交付目录之外的基础运行时，不能据此承诺整个目录可以直接搬到另一台机器运行。
- **我们的适配缺口：**Step 0 仍按旧目录布局读取；主管线执行路径没有完整传递软件运行环境；沙箱不支持本次实际出现的虚拟环境布局，而且可能混用不同 Python 版本的依赖。

因此，不能把全部失败算作上游工具代码错误，也不能认为只修正上游的 Python 路径就足够。这里主要是**交付布局及工具运行环境适配**，不需要据此改变建图、采样、任务生成的判断原则。

本次只整理分析，不修改上游文件或本地业务代码。下文的“建议”尚未实施。

## 2. 已经检查了什么，尚未证明什么

对八个包检查了 binding 加载、环境与工具定义、资源引用、验证报告及状态文件；通过当前工具沙箱，在临时复制的状态中执行了代表性工具调用。

| 环境简称（目录均以 semiconductor_ 开头、_1 结尾） | 正式工具 | Record Set | Filesystem Scope | 当前主要阻断 |
|---|---:|---:|---:|---|
| calibration_deembedding | 13 | 5 | 1 | 上游 Python 映射错误；本地依赖混用 |
| circuit_level_s_matrix | 12 | 5 | 1 | 同上 |
| cloud_fdtd | 27 | 12 | 1 | 同上；实际调用提示缺少 tidy3d |
| design_optimization | 9 | 12 | 1 | 上游 Python 映射错误；本地依赖混用 |
| diffusion_reaction_pde | 11 | 3 | 1 | 同上 |
| eigenmode | 32 | 12 | 1 | 本地沙箱对虚拟环境路径的假设不成立 |
| eme_propagation | 20 | 5 | 4 | 同上 |
| generic_fem_pde | 15 | 0 | 1 | 同上 |

确认的正面结果：

- 八个包均能由当前 `load_delivery()` 加载，合计 139 个正式工具都有 `passed` 验证记录。
- 工具 `usageConditions.targetResources` 引用的资源均存在；交付工具列表与当前对应的生成源工具列表一致。
- 状态目录未发现符号链接；七个含记录库的环境，SQLite `PRAGMA quick_check` 均返回 `ok`。
- `generic_fem_pde` 没有 Record Set，因此没有记录数据库是正常情况。不能把统一检查脚本打开数据库失败当作它的交付错误。

这些结果不等于工具语义全部正确。上游通过记录反映上游测试条件；我们还需要证明同一工具能在自己的执行器里工作。代表性试调用共 15 次，目前全部失败，但失败集中在运行环境准备和依赖加载，**不能推导为 139 个工具的实现都存在问题**。

尚未完成八环境的完整任务生成、独立 agent 求解及 verifier 验收。此前旧环境真实任务通过、集成测试通过，均不能替代本批环境验收。

## 3. 应由上游修正或明确的内容

### U1. 前五个环境的 Python 映射丢失了虚拟环境

**性质：已确认的交付配置问题，阻断使用。**

业务依赖通常安装在虚拟环境中。基础 Python 和该虚拟环境可能来自同一套 Python 安装，但直接启动基础 Python，不会自动使用虚拟环境的第三方包。

本次实际映射如下，路径均相对 `delivery/`：

| 环境 | Profile | python_path 后半段 |
|---|---|---|
| calibration | py-6c4e9b3c5749 | interpreters/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11 |
| circuit | py-61ec32589142 | interpreters/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11 |
| cloud | py-6d8c8cf37adb | interpreters/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11 |
| design | py-041d35f6719d | interpreters/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13 |
| diffusion | py-4366eff79a3a | interpreters/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11 |

例如 calibration 的完整字段是：

```text
software_profiles/profiles/py-6c4e9b3c5749/interpreters/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11
```

用映射指定的 Python，以 `-I` 隔离模式检查包发现情况：calibration 找不到 numpy、skrf；circuit 找不到 numpy、sax；cloud 找不到 gdsfactory、gdstk、numpy、tidy3d、yaml；design 找不到 pyopus；diffusion 找不到 numpy。这里的 `find_spec` 检查证明包不可发现，不是完整的求解器测试。

**为什么发生：**同事源码快照 `env_gen/tool_gen/delivery.py` 将 `software["python"]` 做了 `Path.resolve()` 后再发布路径。虚拟环境启动器如果是符号链接，这会把路径变成基础解释器路径，丢失虚拟环境入口。我们的分支已修过发布/加载时过早解析最终启动器链接的问题，但这些代码修改不会自动修复已经生成的五份 `profile.json`。

**建议责任划分：**上游修正发布逻辑并重新交付正确映射；我们消费明确映射、保留启动器语义，不在运行时猜测哪个 Python 才是正确的。

**验收：**从实际交付映射启动 Python，能导入所需依赖，并完成至少一次使用业务依赖的真实工具调用。不能只验收 `python --version` 或 Schema 通过。

### U2. 后三个环境依赖交付目录之外的基础 Python

**性质：已确认的部署依赖；是否算交付缺陷，取决于是否承诺目录独立可搬迁。当前机器上不一定因此失败。**

后三个环境映射的是虚拟环境入口：

- eigenmode：`py-e699b52f834b/python-3.11/bin/python`。
- eme：`py-9d6611d257a9/python-3.10/bin/python`。
- generic FEM：`py-cb65cd69ab51/python-3.11/bin/python`。

直接探测解释器得到：eigenmode 和 generic FEM 的 `sys.base_prefix` 指向 `/data1/agent_world/toolgen_semiconductor_0917_rich/runtime/interpreters/cpython-3.11.16-linux-x86_64-gnu`；eme 指向 `/usr`。前者在 delivery 外，后者依赖机器系统安装。

这意味着拷贝 `delivery/` 本身不一定能重建运行条件。后三个环境的已检查依赖可被发现，但这既不证明完整导入成功，也不证明全部求解器可运行。

**建议：**上游明确二选一的交付承诺：一是声明所需的外部 Python/系统依赖及重建方式；二是交付并验证可独立部署的运行时。我们应检查声明的依赖是否可用，并给出清晰错误，不把缺失依赖伪装成模型执行失败。

## 4. 应由我们完成的适配

### D1. Step 0 按 binding 解析新目录布局

**现状：**`task_gen/tool_graph/step_0_environment_load.py` 的 `load_environment()` 直接读取 `config.environment_dir/environment.json`；v2 分支继续从同目录读取 `tools.json`、`state/` 和 `tool_generation/tool_validation.json`。

新版包是：

```text
<package>/
  binding.json
  environment/environment.json
  environment/state/
  environment/validation.json
  tools/tools.json
  tools/tool_validation.json
  software/profile.json
```

把 `environment_dir` 指到包根目录，找不到原位置的 environment.json；指到 `environment/`，又找不到同级 tools.json。不是调一下路径就能完整解决。

**应做：**复用已有 `load_delivery()`，以 binding 定位环境、工具、初态和软件依赖；保留后续阶段真正需要的已解析运行信息。模型看到的仍是公开环境/工具信息，不需要看到主机部署路径。

**验收：**八个包均可直接作为输入，Step 0 读到正确的工具和状态来源；不得要求人工重新拼装旧目录。

### D2. 将软件运行环境传到所有实际执行工具的位置

**现状：**最终 Kimi 评测的 binding 接入已经存在，但不等于主管线每个阶段都接入了。

- `task_gen/task_eval_mcp.py` 的 binding 处理能产生 `software.root` 和 `software.python`，传给工具执行器。
- `step_3_chain_execute.py` 的 `_call_tool(..., software=None)` 已接受软件运行环境；但上层实际调用没有传入该参数。
- 同文件仍从 `config.environment_dir/state` 复制初态，与新布局不符。
- `review_agent.py` 构造的工具服务配置包含工具、只读环境、状态及资源限制，没有软件运行环境。若当前启用路径需要由它读取初态，也会遇到同类依赖问题。

**应做：**沿已有调用路径传递解析好的运行信息，统一落到现有工具执行器；不要为每个阶段另造一套 MCP 转换器。对初态路径也使用解析结果。

**特别注意：**review 使用只读资源声明及工具子集，不能直接套用最终评测中“完整工具表和环境必须与 binding 完全一致”的校验，否则会把合法的只读视图误判为不一致。应复用运行环境，同时保留 review 自己的工具和状态权限约束。

**验收：**相同工具在主管线执行、启用的初态检查路径、最终 Kimi 评测中使用相同的软件依赖；各阶段仍使用自己的状态副本。若启用 ReAct，也需要核对其执行入口是否传递同样的信息。

### D3. 沙箱需要支持真实的虚拟环境布局

**现状：**`step_3_chain_execute.py:_run_tool()` 探测 `sys.base_prefix` 和 `sys.executable` 后，要求解释器文件位于基础运行时目录之下：

```python
runtime_executable = Path('/runtime') / executable.relative_to(runtime_root)
```

后三个环境的解释器位于虚拟环境目录，基础运行时位于另一目录。这种布局本身合法，但不满足我们的 `relative_to()` 假设，返回：

```text
Python 解释器不在其运行时目录中
```

**责任：这是我们的适配限制，不是上游必须把合法 venv 改成某种特殊布局。**

**应做：**分别处理启动器、基础标准库及虚拟环境依赖的挂载和寻址，保留虚拟环境语义；继续使用 bubblewrap、只读依赖挂载和任务状态隔离。修复执行布局不意味着允许 agent 直接访问这些主机文件。

**验收：**真实交付中的 Python 3.10、3.11、3.13 入口均能在沙箱里启动，加载对应依赖；不再依赖“executable 必须在 base_prefix 下”这一条件。

### D4. 避免不同 Python 版本混用第三方包

**现状：**沙箱为提供 jsonschema，将主管线当前 Python 的整个 site-packages 目录挂载到 `/dependencies`，worker 把它加入模块搜索路径。软件 profile 的依赖虽优先，但缺包时仍可能从这个目录找到其他包。

我们的主管线依赖包含 Python 3.12 的二进制扩展，交付 Python 包含 3.10、3.11、3.13。它们不能安全互换。本次出现 Python 3.11 加载带 `cpython-312` 标记的 NumPy 扩展，以及 `ModuleNotFoundError: rpds.rpds`。

前五个环境错误的解释器映射使问题更容易暴露，但依赖混用是独立的本地问题：即便改正上游映射，也不应允许缺包时悄悄借用另一个 Python 版本的扩展。

**应做：**工具及状态校验所需依赖必须与执行解释器兼容。先核对交付运行时是否已有 jsonschema 及其依赖，再选择最小的兼容补齐方式；不得将主进程整个 site-packages 当作通用后备依赖库。依赖缺失应在预检或启动时明确报错。

**验收：**不同 Python 版本调用成功；人为缺少依赖时明确失败，不回退加载异版本二进制扩展。

## 5. 代表性试调用记录

这组检查通过我们的 `call_environment_tool`/bubblewrap 执行，使用 binding 指定的软件环境；状态先复制到临时目录，未在上游原始状态中写入。单次超时 35 秒、内存 2 GiB、写入上限 256 MiB。它是运行兼容性检查，不是完整性能评测。

| 环境 | 实际调用工具 | 实际失败 |
|---|---|---|
| calibration | compare_workflow_methods | 缺少 `rpds.rpds` |
| calibration | inspect_touchstone_response | NumPy 的 Python 二进制版本不匹配 |
| circuit | query_device_variants | 缺少 `rpds.rpds` |
| circuit | analyze_sparam_spectrum | NumPy 的 Python 二进制版本不匹配 |
| cloud | compare_experiment_candidates | 缺少 `rpds.rpds` |
| cloud | analyze_hdf5_simulation_data | 返回解析失败，底层缺少 `tidy3d` |
| design | get_record_by_key | 缺少 `rpds.rpds` |
| diffusion | get_simulation_case | 缺少 `rpds.rpds` |
| diffusion | convert_reference_field | NumPy 的 Python 二进制版本不匹配 |
| eigenmode | get_catalog_record | Python 解释器不在其运行时目录中 |
| eigenmode | evaluate_material_index | 同上 |
| eme | get_device | 同上 |
| eme | validate_notebook | 同上 |
| generic FEM | browse_project_assets | 同上 |
| generic FEM | inspect_mesh | 同上 |

三个可复现的具体例子：

```json
{"tool":"compare_workflow_methods","arguments":{"workflow_ids":["multiline_trl_workflow","open_short_workflow"]}}
{"tool":"analyze_hdf5_simulation_data","arguments":{"hdf5_path":"projects/mmi_collection/misc/mmi_simulation_data.h5","operation":"browse"}}
{"tool":"evaluate_material_index","arguments":{"material_model_ids":["femwell_sin_sellmeier","femwell_sio2_sellmeier","femwell_air_1"],"wavelength_um":1.55}}
```

第一个本应读取并比较工作流，却在校验依赖处失败；第二个本应浏览 HDF5 数据，却因无法导入专业库失败；第三个甚至尚未进入业务计算，就被本地解释器路径检查拒绝。由此可见当前优先事项是修复运行基础，而不是调整模型 prompt。

本表来自本次检查的终端返回，未另存完整逐调用原始 trace。后续修复验收应保存结构化调用结果，便于复现和对比，不能将本表当作完整 trace 的替代。

## 6. 与以前相比，变化在哪

参考旧 `artifacts/upstream_20260911/agentworld-toolgen-data0910-20260911/...` 交付和当前代码：核心环境表达仍是 v2 的 Record Set、Filesystem Scope、relationships；没有证据表明需要再次重写状态模型。

主要变化是：

1. 环境、工具、验证报告、软件 profile 被正式拆分，由 binding 连接；旧加载器的目录假设失效。
2. 本批工具实际依赖 NumPy、科学计算库、专业文件解析器和不同 Python 版本，过去较轻量的工具没充分覆盖这些条件。
3. 同事共享 MCP 的更新还涉及短摘要与完整结构化结果，但这是协议实现版本同步问题，不能与八个包的环境数据质量混为一谈。

没有拿到同一批八环境的旧快照，因此这里不是它们逐工具、逐字段的历史差分。当前交付 tools.json 与当前生成源一致，只能证明交付没有在这一步丢失或改写工具，不能证明它们相对过去没有变化。

## 7. MCP 更新：单独列出的责任边界

同事 `/data1/agent_world/kimi-mcp-integration-20260917` 的新协议将 `content` 做成最多 3800 个 UTF-16 单元的摘要，`structuredContent` 保留完整对象。我们不能把摘要上限理解为模型一定只接收这么多内容：Kimi 在处理结构化结果时仍可能把完整结果纳入消息，须核对适配后的实际模型输入。

此前检查还复现了上游 `compact_result_summary()` 的边界失败：大量集合字段或特别长的标识字段可触发 `AssertionError: result summary exceeded its character budget`。这是**上游共享协议代码的边界缺陷**，不是本次八个环境已实际触发的错误。应反馈上游并补摘要边界检查；我们再同步修正版。

我们负责把更新后的标准结果接入现有受限长结果读取机制，确保原始结果可取、错误标志不丢失、完整大对象不会绕过长度处理再次进入上下文。正式环境工具列表的相等关系与 Kimi 展示层附加的读取辅助工具应分层核对；展示适配不能被表述成逐字段完全相同。

## 8. 不应误报的问题，以及仍待实测的风险

**目前不算交付错误：**

- 验证报告有 rejected/skipped 候选，但它们不是正式工具缺失通过记录；正式 139 个工具都有 passed。
- 环境只有文件、没有数据库；generic FEM 就是这种合法情况。
- 大量工具只读、没有 sideEffects。它们可以支持分析研究任务，不能仅据此认定环境不合格。
- 合法虚拟环境的启动器不在 `sys.base_prefix` 内。这是本地兼容问题。

**目前不能下结论：**

- 记录值、关联关系及专业文件内容是否足以支撑高质量复杂任务：结构检查与 SQLite quick_check 不证明这些语义。
- 所有数值计算、动态导入和外部求解器是否能运行：包发现检查不等于完整导入，更不等于计算成功。
- 当前工具时限和内存是否适合专业计算：本次主要失败在启动/导入，不能据此决定扩大资源或认定求解过慢。
- verifier 是否需要匹配的专业依赖来读取结果文件：需要在执行跑通后核对，不应提前宣称现有 verifier 已坏或必须重写。
- 跨机器运行是否可靠：需按上游明确的部署契约实测。

## 9. 建议推进顺序与验收

1. **先修正上游五份 Python 映射，并明确外部运行时依赖。**当前机器上可先按声明的真实依赖验收，不必先建设复杂的跨机器分发系统。
2. **我们修正共享工具执行器。**处理虚拟环境布局及同版本依赖；先用固定参数工具调用验证，避免一边浪费 LLM 调用一边排查基础运行错误。
3. **我们接通 binding 到管线入口和执行调用方。**复用现有加载器及执行器，保留状态隔离、只读检查、失败回滚、工具白名单和正式工具一致性校验。
4. **八环境分别做代表性验收。**至少涵盖记录读取或文件读取、专业库实际计算；有写操作的环境再测状态提交与失败回滚。源状态不能被改变。
5. **再跑完整任务。**从建图推进到独立求解和 verifier，保留原始 LLM/工具记录，区分模型失败、业务失败、基础设施失败。

验收完成前准确的状态是：**数据和定义具备接入基础；交付运行时存在问题；我们的执行及目录适配尚未完整；八环境全流程可用性未被证明。**

主要证据入口：上游各包的 `binding.json`、`software/profile.json`、`tools/tool_validation.json`；同事快照的 `env_gen/tool_gen/delivery.py` 与 `mcp_protocol.py`；本地 `step_0_environment_load.py`、`step_3_chain_execute.py`、`review_agent.py`、`task_eval_mcp.py`。历史 Kimi 集成范围见同目录 `KIMI_EVALUATION.md`，不能代替本批交付验收。
