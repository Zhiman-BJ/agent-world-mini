# Step 3：Agent 直接集成最终环境

阶段入口、Agent 循环和 Prompt：`env_gen/data_gen/steps/step3_integrate_data.py`

一个 Agent 直接根据业务要求、原始文件说明和实际原件完成业务建模、清洗、合并、去重、关系修复和
文件组织，并直接写出最终环境。Python 只执行事实验收，并把具体错误返回给 Agent 修复。

## 1. 工作流

```text
业务要求 + 原始文件说明 + 实际原件
        |
        v
Agent 理解并转换实际数据
        +--> environment.json
        +--> state/records.sqlite
        +--> state/filesystem_scopes/<scope_id>/
        |
        v
integratectl assess
        +--> Schema、表列和类型
        +--> 唯一键与关系闭合
        +--> 文件引用和 Scope 文件树
        |
        +--> fix：Agent 只修具体错误并再次检查
        +--> ready：结束 Step 3，进入 Step 4
```

原始文件说明和实际原件在集成时是只读输入。集成阶段不再联网补采；真实数据不足应在采集阶段解决，
避免下载职责与建模职责再次混在一起。

## 2. Agent 负责什么

Agent 负责需要业务语义的工作：

- 判断哪些 Raw 表示同一业务对象；
- 合并兼容来源并统一字段名、类型、枚举和时间表达；
- 选择稳定主键，处理重复记录和冲突值；
- 修饰跨来源标识，使最终关系能够闭合；
- 保留有意义的真实差异，不用空值或伪造记录填充覆盖；
- 检查压缩包内部成员和大型数据集，集成全部与业务相关的记录与变化类型；下载容器通常需要解包，
  不能把有用归档只登记为一条文件记录，也不能只选择最容易处理的演示样例；
- 文件清单、哈希和下载来源默认属于溯源信息；除非业务要求明确需要查询它们，否则不建立相应业务表；
- 源码和说明文档若只用于理解或实现工具，就留在溯源材料中；业务任务不操作它们时，不建立最终文件
  Scope，也不自行发明源码审阅任务；
- 只在任务需要直接操作文件时建立 Filesystem Scope；
- 让环境尽可能保留原始材料已有的业务覆盖和数据丰富度。

采集阶段的覆盖底线不限制集成规模。Agent 应整合所有有用业务数据，同时排除只起说明作用的网页或文档。

## 3. 直接生成最终环境

Agent 直接生成以下产物：

```text
environment.json
state/records.sqlite                 # 有 Record Set 时
state/filesystem_scopes/<scope_id>/  # 有 Scope 时
```

Agent 可以使用命令、已安装工具或临时脚本解析和批量处理数据，不要求提交固定形式的转换程序。临时文件
不能混入最终 `state/`。Agent 通过一个检查命令获得机械错误：

```bash
bash ./.datagen/integratectl assess
```

控制器在每轮 Agent 结束后也会重新执行验收，不接受 Agent 自行修改的验收结果。

## 4. Python 验收边界

Python 不猜测领域语义，只检查可以从磁盘事实确定的内容：

- `environment.json` 符合 v2 Schema，并沿用 Step 1 的环境语义；
- SQLite 可读且 `integrity_check=ok`；
- 声明的表、字段顺序、SQLite 类型和 nullable 属性与实际一致；
- `key_fields` 在真实记录中非空且唯一；
- 关系目标唯一，来源外键没有部分空值或悬空引用；
- object/array 使用规范 JSON，boolean 使用 0/1；
- 文件路径引用安全且目标真实存在；
- Scope 的实际目录和文件符合 `structure`；
- Step 2 Raw、下载收据、文件卡和最低覆盖线仍然有效。

错误统一写入 `.datagen/integration_assessment.json` 的 `blocking_issues`。Agent 不需要阅读大型画像，
只修对应路径和错误原因。

## 5. 交给 Step 4 的产物

Step 3 成功时只有以下核心集成产物：

```text
environment.json
state/records.sqlite                 # 有 Record Set 时
state/filesystem_scopes/<scope_id>/  # 有 Scope 时
.datagen/integration_assessment.json
```

来源调查、文件卡和 Raw 继续作为输入证据保留。`integration_plan.json`、逐资产 transformation package、
`integration_profile.json`、`quality_profile.json` 和 `field_review.json` 不再是新流程的必需产物。
