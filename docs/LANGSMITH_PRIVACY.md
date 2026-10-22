# LangSmith 配置、隐私与真实证据

阶段 15 已从云端读回真实成功与受控失败追踪，以及 22 条标签/运行的评测实验，见[验收报告](PHASE15_LIVE_ACCEPTANCE.md)。这是显式构造元数据的追踪，不上传 LangChain/LangGraph 的原始自动追踪。

本机私有 `.env`（已被 Git 忽略）：

```dotenv
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=datapilot-rag-acceptance
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
DATAPILOT_LANGSMITH_ENABLED=false
```

Key 在 [LangSmith 官方设置](https://docs.langchain.com/langsmith/create-account-api-key) 创建；云端回读和实验需要有读取/评测权限的凭据，仅写入的 Tracing Only Key 不够。Key 只填本机，不能放入前端、截图、命令参数或 Git。地区端点以自己的 Workspace 为准，本轮实际验证的是上面的端点。

普通运行保持 false。确需追踪时，只在目标服务进程设置 `DATAPILOT_LANGSMITH_ENABLED=true` 并重启后端；单独设置 `LANGSMITH_TRACING=true` 不会导出原始图状态。该项目直接调用 `trace_payload` 创建新字典，输入/输出均为空；SDK 的 hide_inputs/outputs、metadata 白名单和 omit_traced_runtime_info 再过滤一次。字段只有节点/工具名、固定模型枚举、状态、有界计数、实测耗时/Token，以及用每条 trace 的随机 UUID 加盐的来源哈希，不含原始 source ID、Schema、提示词、SQL、知识正文、结果行、文件、用户 ID 或环境值。

发送使用容量 8 的后台队列、5 秒连接/5 秒读取时限、零网络自动重试；异常不能改变分析结果。独立验收工具使用 5/10 秒超时，失败只保存阶段和异常类型，不保存 SDK 异常正文。日志的 `local_trace_id` 只是本地待投递 ID，发送状态 success 也不能替代服务器回读证据。

LangSmith 在存储后额外加入 `ls_run_depth`。本轮逐节点验证是 0/1/2，回读只接受与实际树层级一致的整数；上传白名单并不因此允许任意额外字段。正文不为空、出现 runtime/environment、陌生 metadata 或层级不符都拒绝验收。隐私差异未核清时先关闭临时服务追踪，再继续只读检查。

## 显式预览与上传

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python -m app.langsmith_acceptance --report docs/rag-development-selected.json --preview data/runtime/cloud-preview-new.json
```

这一步只读本地报告并生成清单，不联网。检查清单只有固定合成 sample_id、数量标签、分数、模型枚举、配置和源码摘要。随后才设置 `RUN_LIVE_LANGSMITH=1` 并加 `--live`。默认工具运行独立 `_test` 数据库的一条真实合成分析；Windows 的重型 CLI 入口曾触发包含启动时间的 5 秒查询上限，因此本轮最终使用独立 Linux 服务的真实浏览器证据。没有增加 SQL 时限，也没有重跑正式留出分数。

真实浏览器使用独立测试数据库、真实模型/Embedding 和显式应用追踪。`RUN_LIVE_BROWSER_RAG=1` 时运行 `npm run test:e2e -- e2e/live-rag.spec.ts`；设置新的 `E2E_EVIDENCE_PREFIX` 保存本轮文件，避免覆盖阶段 10 证据。登录测试的 `DATABASE_URL` 必须与浏览器后端一致。实际服务日志给出 local_trace_id 后：

```powershell
$env:RUN_LIVE_LANGSMITH='1'
.venv/Scripts/python -m app.langsmith_acceptance --report docs/rag-development-selected.json --preview data/runtime/cloud-preview-new.json --live --verify-run <实际local_trace_id> --browser-evidence data/runtime/<本轮前缀>-real-browser.json --publish-experiment --verify-rejection --output data/runtime/cloud-receipt-new.json
```

`--verify-run` 只读该真实服务记录，不从 UI 耗时重建或伪造 spans。`--publish-experiment` 是唯一显式创建实验的开关；读取预览必须与当前报告一致。`--verify-rejection` 另发送一个本地受控 DROP 提案被 SQLGlot 拒绝的真实图轨迹，明确标为 Mock/受控提案，零执行；它不是“真实模型生成危险 SQL”的证明。

云端数据集只含固定 sample_id 与数量/空知识标签；实验元数据绑定语料、父提交、dirty 状态、实际源码 SHA、配置、Embedding/模型和提示词版本。之后可用 `--verify-experiment-receipt <已保存receipt.json>` 只读核对项目元数据、逐条标签与反馈，避免再次上传。云端数值反馈实际保留四位小数，工具同时允许原精度或明示的四位量化表示，错误值仍拒绝；本地 JSON 全精度评分始终是权威。

普通 CI 不调用该 CLI 的 live 分支。需要清理云端实验时由用户在 LangSmith 控制台管理，本次不会自动删除验收证据或公开分享追踪。

阶段18新增候选诊断只来自授权候选：最多5阶段、每阶段16条；云端只传每trace加盐来源哈希、排名与百万分比整数分数、配置哈希和枚举错误码/契约状态，不传知识/SQL/稳定ID。真实retriever失败与云端回读见[阶段18](PHASE18_RAG.md)。

## 阶段23本地合同增量

新分析的 `rag.evidence.version=1` 区分初始候选、登记目录/工具取得、适用指标、计划选择、传入 SQL Provider、执行后确认使用。导出合同仅增加固定状态枚举、有界数量（0–96）和每阶段最多8个来源哈希；不直接导出 evidence 对象或本地来源快照。缺少的历史字段保持未知，不补成0。SQL AST 校验、指标契约、DuckDB执行和最终决策分别记录，不能相互替代。

阶段23起，所有新 trace 的候选与来源标识统一使用 HMAC-SHA256，密钥为每 trace 新生成的32字节秘密随机值，仅在构造 payload 时短暂持有，不序列化或持久化。公开的 trace UUID 不作为密钥；同一 trace 内可关联，不提供跨 trace 稳定映射。这减少低熵ID枚举风险，不构成对全部行为元数据的绝对匿名化保证，也不改写历史云端记录。

继续只允许固定节点、工具名称、配置哈希、状态、错误分类、数量、Token与耗时；root/child 的 inputs、outputs为空。原始问题、知识正文、SQL、结果行、Schema、用户身份、原始文档ID、Cookie、凭据、地址及未授权工具参数均不得导出。应用日志的 local_trace_id 关联本次随机 trace；该标识本身不证明云端已接收。

本轮只在本地调用 payload 构造函数并序列化扫描，验证越界数量、布尔冒充整数、陌生字段、正文注入、非法哈希和新旧字段兼容；没有LangSmith上传或读回。真实聊天、Embedding和云端追踪全部保持关闭，阶段21未知usage护栏不变。具体命令与结果见[阶段23报告](PHASE23_EVIDENCE_OBSERVABILITY.md)，不得作为新的云端验收证据。
