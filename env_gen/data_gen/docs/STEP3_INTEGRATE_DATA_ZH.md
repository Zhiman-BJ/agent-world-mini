# Step 3：来源选择、定向深采与数据集成

阶段入口：`env_gen/data_gen/steps/step3_integrate_data.py`

Step 2 只回答“来源实际有什么”。Step 3 才把这些样本变成最终环境数据：选择有价值的来源，
定向补充覆盖，建立统一的 Record Set 和 Filesystem Scope，并在每轮之后由 Python 重新画像。

## 1. 工作流

```text
Step 2 source_plan + source_inventory
        |
        v
保存 integration_plan
        |
        +--> source_decisions：core / supporting / evidence_only / rejected
        +--> record_sets：统一业务记录集合
        +--> relationships：真实可闭合的记录关系
        +--> filesystem_scopes：按操作上下文组织的文件工作区
        +--> need_bindings：每个 Step 1 数据需求的实际落点
        |
        v
定向深采（只补选中来源和明确缺口）
        |
        v
确定性转换 + 物化
        +--> state/records.sqlite
        +--> state/filesystem_scopes/<scope_id>/
        |
        v
integratectl assess
        +--> integration_profile：集成事实
        +--> environment_quality_profile：丰富度事实
        +--> next_actions：下一轮的最小动作
        |
        +--> ready：rich，进入 Step 4
        +--> exhausted：事实合法但 not_rich，进入 Step 4
        +--> continue：补采、修转换、拆 Scope 或移除资产
```

`exhausted` 不是忽略质量问题。它表示来源和数据需求都已真实收口，数据仍然可复现、可操作，
但公开数据客观上没有达到默认丰富度门槛；这种环境发布到 `not_rich/`，不能伪装成 `rich`。

## 2. 集成计划的职责

`integration_plan.json` 是最终数据组织的执行合同，不是调研报告。

### 2.1 来源决策

每个 Step 2 来源必须恰好有一个决策：

| 决策 | 是否可以进入最终资产 | 含义 |
|---|---:|---|
| `core` | 是 | 直接支撑环境主轴和核心数据需求 |
| `supporting` | 是 | 有用的词典、参考、辅助记录或领域文件 |
| `evidence_only` | 否 | 只用于证明来源或协议，不暴露给任务侧 |
| `rejected` | 否 | 与环境无关、重复或不可用 |

`evidence_only`/`rejected` 不能通过 `source_ids` 或 `source_paths` 间接进入 Record Set 或 Scope。

### 2.2 Record Set

Record Set 是面向后续工具查询的稳定记录集合，不要求一份来源文件对应一张表：

- 相同业务概念的兼容来源合并为一个集合；
- 多对多关系使用关联集合；
- `key_fields` 只使用顶层、非空、标量字段；
- 对象和数组字段必须声明完整的 `properties`/`items` 结构，容器嵌套最多两层；
- 字段引用文件时，只允许顶层 `string` 或 `array[string]` 声明 `filesystem_path`；
- `source_paths` 必须是实际已画像的 Raw，`transformation_id` 必须唯一且可重放；
- `description` 只写语义、用途和边界，不写会因补采失效的当前数量。

Record Set 的物理实现是 `state/records.sqlite` 中的一张同名严格表。对象和数组以规范 JSON 文本
存储，读取时由工具还原为 JSON 值。

### 2.3 Filesystem Scope

Scope 是一个工具可以理解和操作的工作区，不是所有 Raw 的汇总目录：

- 单个文件使用 `kind=file`；
- 同类文件集合使用 `kind=file_collection`；
- 一个有真实层级的项目目录使用 `kind=directory`；
- 多个同构项目使用 `kind=directory_collection`；
- `layout` 必须覆盖 Scope 中所有普通文件，不能只声明“重要文件”而静默暴露其余文件；
- 同一个 Raw 只能物化到一个最终 Scope，多个 Record Set 可以共同引用该 Scope；
- 需要保留损坏输入时使用 `content_validation=allow_invalid`，并在记录中保留其诊断状态。

Record 中的文件字段只保存 Scope 相对路径，例如 `drawings/part-01.dxf`，不保存机器绝对路径，
也不把每个文件强行建模为对象。

## 3. 定向深采的原则

补采必须由画像中的具体缺口触发：

```text
核心实体记录不足       -> 补充分页/地区/时间范围
关系键无法闭合         -> 补齐关系另一端或修正字段映射
文件集合过薄           -> 补充真实的同构项目/格式分支
文件存在但无法定位     -> 增加顶层 file_path 索引
多个来源互不连接       -> 合并兼容概念或建立事实关系
来源不可访问           -> 记录 blocked/unavailable，不生成替代业务记录
```

