# DataPilot AI 升级交付与验收

本页记录阶段 1–8 的交付快照；推送前审计见[阶段 9](PHASE9_INTEGRATION.md)，真实 Embedding/语义 RAG/生产容器浏览器证据见[阶段 10](PHASE10_EMBEDDING_RAG.md)，固定真实评测与质量问题见[阶段 11](PHASE11_LIVE_EVALUATION.md)，后续预算稳定性与契约验证见[阶段 12](PHASE12_AGENT_RELIABILITY.md)。阶段 12 `ae45190` 已推送且 CI 成功；阶段 13–15 仅本地提交。下文“尚未运行真实服务”指该快照验收时点。

最新证据：[人工复核的检索基线](PHASE13_RAG_BASELINE.md)、[三种检索策略与一次留出对照](PHASE14_RAG_CALIBRATION.md)、[真实浏览器和 LangSmith 云端验收](PHASE15_LIVE_ACCEPTANCE.md)。真实外部服务已跑通，正式留出 SQL 仍为 0/2，未据此宣称质量全面通过；LangSmith 普通运行保持关闭。

[阶段16 SQL整改](PHASE16_SQL_CORRECTNESS.md) 已定位旧超时/别名，独立新题集真实开发严格12/12、冻结留出7/8；唯一失败为仍不支持的HAVING，零执行。新旧题集不可直接比较，旧0/2保持不变；阶段16也仅本地提交。

基线 `a7971cb`，分支 `codex/agent-rag-upgrade`。保留原认证、所有权、CSRF、事务、配额、审计、SQL 安全和界面，未改写用户 .env；升级提交仅在本地，未推送、发布或调用收费服务。各阶段证据见 [AI_UPGRADE](AI_UPGRADE.md)，固定测量见 [EVALUATION](EVALUATION.md)，全部 61 个新增/修改文件见 [逐文件清单](UPGRADE_FILES.md)。

## 实现与数据流

| 能力 | 实际实现 | 安全与职责 |
| --- | --- | --- |
| LangGraph | `backend/app/analysis_graph.py` | StateGraph：准备 → 检索 → 规划 → SQL 生成 → 校验 → 执行 → 结果检查；澄清终止，结构已合格的字段错误最多修复一次再完整校验；无 Checkpointer |
| LangChain Agent | `backend/app/analysis_agent.py` | create_agent + ToolStrategy；五个只读工具按当前用户/数据集重新检查权限；结构化计划和澄清，拒绝任意 SQL/Python/Shell/文件工具 |
| pgvector | `models.py` / `0006_business_knowledge.py` / `infra/Dockerfile.postgres` | PG17 / vector 0.8.7；固定 1536 维，模型/服务空间隔离；HNSW cosine 索引已建立，当前授权候选上精确检索，未声称 ANN 性能 |
| 独立 Embedding | `backend/app/embeddings.py` | none / fake / openai / openai_compatible，独立 Key、严格维度/数值/模型校验；未配置不会伪造向量 |
| RAG | `backend/app/knowledge.py` / `analyses.py` | 结构化知识 → 校验/分块 → 嵌入 → 存储 → 数据库授权过滤 → 距离/top-k/阈值 → 有界上下文；返回真实引用 ID，拒绝编造来源 |
| LangSmith | `backend/app/tracing.py` / `evaluation_cloud.py` | 显式开关和 Key；原始自动追踪关闭，SDK 只导出白名单元数据；另以 --publish-metrics 显式将固定评分上传为可比较实验，禁止内容上传 |
| 评估 | `backend/app/evaluate.py` / `app/evals/` | 29 道固定题，真实查询评分；基础/增强能力开关对照；真实模型双开关，云端评分独立开关，未使用模型自评 |
| 前端 | `knowledge-panel.tsx` / `analysis-workflow.tsx` | 数据集知识 CRUD/重索引/失败提示、执行阶段、调用/修复数、实际引用；沿用现有设计 |

完整图见 [架构](ARCHITECTURE.md)。Next.js 负责交互和同源代理，FastAPI 负责可信执行边界。PostgreSQL 保存业务事务和知识，DuckDB 查询上传数据；模型既没有平台数据库连接，也不能绕过 SQLGlot 和受限子进程。RAG 补充定义，最终数字来自真实 SQL 结果，图表/CSV/总结复用该结果。

认证、会话/改密、owner/CSRF、配额、数据库锁、审计、SQL AST、执行超时/内存/行数/字节限制和结果持久化仍由普通后端负责。提示词无法成为授权策略。SQL 危险/不支持/越权、超时和结果超限都不能经自动重试绕过。

## 环境与新增配置

