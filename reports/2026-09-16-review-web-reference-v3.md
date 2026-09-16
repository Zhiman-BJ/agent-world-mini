# 强制搜索、最多三次：试跑结果

Prompt 提交 `ab1140b`，仅修改网络段落：实际调用至少一次、最多三次，失败后模型重新调用也计入。上限是 prompt 要求，没有增加代码强制计数器，不限制底层 HTTP 自动重试。

复用 v2 的 OpenZeppelin task10 Step2 输入、相同初态（签名一致）、sol/low、随机种子42，搜索开启。只运行 Step3。

## 实际结果

- 实际 web_search 调用 **2 次**，没有超过上限。这次模型明确表示先搜索真实场景，并确实执行。
- 两次均失败。stderr 在 2026-09-16T15:39:48Z 与 15:42:35Z 记录 HTTP 502 / Upstream service temporarily unavailable。未取得有效网络参考，无法评价参考资料质量。
- 模型随后明确说明：“公开搜索连续两次遇到上游 502，我会保留这一限制，并以环境中的真实治理记录完成审阅。”
- 方案抽样选择纯治理核验；完成 21 次成功业务调用，重算计票，导出 Markdown、JSON、CSV 三份审阅产物，工具均返回成功。另有 4 次方案选择调用，其中 3 次参数格式错误。
- **整轮失败**：尚未输出最终 JSON 时达到原有 1200 秒超时，程序记录 success=false。不能把文件已导出当作最终任务已成功生成。
- 阶段耗时 1200.309 秒，未收到 turn.completed，因此 usage 为空；不能据此视为零 token 或估算准确成本。
- 本轮没有进入 Step4/5/verifier，没有最终任务文本。未为了这次试验提高超时或改动其他流程。

本轮验证了明确措辞下模型实际进行了网络搜索、未超过三次；没有验证搜索结果对任务真实性的提升。由于 provider 配置和提示词也经历过修改，不能把跨轮行为变化全部归因于一句措辞。

## 产物

- [完整调用记录与超时结果](../runs/review_web_reference_v3/20260916_233323_874016_smithery_openzeppelin_25_gpt-5.6-sol/tasks/task10/agent_result.json)
- [实际 prompt、事件与错误日志](../runs/review_web_reference_v3/20260916_233323_874016_smithery_openzeppelin_25_gpt-5.6-sol/llm_calls.jsonl)
- [运行配置与耗时](../runs/review_web_reference_v3/20260916_233323_874016_smithery_openzeppelin_25_gpt-5.6-sol/run.json)

修改经过 Python 语法检查和 git diff --check。
