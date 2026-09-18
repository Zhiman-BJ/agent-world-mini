# 网络搜索 HTTP 诊断

使用管线专用 provider 和已有专用认证，不修改当前对话认证。不记录密钥或 Authorization 头。

## 实测

1. 在本地启动诊断转发，运行原 `_ReviewClient`，保留 SSE Content-Type 并按块转发，关闭诊断请求的自动重试。
2. 捕获 Codex 的 `POST /responses`，请求 53508 字节，未压缩；服务端在约 0.43 秒返回 HTTP 403：

```json
{"code":"INSUFFICIENT_BALANCE","message":"Insufficient account balance"}
```

3. 单独以相同认证请求 `POST /alpha/search`，使用明确非空查询词 `Snapshot official voting power definition`，约 0.46 秒收到相同 HTTP 403 和错误正文。此探测不是模型生成的原始请求，不能证明历史搜索参数格式正确；可以确认当前搜索端点也受余额阻塞。

## 结论与限制

当前确定的阻塞是专用服务账号余额不足，发生在推理入口及搜索入口；不是业务工具 Bubblewrap 的网络隔离造成。
历史 502/503 只返回上游暂不可用，不能从今天的 403 倒推出历史错误同样由余额引起。
历史 CLI 事件 `query="", action=other` 不是原始 HTTP 请求体，不能据此认定模型生成空查询。
当前认证不能完成真实模型调用，因此本轮没有原始模型搜索请求体或有效网页结果。需恢复专用账号额度后再继续此项诊断与 Step3 验收。

诊断日志：`runs/search_wire_probe/logs/run_01/`。本轮没有启动环境全流程。
