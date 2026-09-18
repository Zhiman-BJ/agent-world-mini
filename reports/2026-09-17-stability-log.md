# 网络参考与三环境试跑：故障记录

目标尚未完成。先完成带有效网络参考的 Step3，再合并 main 并跑三个未跑环境。

## 2026-09-17 前置检查

- 搜索质量探针返回 503，未获得资料；不能用没有参考的成功执行替代验收。
- 上次抓包脚本错误地把 SSE 流按 application/json 转发。已修正诊断转发以保留 Content-Type、逐段转发，且不伪造搜索错误。历史事件 query 为空不能证明模型发出空关键词；原结论撤回。
- 修正后的诊断仍发生推理流断开、自动重连，当前尚未获得真实搜索请求体。
- GitHub 固定 SSH agent socket 不存在。已启动 agent：/run/user/1006/codex-github-agent.sock；仍需用户解锁私钥。未把拉取完成当作事实。
- 本地 main 已到 4c46944：只在 execution_prompt 的 json.dumps 添加 separators=(',', ':')，是序列化压缩，不改变 JSON 解析规则。尚未合并。
- 新环境采用 environment/ 与 tools/ 分目录，现有 Step0 要求同目录 environment.json、tools.json 和 tool_generation/tool_validation.json；后续需准备独立运行包，保留上游原件。
- klayout 的 tools/tools.json 权限 600，所有者 sunhenghui，当前用户无法读取。未修改其权限。
- 新版本 atomate2_semiconductor_defects、doped_semiconductor_defects_001、pymatgen_semiconductor_core_4 的 environment.json、tools.json、tool_validation.json 可读取。此前运行记录未出现这三个包名；正式选择前还需对 environment_id 核验历史。

以上均为前置或诊断问题，尚未启动三个环境的正式流程。

## 阻塞复核

修正转发后的诊断会话最终在 240 秒超时，未捕获 /alpha/search 请求体；已确认诊断进程结束，不存在仍需等待的后台试跑。SSH agent 再次检查仍返回 The agent has no identities。同一组阻塞已连续出现三轮，目标标为 blocked，未标完成。需要解锁 GitHub 私钥，以及搜索服务恢复或取得可诊断其失败原因的服务端信息，才能继续完成所要求的搜索验收、远端合并与三环境正式运行。历史空 query 不能作为非法请求的证据。
