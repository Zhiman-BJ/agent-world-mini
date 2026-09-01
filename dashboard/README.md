# Environment Observatory

这个静态页面用于查看三个已发布环境的数据结构和来源血缘。页面明确区分：

- Raw：公开来源的原始响应或文件，用于审计和重建；
- Entity：由 Raw 确定性规范化的业务记录，供后续工具直接使用；
- Derived：由现有业务数据确定性计算的统计、索引或聚合；
- Output：后续工具可以写入报告和导出结果的目录。

实体视图会展示每个字段的类型、含义、不同值数量和真实样本。环境包仍是事实来源，
`dashboard/data/environments.json` 只是面向浏览器的只读快照。

更新环境后重新生成快照：

```bash
python scripts/build_environment_dashboard.py
```

启动本地页面：

```bash
python -m http.server 8765 --directory dashboard
```

浏览器访问 `http://127.0.0.1:8765/`。

指定其他环境根目录或输出路径：

```bash
python scripts/build_environment_dashboard.py \
  --source-root /path/to/rich \
  --output dashboard/data/environments.json
```
