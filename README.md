# DataPilot

基于自然语言的 AI 数据分析平台。Next.js 展示数据，FastAPI 执行安全策略，PostgreSQL 保存业务元信息，DuckDB 查询上传的数据。

## 开发状态

AI 升级已实现：**LangGraph** 编排检索/规划/生成/校验/执行与最多一次字段修复；**LangChain Agent** 在限定的只读工具中选择 Schema、画像、业务知识、指标口径与澄清；**pgvector + 独立 Embedding** 保存并按用户/数据集过滤知识；**RAG** 将实际来源提供给 SQL 生成并在界面展示引用；**LangSmith** 默认关闭，只在显式启用后导出白名单性能元数据。详细交付和当前验证状态见 [升级验收](docs/UPGRADE_ACCEPTANCE.md)、[阶段记录](docs/AI_UPGRADE.md)、[评估报告](docs/EVALUATION.md) 和 [追踪隐私](docs/LANGSMITH.md)。

升级分支 `codex/agent-rag-upgrade` 已推送至阶段 11 提交 `702c48e`，[对应 GitHub Actions](https://github.com/8xbAdx8/DataPilot/actions/runs/39439653579) 成功。[阶段 10 真实验收](docs/PHASE10_EMBEDDING_RAG.md)跑通百炼 1536 维向量 → pgvector → DeepSeek 原生 Agent → LangGraph → SQL 安全校验 → DuckDB → 生产容器浏览器图表、来源、历史和导出；合成净贡献额为 18，分组 East=6、West=12。

[阶段 11 固定真实评测](docs/PHASE11_LIVE_EVALUATION.md)已实际执行基础/增强各 29 题，保留[逐题证据](docs/evaluation-live.json)。严格结果符合率为 1/13 与 0/13，包含列别名/排序契约差异，也发现 Agent 工具预算、无关召回与澄清问题；不能宣称真实质量验收通过。

[阶段 12](docs/PHASE12_AGENT_RELIABILITY.md)增加 Agent 预算内收敛、工具批次预检和澄清 Token 分项，新增明确输出契约的 v2 开发/验证题集。真实增强模式开发 6/6、验证 6/6 规则通过，验证中 5 道计算题严格匹配，另 1 道正确澄清；[原始记录](docs/evaluation-contracts-v2-live.json)保留基础模式失败。新题集与旧 29 题不直接可比，不能宣称从 0% 提升至 100% 或泛化质量通过。检索阈值未调整、旧误召回风险保留；LangSmith 云端未验证。本阶段新增内容仅本地提交。

无需付费服务即可运行 Mock 分析；`EMBEDDING_PROVIDER=none` 默认禁用知识索引，演示 RAG 须显式使用 `fake`，真实专项与浏览器专项默认关闭。阶段 10 的固定案例不代表自然语言泛化准确率或大规模语义召回率。

已跑通完整 Mock 闭环：账户登录 → CSV/XLSX → DuckDB → 字段画像 → 自然语言生成 SQL → AST 安全校验 → 真实查询 → 表格/ECharts/事实总结 → 历史搜索与 CSV 导出。PostgreSQL 保存账户、会话和业务元信息，后端强制数据集所有权校验，提供删除与保留清理。阶段 10 另外使用真实聊天和 Embedding 服务验证同一执行边界，只发送合成 Schema/问题和人工合成知识。GitHub Actions 的普通回归使用 Mock/Fake，真实专项结果单独记录。

文档：[实施计划](docs/PLAN.md) · [架构与学习入口](docs/ARCHITECTURE.md) · [安全策略](docs/SECURITY.md) · [运行与保留策略](docs/OPERATIONS.md) · [真实模型接入](docs/REAL_MODEL.md) · [配额与执行诊断](docs/OBSERVABILITY.md) · [演示与验收](docs/DEMO.md) · [实际开发记录](docs/DEVELOPMENT.md)。

## 本地启动（PowerShell）

需要 Python **3.12**、Node.js 22/24 和 PostgreSQL（可用 Docker Desktop 或本机实例）。本机默认 Python 为 3.14，本项目实际使用已安装的 3.12.14。

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
py -V:Astral/CPython3.12.14 -m venv .venv # 当前机器的发行版标签
.\.venv\Scripts\python -m pip install -r backend/requirements.lock
.\.venv\Scripts\python -m pip install --no-deps -e './backend[dev]'
docker compose up -d postgres
Set-Location backend
..\.venv\Scripts\alembic upgrade head
..\.venv\Scripts\python -m app.accounts create-user --username alice # 交互输入并确认密码，至少 12 字符
..\.venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
```

另开终端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

普通 CPython 3.12 安装可用 `py -3.12 -m venv .venv`。当前机器的 3.12 属于 Astral 发行版，`py -3.12` 无法解析；上面的完整发行版标签已实际验证。已有本项目 `.venv` 时无需重建。

工作台：<http://localhost:3000>；API 文档：<http://localhost:8000/docs>。存活检查 `/api/health/live` 不依赖数据库；就绪检查 `/api/health/ready` 检查 PostgreSQL 和全部迁移表，失败返回 503。

## 检查

```powershell
.\.venv\Scripts\python -m pytest backend/tests
.\.venv\Scripts\ruff check backend
Set-Location frontend
npm run lint
npm run build
npm run test:e2e # 先启动后端；本机使用 Edge，CI 使用 Playwright Chromium
```

默认 `LLM_PROVIDER=mock`，无需 API Key。真实模型调用将在对应阶段单独记录；不要把密钥放进前端或提交到 Git。工作台需要管理员创建的账户，没有默认账户或公开注册接口。用户名为 3–64 位英文/数字/点/下划线/短横线（首位英文或数字，不区分大小写），密码为 12–128 字符。已存在账户不要重复创建；命令会拒绝覆盖。

登录、退出、24 小时会话与所有权由后端执行。工作台右上角“账户安全”支持修改密码、查看会话、撤销单个会话和退出全部设备；改密会撤销全部旧会话并要求重新登录。忘记密码时由管理员通过 CLI 重置。升级前的旧数据保留但默认不可访问；按[账户与迁移说明](docs/OPERATIONS.md#账户与旧数据归属)显式分配。当前尚无 MFA 或公网部署所需的完整网关防护。

## 安全分析 API

`POST /api/datasets/{id}/analyses`，JSON 为 `{"question":"按 region 统计 revenue 总和"}`。Mock 还支持“这个数据集有多少行？”、“预览数据”、“revenue 平均值”。它只支持明确模板；复杂问题会返回错误。生成 SQL 必须通过 [安全策略](docs/SECURITY.md)，实际结果由 DuckDB 计算。

数据 API 需要登录 Cookie。先向 `POST /api/auth/login` 提交 `username` / `password`，Cookie 由 HttpOnly 响应设置；`GET /api/auth/me` 返回当前用户，`POST /api/auth/logout` 撤销当前会话。所有 `/api` 写请求（包括登录/退出）必须带 `X-DataPilot-CSRF: 1`；浏览器通过内置客户端自动发送。不要把密码放进 URL、命令行参数或前端存储。

`GET /api/auth/sessions` 只列出本账户最多 10 个未过期会话。`POST /api/auth/password` 使用 `current_password` / `new_password`，`POST /api/auth/logout-all` 与 `DELETE /api/auth/sessions/{公开会话ID}` 使用 `current_password`。后端重新确认密码与会话归属，失败次数和登录共享限额；详细操作见[密码与会话管理](docs/OPERATIONS.md#密码与会话管理)。

默认每用户每 60 秒允许 30 次分析、10 次上传，PostgreSQL 原子计数使配额跨 API 进程共享。超限返回 429 和 `Retry-After`，前端显示等待建议，不自动重试。每个响应的 `X-Request-ID` 可关联脱敏 JSON 日志；分析“执行详情”展示真实分段耗时和提供商上报的 Token。未知用量不记为 0，不估算账单费用，详见[诊断说明](docs/OBSERVABILITY.md)。

`GET /api/analyses?dataset_id={id}` 返回历史元信息；`GET /api/analyses/{id}` 读取有界结果快照；`GET /api/analyses/{id}/export` 导出相同结果的 CSV。超限、失败和安全拒绝都不会生成虚假的成功结果。

历史列表支持组合参数：`q` 搜索问题或 SQL（去除首尾空格、最多 200 字符、英文不区分大小写，`%` / `_` 按文字匹配），`status` 为 `running` / `success` / `clarification` / `rejected` / `error`，`limit` 为 1–100（默认 20），`offset` 为非负整数。界面每页 20 条，可搜索、筛选、翻页和重置；只请求元信息，不批量加载结果快照。

真实模型：在根目录 `.env` 设置 `LLM_PROVIDER=deepseek`（也支持 `openai`、`openai_compatible`）、`LLM_MODEL`、`LLM_BASE_URL` 和 `LLM_API_KEY` 后重启后端。DeepSeek 的 Base URL 使用 `https://api.deepseek.com`，OpenAI 使用 `https://api.openai.com/v1`。后端会追加 `/chat/completions`。真实模式缺 Key 返回 503，不会降级 Mock。

DeepSeek 当前示例模型为 `deepseek-flash`，适配器显式关闭其默认思考模式，让 1000 Token 输出预算用于 SQL JSON。其他兼容提供商不收到 DeepSeek 专用参数。按[真实模型接入步骤](docs/REAL_MODEL.md)配置并运行真实 API 专项；模型名以提供商控制台和官方文档为准。

显式启用真实 API 测试（会消耗配置服务的 Token）：设置 `$env:RUN_LIVE_LLM='1'` 后运行 `.\.venv\Scripts\python -m pytest backend/tests -m live_llm`。默认跳过；HTTP MockTransport 合同测试不能替代真实 API 验证。

PostgreSQL 专项：设置 `$env:TEST_DATABASE_URL` 和 `$env:TEST_POSTGRES_URL` 指向已迁移的开发测试数据库，再运行同一套 pytest。测试事务回滚，但不要把这些变量指向生产数据库。

Docker 一键启动命令、隔离验收、软件源网络、保留清理、原生 PostgreSQL 启停与端口说明见 [运行文档](docs/OPERATIONS.md)。[首轮 CI 验收记录](https://github.com/8xbAdx8/DataPilot/actions/runs/39051736631) 包含当时的应用检查和独立 Compose 冒烟成功结果；[后续运行](https://github.com/8xbAdx8/DataPilot/actions) 按提交分别查看，本升级本地结果见验收文档。

## 上传约定

RAG 专项使用已人工复核的 44 条合成查询，开发/留出分开。原始向量开发基线的误召回与严格 SQL 格式失败如实保留，见[阶段 13 报告](docs/PHASE13_RAG_BASELINE.md)；普通测试不调用外部模型。

[阶段 14 对照](docs/PHASE14_RAG_CALIBRATION.md) 将默认语义阈值改为 0.70，保留 vector/none；混合检索、FlashRank 和 Ragas 均可选。留出误召回下降，但漏召回、错误知识采用及严格 SQL 超时仍有失败，不能称为全面通过。安装、锁文件、真实评测与回滚见[评测说明](docs/RAG_EVALUATION.md)。

[阶段 15 验收](docs/PHASE15_LIVE_ACCEPTANCE.md) 已完成真实模型浏览器流程（East=6、West=12），并从 LangSmith 读回成功/受控拒绝追踪及 22 条评测运行。云端只接收白名单元数据，普通运行仍默认关闭，配置见[追踪隐私说明](docs/LANGSMITH_PRIVACY.md)。这条独立流程成功不改变阶段 14 留出 SQL 0/2 的失败结论；新增阶段提交只保留本地，尚未推送。

[阶段 16 SQL 专项](docs/PHASE16_SQL_CORRECTNESS.md) 逐题定位旧超时与别名缺陷，DuckDB 改用轻量隔离子进程，保留包含启动的 5 秒限制。新题集按意图分组、经人工确认，并严格检查空结果列描述、列顺序与行顺序；旧题与历史分数不改。新开发/冻结留出结果、命令和限制均在阶段报告中，本阶段不推送。

- 打开 <http://localhost:3000>，选择 `data/examples/sales.csv` 体验真实导入。
- CSV 使用 UTF-8（支持 BOM），支持逗号、分号、制表符、竖线分隔；表头必填且不重复，数据行字段数须一致。保留完整字段的全空记录，跳过空白行；仅有表头不接受。
- XLSX 要求有效 OOXML，多工作表需选择；不接受公式、宏、外部链接；解压总量最多 50 MiB / 1000 条目。
- 默认最多 10 MiB / 100,000 行 / 100 列；空白视为缺失，混合类型保留文本，前导零代码保留文本；不会执行单元格内容。
- 原文件不保留；DuckDB 使用服务端 UUID 路径，PostgreSQL 仅保存字段画像与最多 20 行预览（长文本截断为 500 字符）。
- 就绪健康检查、上传、列表和详情 API 可在 `/docs` 查看。浏览器通过 Next.js 同源代理连接后端，服务端地址由 `API_INTERNAL_URL` 配置。