补采前必须把精确 URL 加入 `source_plan.registered_urls`，补采后必须重新生成受影响的转换和
Scope。不得用合成业务记录填充数量门槛；确定性解包和转换只用于产生计划中声明的 Record Set
或 Filesystem Scope，转换代码与重放证据保存在 provenance，不另设任务侧 `Derived` 类型。

## 4. Python 画像与决策

### 4.1 integration_profile

程序从 SQLite 和 Scope 现场计算：

- 每个 Record Set 的真实记录数和空表；
- 每个字段的非空率、空内容数、去重数、头部值、跨数据位置样本和长度/范围形态；
- 每条关系的目标唯一性、缺失引用、复合键空值和实际命中数；
- 每个文件引用的路径存在性、目标类型和实际解析数；
- Scope 声明层级是否覆盖实际文件；
- 资产之间的连通分量和无理由孤岛；
- 选中来源是否全部被使用、是否存在多源合并、是否重复物化同一 Raw；
- 核心资产是否绑定了 Step 1 数据需求。

任一事实错误都会产生 `integration_gaps`，阻止 `integrated`。

字段频次和样本还会产生 `asset_profile.field_review.findings`。例如某个类别值占比异常高，或者
两个本应表达不同概念的字段共享多个类别值。它们是通用启发式复核提示，不直接判定错误，也不
改变 `integration_tier`；Agent 必须抽查不同位置的完整记录并回到 Raw 验证。真实业务偏斜可以
保留，发现解析分支覆盖、错列或值域污染时，只修复对应转换和 Record Set。来源明确给出闭合
值域的字段应在计划中声明 `enum`，物化器会确定性拒绝越界值。

存在提示时，Agent 必须把每条提示与对应 Record Set 的真实 `source_paths` 一起写入字段复核草稿，
并执行：

```bash
bash ./.datagen/integratectl save-field-review \
  --input .datagen/drafts/field_review.json
```

正式 `provenance/field_review.json` 记录 `finding_id`、`verified_against_raw` 决定、核实理由和
Raw 证据路径，并绑定当前 integration plan/profile 哈希。任何计划、数据或画像变化都会使旧复核
失效。提示存在但未复核时，`assess` 保持 `continue`；错误数据不能通过“接受提示”绕过，必须先修
转换再物化。没有提示时不要求额外文件。

声明本身不构成连接。关系必须至少有一条非空来源键命中目标记录；文件引用必须至少有一个非空
路径解析到实际 Scope 成员。全空的 nullable 字段分别产生 `empty_relationship` 或
`empty_file_reference`，也不能用于连通分量和跨来源集成统计。

### 4.2 environment_quality_profile

在集成事实有效后，程序再检查：

- 核心 Record Set 的总记录量和各核心集合的记录深度；supporting 大表不能垫高核心深度；
- 核心集合中实际达到非空率门槛的业务字段数量，始终为空的声明字段不计数；
- 核心数据需求的真实覆盖；明确 `unavailable` 的核心需求会阻止 `rich`；
- Seed 声明的领域文件格式是否实际存在并被索引；
- 文件集合是否有足够成员体现真实变化；`directory_collection` 按同构项目数计数，不按项目内部
  文件总数计数；
- 核心资产是否误把 supporting 数据当作主轴；
- 核心资产是否分裂到多个互不连接的业务分量。

候选操作数、组合链估算和工具数量只作为诊断信息，不能让环境越过质量门槛。

文件格式检查只针对 `source_plan.required_file_formats`。该字段只允许表达最终 Scope 的操作性
要求；来源网页、API JSON、HTML 文档等抽取证据应记录在 `evidence_file_formats`，不会被当成必须
物化的文件。这样可以区分“数据来源格式”和“任务侧文件能力”，避免为满足后缀数量把没有业务
操作意义的文件塞进环境。

### 4.3 最小修复循环

```text
assess
  |
  +-- blocking issue：先修计划、收据、路径或转换错误
  +-- quality gap：只针对该需求补采或调整资产职责
  +-- 无缺口：finalize
```

每轮只重建受影响的资产。若连续多轮没有新增 Raw、可执行转换或物化状态，控制器停止，避免
Agent 在同一问题上重复消耗预算。

## 5. 交给 Step 4 的证据

Step 3 成功收口后，至少应存在：

- `provenance/source_plan.json` 与 `source_inventory.json`；
- `provenance/integration_plan.json` 与其保存收据；
- `provenance/integration_profile.json`；
- `provenance/field_review.json`（只有字段画像出现复核提示时需要）；
- `provenance/quality_profile.json`；
- `state/records.sqlite`（存在 Record Set 时）；
- `state/filesystem_scopes/`（存在 Scope 时）；
- 每个资产的转换/物化收据和独立重放证据。

Step 4 不再发现来源、不再改业务数据，只复核这些事实并导出 `environment.json`。
