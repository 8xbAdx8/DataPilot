# DataPilot

基于自然语言的 AI 数据分析平台。Next.js 展示数据，FastAPI 执行安全策略，PostgreSQL 保存业务元信息，DuckDB 查询上传的数据。

## 开发状态

已跑通完整 Mock 闭环：CSV/XLSX → DuckDB → 字段画像 → 自然语言生成 SQL → AST 安全校验 → 真实查询 → 表格/ECharts/事实总结 → 历史搜索与 CSV 导出。PostgreSQL 保存业务元信息，提供删除与保留清理。GitHub Actions 的应用检查和 Linux Docker Compose 全栈冒烟均已通过；真实 LLM API 仍待配置凭据后验收。

文档：[实施计划](docs/PLAN.md) · [架构与学习入口](docs/ARCHITECTURE.md) · [安全策略](docs/SECURITY.md) · [运行与保留策略](docs/OPERATIONS.md) · [演示与验收](docs/DEMO.md) · [实际开发记录](docs/DEVELOPMENT.md)。

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
..\.venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
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

默认 `LLM_PROVIDER=mock`，无需 API Key。真实模型调用将在对应阶段单独记录；不要把密钥放进前端或提交到 Git。本项目当前面向本机单用户演示，鉴权和所有权校验属于后续阶段。

## 安全分析 API

`POST /api/datasets/{id}/analyses`，JSON 为 `{"question":"按 region 统计 revenue 总和"}`。Mock 还支持“这个数据集有多少行？”、“预览数据”、“revenue 平均值”。它只支持明确模板；复杂问题会返回错误。生成 SQL 必须通过 [安全策略](docs/SECURITY.md)，实际结果由 DuckDB 计算。

`GET /api/analyses?dataset_id={id}` 返回历史元信息；`GET /api/analyses/{id}` 读取有界结果快照；`GET /api/analyses/{id}/export` 导出相同结果的 CSV。超限、失败和安全拒绝都不会生成虚假的成功结果。

历史列表支持组合参数：`q` 搜索问题或 SQL（去除首尾空格、最多 200 字符、英文不区分大小写，`%` / `_` 按文字匹配），`status` 为 `running` / `success` / `rejected` / `error`，`limit` 为 1–100（默认 20），`offset` 为非负整数。界面每页 20 条，可搜索、筛选、翻页和重置；只请求元信息，不批量加载结果快照。

真实模型：在根目录 `.env` 设置 `LLM_PROVIDER=deepseek`（也支持 `openai`、`openai_compatible`）、`LLM_MODEL`、`LLM_BASE_URL` 和 `LLM_API_KEY` 后重启后端。DeepSeek 的 Base URL 使用 `https://api.deepseek.com`，OpenAI 使用 `https://api.openai.com/v1`。后端会追加 `/chat/completions`。真实模式缺 Key 返回 503，不会降级 Mock。

显式启用真实 API 测试（会消耗配置服务的 Token）：设置 `$env:RUN_LIVE_LLM='1'` 后运行 `.\.venv\Scripts\python -m pytest backend/tests -m live_llm`。默认跳过；HTTP MockTransport 合同测试不能替代真实 API 验证。

PostgreSQL 专项：设置 `$env:TEST_DATABASE_URL` 和 `$env:TEST_POSTGRES_URL` 指向已迁移的开发测试数据库，再运行同一套 pytest。测试事务回滚，但不要把这些变量指向生产数据库。

Docker 一键启动命令、当前 Windows 引擎阻塞、保留清理、原生 PostgreSQL 启停与端口说明见 [运行文档](docs/OPERATIONS.md)。[首轮 CI 验收记录](https://github.com/8xbAdx8/DataPilot/actions/runs/39051736631) 包含应用检查和独立 Compose 冒烟成功结果；[后续运行](https://github.com/8xbAdx8/DataPilot/actions) 按提交分别查看。

## 上传约定

- 打开 <http://localhost:3000>，选择 `data/examples/sales.csv` 体验真实导入。
- CSV 使用 UTF-8（支持 BOM），支持逗号、分号、制表符、竖线分隔；表头必填且不重复，数据行字段数须一致。保留完整字段的全空记录，跳过空白行；仅有表头不接受。
- XLSX 要求有效 OOXML，多工作表需选择；不接受公式、宏、外部链接；解压总量最多 50 MiB / 1000 条目。
- 默认最多 10 MiB / 100,000 行 / 100 列；空白视为缺失，混合类型保留文本，前导零代码保留文本；不会执行单元格内容。
- 原文件不保留；DuckDB 使用服务端 UUID 路径，PostgreSQL 仅保存字段画像与最多 20 行预览（长文本截断为 500 字符）。
- 就绪健康检查、上传、列表和详情 API 可在 `/docs` 查看。浏览器通过 Next.js 同源代理连接后端，服务端地址由 `API_INTERNAL_URL` 配置。
