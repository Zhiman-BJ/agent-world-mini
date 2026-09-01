# Step 0：准备一次环境生成运行

入口：`env_gen/data_gen/steps/step0_prepare_run.py`

## 1. 目标

Step 0 只建立后续阶段共用的运行上下文。它选择并验证一条完整 Seed，解析协议路径，固化本次运行
使用的策略，然后创建隔离的控制目录。Step 0 不调用 Agent，也不形成任何业务调研结论。

Step 0 直接复用流水线已有的 `DataGenConfig`，不再定义一层只用于传参的配置类。与准备有关的
字段是：

```text
DataGenConfig
├── seed_path                       Seed 集合文件
├── global_id                       本次选择的 Seed
├── seed_validation_schema_path     Seed 校验 Schema
└── contract_path                   可选；默认使用环境契约 v2.0
```

调用方把已经验证的全局配置作为普通字典传入：

```text
limits                         各阶段时间、轮次和文件空间限制
quality                        最终环境质量标准；只运行 Step 0/1 时可以为空
```

Step 0 不创建或执行任何 Policy 类，只负责把这两个配置快照写入运行上下文。

## 2. 执行树

```text
prepare_generation_run
├── A. 创建隔离目录
│   ├── .datagen/drafts/
│   └── provenance/
├── B. 解析协议
│   ├── Seed 校验 Schema
│   ├── environment.schema.json 与环境契约
│   └── 后续阶段实际读取的 checkpoint Schema
├── C. 选择 Seed
│   ├── 校验完整 Seed 集合
│   ├── 按 global_id 唯一匹配
│   ├── 复算 global_id
│   └── 计算规范化 JSON SHA-256
└── D. 固化运行上下文
    ├── .datagen/selected_seed.json
    └── .datagen/run_config.json
```

## 3. 产物

`selected_seed.json` 是从 Seed 集合中原样选出的完整对象，供场景调研和来源探索读取。

`run_config.json` 是程序内部运行上下文，保存 Seed 身份、协议绝对路径和确定性策略。后续阶段读取
这份文件，不再逐层传递 Seed、Schema、契约和质量策略参数。

Step 0 只返回本次选中 Seed 的 SHA-256；完整 Seed 已经写入固定位置，无需再包装返回：

```text
seed_sha256
```

## 4. 阶段边界

Step 0 负责回答“这次运行使用哪条 Seed、哪些协议和哪些策略”。场景是什么、有哪些实体、工具和任务，
由 Step 1 调研；真实来源、下载和数据建模由后续阶段完成。