实测 Python 3.12.14、Node 24.15.0、npm 11.12.1、原生 PostgreSQL 17.10、pgvector 0.8.7。依赖详见锁文件；只因 LangGraph SDK 兼容要求将原 websockets 17.2 调整为 16.1.1，未批量升级旧依赖。新框架 API/隐私说明均核对官方文档与已安装版本。

| 配置 | 默认 / 要求 |
| --- | --- |
| `WORKFLOW_TIMEOUT_SECONDS` | 90 秒，全工作流预算 |
| `SQL_REPAIR_ENABLED` | true，仅限允许恢复的字段错误，最多一次 |
| `AGENT_ENABLED` / `AGENT_NATIVE_TOOLS` | true / false；通用兼容端点需显式工具能力授权 |
| `AGENT_MAX_MODEL_CALLS` / `AGENT_MAX_TOOL_CALLS` | 3 / 4；上限 4 / 6 |
| `AGENT_TOKEN_BUDGET` | 64000，保守字节上界加输出预留；实际用量另记 |
| `EMBEDDING_PROVIDER` | none；演示显式 fake，真实 openai/openai_compatible |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | text-embedding-3-small / 1536，改维度必须迁移和重嵌入 |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` | 独立接口和服务端凭据，默认 OpenAI /v1，不复用聊天 Key |
| `EMBEDDING_TIMEOUT_SECONDS` | 20 秒，受工作流剩余时间约束 |
| `RAG_TOP_K` / `RAG_MIN_SIMILARITY` | 4 / 0.20；Fake 演示使用 0.05，真实阈值需评估 |
| `KNOWLEDGE_REQUESTS_PER_WINDOW` | 10；每账户最多 100 个文档，每份 16000 字符/32768 字节 |
| `DATAPILOT_LANGSMITH_ENABLED` | false；还需独立 LANGSMITH_API_KEY/PROJECT/ENDPOINT；[隐私边界](LANGSMITH.md) |
| `RUN_LIVE_LLM` / `RUN_LIVE_AGENT` / `RUN_LIVE_EMBEDDING` | 各自设 1 才开启对应真实专项 |
| `RUN_LIVE_EVAL` | 还需 CLI `--live`，真实统一评估会产生费用 |

## 实际验证

所有普通回归强制 `LLM_PROVIDER=mock`、三个 RUN_LIVE 开关为 0、外部追踪关闭。数据库使用独立 `datapilot_test`，不清空业务库。

| 命令 | 实际结果 |
| --- | --- |
| `.venv/Scripts/python -m pytest backend/tests -q`（TEST_DATABASE_URL / TEST_POSTGRES_URL 指向 PG 测试库） | **328 passed / 3 skipped**；三项为显式真实 API 专项 |
| 同一 pytest，清除两个 TEST 数据库变量 | **310 passed / 21 skipped**；额外 18 项需要真实 PG/迁移/向量，不拿 SQLite 冒充 |
| `python -m ruff check backend` / `ruff format --check backend` | 通过 |
| `python -m pip check` | 无依赖冲突 |
| `alembic -c backend/alembic.ini check` | 无模型与迁移差异 |
| `test_vector_migration_round_trip_preserves_platform_rows` | 临时库 0005 → 0006 → 0005 → 0006 通过，旧用户保留，回退保留共享 vector 扩展 |
| `npm run lint` / `npm run build` | 通过 ESLint、TypeScript 和生产构建 |
| `npm run test:e2e` | **13 passed**，真实 Edge / HTTP / PG / DuckDB；包括知识 CRUD、来源显示、390px 布局、澄清、历史、导出、认证/改密/会话/限额 |
| `python -m app.evaluate` | 58 次两模式分析，增强 29/29 规则通过；[结果和代价](EVALUATION.md) |
| `python -m py_compile infra/compose-smoke.py` | 通过 |
| `docker compose config --quiet`（默认及隔离配置） | 通过；实际镜像/健康冒烟状态在下方记录 |
| 最终 Linux 后端镜像内 SQL/Graph/执行器/Embedding/追踪/展示/评分测试 | **121 passed / 1 skipped / 1 deselected**；跳过真实 Embedding，不重跑已在 PG 全套通过的独立数据库评估；Linux `pip check` 通过 |

Docker 三个镜像均实际构建：PG17 Alpine + 官方 pgvector 源码编译、Python 3.12 后端、Node24/Next standalone 前端。首次 Docker Hub 认证与 HTTP Debian 软件源失败，使用官方 ECR 镜像源与 HTTPS Debian 源后成功；未改 Docker 全局设置或用户 .env。长构建的源码快照缺少最后一条 Embedding 校验，Linux 测试如实检出；随后利用依赖缓存重建并重测通过，最终加入评分合同后 121 项通过。

隔离项目 `datapilot-upgrade-e2e` 的 postgres/backend/frontend 健康，migrate 正常退出。实际执行 `docker compose --env-file .env.example up --no-build --wait --wait-timeout 300` 与 `python infra/compose-smoke.py`，健康/上传/真实分组查询、Agent 工具、pgvector 知识 CRUD、RAG 真实引用与 SUM=60、密码修改及会话撤销全部 PASS。测试全程强制 Mock/Fake、外部追踪关闭，不将容器验收等同于真实模型验收。

最终又以 `E2E_BASE_URL=http://127.0.0.1:13000`、`DATABASE_URL` 指向隔离 PG15432 运行同一套 Playwright：生产容器 **13 passed**（1.0 分钟）。测试端口显式注册可信 Origin，浏览器登录/CSRF/全部知识与分析交互通过。容器实际查询 vector 扩展为 **0.8.7**，Alembic 为 **0006**。

