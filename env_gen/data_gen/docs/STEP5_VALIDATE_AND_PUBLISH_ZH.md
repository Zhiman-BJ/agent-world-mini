# Step 5：独立校验、哈希收口与发布

阶段入口：`env_gen/data_gen/steps/step5_validate_and_publish.py`

Step 5 是最终发布门，不负责发现来源、补采数据或重设计模型。它只验证 Step 4 冻结的事实和声明
是否一致，并把通过的 staging 目录原子移动到 `rich/` 或 `not_rich/`。

## 1. 校验顺序

```text
读取冻结包
  |
  +--> integration_plan Schema 和语义
  +--> provenance/raw 文件、URL、SHA-256、字节数
  +--> 每个转换包、脚本和物化状态摘要
  +--> 独立重算 integration_profile
  +--> 字段复核覆盖当前提示，且绑定当前 plan/profile 哈希与 Raw
  +--> 独立重算 environment_quality_profile
  +--> environment.json 与 integration_plan 确定性比对
  +--> v2 environment/state 校验
  +--> freeze_manifest 全量哈希比对
  |
  +--> 任一事实错误：拒绝发布，回到 Step 3
  +--> 声明错误：当前版本不再自动修业务事实
  +--> 全部通过：写 validation.json 并原子发布
```

## 2. 发布状态

| 状态 | 发布条件 | 目录 |
|---|---|---|
| `rich` | `integration_tier=integrated` 且质量画像没有缺口 | `rich/<global_id>/` |
| `not_rich` | 集成事实合法、来源和需求已收口，但质量门槛仍有真实缺口 | `not_rich/<global_id>/` |
| `failed` | 事实、计划、收据或协议校验失败 | `failed/<global_id>-<timestamp>/` |

`not_rich` 不是 Validator 失败，也不是把缺口隐藏掉。它保留真实可复现的数据包，并在
`provenance/quality_profile.json` 中列出每个缺口；后续批次可以据此决定是否值得补采。

## 3. 不可越过的事实边界

- `environment.json` 必须是 `integration_plan` 的确定性导出；
- `state/records.sqlite` 的表、列、类型、记录和关系必须与声明一致；
- 每个 Scope 的实际文件必须符合 `structure.layout`；
- 每个 Record 的文件路径必须在对应 Scope 中存在且目标类型正确；
- provenance/raw 的每个文件必须有真实下载 URL 和可重算 SHA-256；
- 转换脚本在独立、无网络环境中重放两次必须得到相同输出；
- 当前字段画像提示必须具有对应 Raw 路径和受控复核收据；
- 发布包不能包含 `.datagen` 控制目录、SQLite sidecar 或未声明文件。

Step 5 只验证这些边界，不会为了让 `quality_tier` 变成 `rich` 而生成业务记录、修改来源事实
或放宽关系约束。

## 4. 发布后检查

发布后可执行批量审计脚本，对多个环境再次检查：

```text
validation.valid == true
checkpoint/manifest 文件集合一致
Raw SHA-256 可重算
source_plan 来源和数据需求已收口
integration_profile == integrated
quality_tier 正确反映 rich/not_rich
```

批量 HTML 只展示发布后的实体、文件 Scope、关系、来源和质量结果，不展示运行期草稿或 Agent
中间推理。
