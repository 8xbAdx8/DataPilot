# 可选 LangSmith 追踪与隐私

实现：`backend/app/tracing.py`。默认 `DATAPILOT_LANGSMITH_ENABLED=false`，即使 shell 设置 `LANGSMITH_TRACING=true`，Graph 和 Agent 仍用 `tracing_context(enabled=False)` 禁止自动导出原始输入输出。

显式启用需同时设置：

```dotenv
DATAPILOT_LANGSMITH_ENABLED=true
LANGSMITH_API_KEY=<仅填服务端环境>
LANGSMITH_PROJECT=datapilot-metadata
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

Key 缺失时仍可分析，无外部请求。项目名称限定 64 位英文数字/下划线/连字符，地址限定无凭据和查询参数的 HTTPS。不要把个人信息写入项目名。没有“全量内容追踪”开关；若未来添加，必须独立授权和隐私设计。

一次分析结束并持久化后，将新建随机 trace ID 的父记录、实际 Graph 节点和实际 Agent 模型/工具调用以 `Client.batch_ingest_runs` 导出。工具和模型 span 嵌套在 plan 节点下，有限修复会出现第二次 validate。时间取真实进入/离开时间；检索记录状态、命中数、是否提供给 SQL 生成；模型记录调用数及已报告 Token 数。未知 Token 不填写，也不估算金额。

导出只允许固定状态、固定节点/工具名及非负整数计数。原始问题、Schema/字段名、SQL、知识内容和 ID、文件路径、行数据、模型完整输入输出、账号和业务请求 ID、完整异常均不上传。输入输出强制 `{}`；Client 的 `hide_inputs`/`hide_outputs`/`hide_metadata` 再次白名单过滤，`omit_traced_runtime_info=True` 防止 SDK 追加环境或 Git 信息；显式固定单一目的地址，不沿用 shell 的多目标导出配置。SDK 日志删除远程异常正文。

导出在 daemon 后台单线程中执行，队列容量 8，满时丢弃并记录 `langsmith_export/status=dropped`；网络 connect/read 超时分别 0.5/1 秒。SDK batch 可能执行最多 3 次传输尝试；应用不重试分析或 SQL。退出进程可能丢弃未发送追踪，因此它不替代 PostgreSQL 审计。远端服务接收非敏感性能元数据和时间戳后，其保留政策由相应 LangSmith 工作区配置决定。

测试 `test_tracing.py` 拦截 SDK 的最终 HTTP 请求体，验证白名单、嵌套、禁用状态、缺少 Key、队列满和异常不影响结果。没有连接真实 LangSmith 云端，外部看板及服务接收状态未验证。追踪不包含内容，因此只能诊断路径、调用和延迟，不能在云端重放原始用户问题。

官方依据（2026-10-20 核对，SDK 0.14.11）：[隐私控制](https://docs.langchain.com/langsmith/mask-inputs-outputs)、[显式 tracing_context](https://docs.langchain.com/langsmith/trace-without-env-vars)、已安装 SDK 的 Client/batch_ingest_runs API。SDK 参数采用 `tracing_mode="langsmith"`，未使用已弃用的 `otel_enabled`。
