# Step 2：来源探索与代表性取样

阶段入口：`env_gen/data_gen/steps/step2_explore_sources.py`

Step 2 的任务是用少量真实访问把 Step 1 的来源假设变成可审计的事实。它回答“来源实际提供
什么”，不回答“最终环境应该建成几张表”。最终来源选择、深度采集、Record Set、Filesystem
Scope 和质量门槛属于 Step 3。

## 1. 流程树

```text
scenario_research.json
        |
        v
核实来源线索并继续发现替代来源
        |
        v
source_plan.json
  ├── 来源、精确 URL、数据需求和格式假设
  └── 每个来源的 retrieval/coverage/status
        |
        v
真实代表性请求
  ├── API：不同分页、地区、时间或层级
  ├── 批量文件：归档成员和目录根
  ├── Git 仓库：先探索，再固定 commit
  └── 领域文件：不同格式或有效/无效结构
        |
        v
source_inventory.json
  ├── 文件格式、大小、SHA-256
  ├── 解析状态和占位页识别
  ├── 结构化记录组、字段和候选键
  ├── 归档成员和根目录
  └── 来源可用性与检索稳定性
        |
        v
explorectl assess
        |
        +--> 继续补足结构盲区
        +--> 来源 complete / blocked / unavailable
        +--> insufficient_public_data（没有可用核心来源）
        |
        v
explorectl finalize --result ready
```

## 2. 取样边界

取样的目标是覆盖结构分支，而不是提前达到最终记录数量：

- 小型完整文件可以一次下载；
- 大型 API 优先覆盖分页、时间、地区、层级和空结果分支；
- 仓库优先取得目录清单和关键文件，进入最终资产前必须改用 commit 固定 URL；
- 领域文件至少确认实际扩展名、格式和是否需要保留无效样本；
- 同一 URL 或同一内容不重复下载；
- HTTP 200 的登录页、软 404、Git LFS pointer 和跳转页不算有效业务样本。

Step 2 不创建以下内容：

```text
workspace/entities/
workspace/derived/
state/records.sqlite
state/filesystem_scopes/
integration_plan.json
```

这些内容需要基于完整样本进行业务判断，应留给 Step 3。

## 3. source_plan

`source_plan.json` 是探索执行计划和证据索引，不是最终环境模型。每个来源至少说明：

- `source_id`、名称、主 URL 和请求前登记的 `registered_urls`；
- 服务的 `need_ids` 和预期 `target_entity_types`；
- `retrieval`（请求方式、分页/单元数、报告总量）；
- `coverage_strategy`（代表性取样、完整文件、元数据等）；
- `status`（`in_progress`、`complete`、`blocked`、`unavailable`）；
- `status_evidence`（为什么可以收口）；
- `raw_files`（实际成功下载的 Raw 路径）。

### 文件格式角色

`required_file_formats` 的含义是“最终任务侧必须直接操作、因此必须出现在 Filesystem Scope 中的
领域文件格式”。它不是所有下载过的后缀名，也不是所有来源响应格式。

例如：

```json
{
  "required_file_formats": ["solidity", "markdown"],
  "evidence_file_formats": ["html", "json"]
}
```

这里表示 Solidity 和 Markdown 会作为最终文件工作区；HTML 和 JSON 只用于读取网页文档或抽取
Record Set，不需要为了“格式齐全”额外暴露给任务侧。候选格式中最终不需要操作的格式应放在
`evidence_file_formats`，不能放进 `required_file_formats`。
两个字段都必须出现，可以使用空数组；同一格式及其别名不能同时属于两类。

Step 2 继续调查时，把对 Step 1 的确认、修订、淘汰和新增写入 `research_refinements`。新发现
的来源可以将 `scenario_source_lead_id` 设为 `null`，但必须记录 `discovery_note`，并和已有来源
一样通过真实请求确认。

### 来源终态

| 状态 | 含义 | 能否进入 Step 3 |
|---|---|---:|
| `complete` | 代表性样本足以解释来源结构，且可继续深采 | 可以 |
| `blocked` | 真实请求被认证、权限、限流或网络阻断 | 不可以，保留证据 |
| `unavailable` | 端点不存在、内容为空或经过尝试仍不可用 | 不可以，保留证据 |
| `in_progress` | 还没有完成探测或仍有关键结构盲区 | 不可以 |

只有所有来源都处于终态，Step 2 才能 `ready`。如果所有核心来源都不可用，应使用
`insufficient_public_data`，不能为了通过而生成业务记录。

## 4. source_inventory

画像程序只从 `workspace/raw` 和下载收据读取事实，不推断最终 Record Set：

```text
每个 Raw 文件
├── path / source_id / sha256 / bytes
├── format / retrieval_stability / retrieval_urls
├── content_roles
├── parse_status
├── shape
│   ├── structured：记录组、字段、候选键
│   ├── archive：成员数、格式、顶层根
│   ├── xml：根元素
│   └── text：文档或 Solidity/Python 等可读源码的行数
└── issues
```

已识别但没有专用解析器的领域二进制仍可作为 `domain_file` 保留；无法识别格式和用途的普通二进制
标记为 `unknown`，不能仅因下载成功就把来源算作可用。

`structured_record_count` 只表示探索样本中观察到的记录数，不是最终环境应有的深度。字段画像
只取有限样本，不能据此宣称完整枚举或关系闭合。

## 5. Python 检查顺序

```text
精确 URL 是否已登记
        ↓
下载收据、SHA-256 和路径是否一致
        ↓
Raw 是否为真实内容而非占位页
        ↓
格式、解析状态和结构分支是否可解释
        ↓
来源状态是否可以收口
```

`exploration_assessment.json` 只给 Step 2 的下一步动作：

- `ready`：至少一个核心来源可用，所有来源已经收口；
- `continue`：需要补足代表性样本或收口未完成来源；
- `fix`：计划、收据或 Raw 事实不一致。

## 6. 交接给 Step 3

Step 3 读取 Step 2 的样本并重新判断：

```text
source_inventory
  ├── 哪些来源值得进入最终环境
  ├── 哪些来源只能作为 evidence_only
  ├── 哪些字段可以组成统一 Record Set
  ├── 哪些文件需要保留为 Filesystem Scope
  └── 哪些分页/格式/关系需要定向补采
```

Step 2 的样本数量、候选键和字段名称不能直接成为最终协议；Step 3 必须通过确定性转换重新
验证记录、关系和文件路径。
