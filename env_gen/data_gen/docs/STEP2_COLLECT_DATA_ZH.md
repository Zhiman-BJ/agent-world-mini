# Step 2：采集环境数据

实现入口：`env_gen/data_gen/steps/step2_collect_data.py`

## 目标

本阶段为后续离线环境准备初始业务状态和任务直接操作的文件。两个输入的地位不同：

- `.datagen/selected_seed.json` 是来源平台的原始入口，保存来源地址、简要场景、参考工具和任务，以及可能的
  数据方向。参考工具和任务形成第一组覆盖清单；其余内容用于寻找来源，不直接当成已经核实的事实。
- `provenance/scenario_research.json` 是通过外部来源核实和扩展后的现实业务报告，包含工作背景、实体、工具、
  典型任务、数据方向、来源证据和开放问题。实体、工具和任务形成第二组覆盖清单；数据方向用于选择材料，
  开放问题用于约束不能擅自推断的内容。

Agent 下载实际数据，不以产品说明、API 文档或功能源码代替业务数据。数据尽可能来自同一系统，并通过稳定
标识相互关联。

## 数据形态

采集结果允许两种平级的数据形态，它们没有主次或质量高低之分：

- `business_records`：后续拆成一条条数据库记录，工具按字段和 ID 查询、关联、创建或更新；
- `task_domain_files`：后续保留文件名、目录和原始内容，工具把整个文件作为工作对象读取、校验、比较或修改。

分类取决于后续使用方式，不取决于扩展名。例如对象列表 JSON 属于 `business_records`，需要保持原样编辑的
配置 JSON 属于 `task_domain_files`。

源码、文档和测试文件只有在任务本身确实处理它们时才属于 `task_domain_files`。仅用于说明功能存在的页面、
教程、接口文档或源码不下载。根据种子线索和调研报告中的实际业务选择一种或两种数据形态；二者都不是
固定必备项，也没有数量配额。

## 循环

```text
读取待支持的实体、工具和任务
    ↓
将缺口按共同需要的数据分组，优先选择能同时覆盖最多项目的一组
    ↓
依次尝试最多三个不同来源，成功即停止尝试
    ↓
下载一到数个相关原件到 workspace/raw/<source>/
    ↓
打开实际内容并拒绝错误页、空文件、说明页和重复文件
    ↓
分类为业务主体数据或任务直接操作文件
    ↓
立即更新文件卡
    ↓
检查剩余项目并进入下一轮
```

Agent 不应先搜索大量来源再统一下载和整理。默认连续采集预算为 40 分钟；结束前需要保存文件卡，清理临时
文件并停止未完成的下载进程。一个来源失败后立即更换来源，不重复请求同一个失败地址；三个不同来源均失败时，
把对应项目、URL 和原因写入最终摘要，然后继续下一组缺口。

## 认证

Agent 可以使用运行环境预先配置的只读账号、官方 CLI 或 API 凭据，但不能打印、复制或保存 Token、Cookie
和密码。它不能交互登录、注册账号或邮箱、处理验证码或 MFA，也不能代替用户接受许可证或服务条款。

遇到 `401`、`403` 或登录页时，应区分未提供凭据、权限不足和限流，并继续检查官方公开 API、静态导出、
发布归档或可信公开镜像。一个受限端点不能直接终止整个采集。最终仍无法取得的数据及具体限制写入结果摘要。

当前流水线不会创建账号或申请权限；GitHub、Hugging Face、Kaggle 等凭据需要在运行前由操作者安全配置。
DataGen 启动时会自动读取已被 Git 忽略的 `config/api_keys.env`。首次使用时执行：

```bash
cp config/api_keys.env.example config/api_keys.env
chmod 600 config/api_keys.env
```

然后只在本地文件中填写需要的平台：

```dotenv
GH_TOKEN=github_read_only_token
HF_TOKEN=huggingface_read_only_token
KAGGLE_API_TOKEN=kaggle_api_token
```

已经存在的系统环境变量优先于文件配置。不要把密钥写进 Prompt、Seed、生成目录或命令参数。建议每个平台
使用人工建立的专用服务账号，完成必要的邮箱验证、MFA 和许可证确认后，只授予下载所需的读取权限。

## 文件卡

每接受一个文件，Agent 就更新 `.datagen/collection_result.json`：

```json
{
  "schema_version": "1.0",
  "result": "collecting",
  "summary": "当前已取得的数据和仍缺少的内容",
  "file_cards": [
    {
      "path": "raw/example/items.json",
      "url": "https://example.org/items.json",
      "source_id": "example",
      "role": "business_records",
      "name": "业务对象记录",
      "summary": "包含对象 ID、状态以及与其他对象的关联 ID。",
      "subjects": [
        {
          "subject_type": "tool",
          "subject_name": "list_items",
          "status": "supported",
          "reason": "包含列举和筛选对象所需的 ID、状态和分类字段。"
        }
      ],
      "limitations": []
    }
  ]
}
```

`subject_type` 只能是 `entity`、`tool` 或 `task`，名称必须原样取自输入。`supported` 表示数据包含完成对应
操作所需的对象、状态、ID 和关键字段；`partial` 表示内容相关但数据不足，并且不计入覆盖率。

写操作不要求在真实网站上执行。业务数据只要提供可修改对象的初始状态、稳定 ID 和必要字段，就可以支持
离线环境中的状态变化。同一个文件可以支持多个项目，但每项都必须给出具体的数据依据。

## 完成条件

Python 按项目名称去重，只计算 `supported`：

- 初始参考工具和任务至少覆盖 90%；
- 调研确认的实体、工具和任务至少覆盖 75%。

低于任一最低线时继续采集。只有剩余数据组都已成功取得数据，或已经分别记录三个失败来源，才以 `partial`
结束。达到两条最低线后再检查一次未覆盖项目；存在明确可取得的新业务数据就继续，否则以 `ready` 结束。
完全没有可用数据时使用 `insufficient_data`。

## Python 收尾

Agent 结束后，Python：

- 确认 Raw 文件与文件卡一一对应；
- 按 URL 和 SHA-256 拒绝重复文件；
- 补充大小、格式、记录数或归档成员数；
- 根据文件卡重新计算覆盖率；
- 阻止低于最低线的结果标为 `ready`；
- 生成来源清单、下载收据和 `collection_profile.json`。

Python 当前只在 Agent 结束后计算精确覆盖率，不会在一次采集会话中主动发回增量缺口；这仍是后续需要改进
的循环控制问题。