新增安全证据覆盖：危险/多语句/未知表列/文件访问、危险修复、查询超时/字节超限、每次工具授权、工具/模型/Token 预算、跨用户/跨数据集候选隔离、注入知识仍需 AST、假引用拒绝、别名冲突澄清、缺 Embedding/维度错误、索引去重/更新/删除竞态、CSRF 与 owner 覆写拒绝、LangSmith SDK 最终载荷和失败隔离。原有测试保留且重跑。

原始固定题和 JSON 报告是合成且无凭据的数据。真实 LLM、Agent 工具调用、Embedding、LangSmith 云端尚未在本升级中运行；历史 DeepSeek 单题不能替代它们。升级未推送，因此 GitHub CI 当前只更新配置，未运行新提交。

LangSmith 外部实验上传另有 6 项 HTTP 合同/隐私回归，核对同一固定数据集的两组实验、稳定用例 ID、实际时间戳、空内容、数值评分、缺配置/非法载荷不联网与失败不自动重试；只在 --publish-metrics 和服务端开关/Key 同时满足时发送，未实际连接云端。

## 自己验证

按根 README 安装、迁移和创建账户。离线可在启动 API 的终端设置以下变量，保持用户 .env 不变：

```powershell
$env:LLM_PROVIDER='mock'
$env:EMBEDDING_PROVIDER='fake'
$env:RAG_MIN_SIMILARITY='0.05'
$env:DATAPILOT_LANGSMITH_ENABLED='false'
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
# 另开终端：cd frontend; npm run dev
```

已有 8000 进程时先停止自己的旧服务，不能重复绑定。打开 http://localhost:3000，上传 `backend/app/evals/sales.csv`；新增字段别名 `{"alias":"销售额","field":"revenue"}`，问“销售额总和”应为 60 并显示真实来源。改别名字段为 cost 后实际总和为 27，刷新历史能看到两次各自的快照。问“分析一下”先澄清；删除知识后新检索不再使用它。Fake 模型仅支持明确模板，复杂指标需真实模型，不能将 fixture 评估能力冒充 UI Mock 支持范围。

Docker 隔离栈的全部命令与官方镜像源选择见 [OPERATIONS](OPERATIONS.md)。正常栈使用 5432/8000/3000；隔离验收用 15432/18000/13000，不读开发者 .env，不影响已有开发服务。真实 chat/Embedding 接入见 [REAL_MODEL](REAL_MODEL.md)，LangSmith 需另外授权配置。

## 限制与面试要点

- 真实语义效果、真实工具兼容性、真实模型 Token/金额、并发容量尚未测量。离线 100% 只证明固定受控题，不是自然语言泛化准确率。Embedding 响应必须明确返回匹配的 model，缺失时也拒绝，避免默认假定其语义空间。
- 无 Checkpointer、多自主 Agent、组织共享或 ANN 性能承诺；当前知识 API 只创建数据集范围文档，模型支持的 personal 范围预留给受控运营，不向任意用户开放全局写入。
- 凭据正则不是完整 PII 去敏。人工知识应先授权去敏；删除文档影响后续检索，历史中的有界引用按历史保留。索引请求中断可手动重索引，暂无后台任务队列。
- LangSmith 元数据受限，不能云端重放原问题；后台追踪在进程退出时可能丢失，因此审计仍保存在 PostgreSQL。
- 面试重点：Graph 与 Agent 的控制权差别、SQL 安全为何独立于模型、先授权再距离排序、语义空间与维度管理、版本号如何解决索引竞态、如何用真实结果评分、增加 Agent 的调用与延迟成本。

## 阶段提交

1. `4303eb2` 基线与依赖兼容
2. `2232116` LangGraph 与有限修复
3. `a98c1ad` 受控 LangChain Agent
4. `55987cb` pgvector / Embedding / 迁移
5. `f4994a9` 知识管理与 RAG 引用
6. `4baceaf` LangSmith 元数据追踪
7. `3233a6e` 固定结果评估与对照
8. 最终回归/部署/文档提交见本文件所在阶段的 `git log -1 --oneline`。
