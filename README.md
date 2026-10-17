# DataPilot

基于自然语言的 AI 数据分析平台。Next.js 展示数据，FastAPI 执行安全策略，PostgreSQL 保存业务元信息，DuckDB 查询上传的数据。

## 开发状态

按可验证的垂直切片开发，见 [实施计划](docs/PLAN.md) 和 [开发记录](docs/DEVELOPMENT.md)。已跑通：真实 PostgreSQL 元信息、CSV/XLSX → DuckDB → 画像 → Next.js 展示。正在接入安全 Text-to-SQL。真实 LLM API 和 Docker 部署尚未验证。

## 本地启动（PowerShell）

需要 Python **3.12**、Node.js 22/24 和运行中的 Docker Desktop。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
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

API 文档：<http://localhost:8000/docs>。存活检查 `/api/health/live` 不依赖数据库；就绪检查 `/api/health/ready` 检查 PostgreSQL 和迁移后的表，失败返回 503。

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

## 上传约定

- 打开 <http://localhost:3000>，选择 `data/examples/sales.csv` 体验真实导入。
- CSV 使用 UTF-8（支持 BOM），支持逗号、分号、制表符、竖线分隔；表头必填且不重复，数据行字段数须一致。仅有表头不接受。
- XLSX 要求有效 OOXML，多工作表需选择；不接受公式、宏、外部链接；解压总量最多 50 MiB / 1000 条目。
- 默认最多 10 MiB / 100,000 行 / 100 列；空白视为缺失，混合类型保留文本，前导零代码保留文本；不会执行单元格内容。
- 原文件不保留；DuckDB 使用服务端 UUID 路径，PostgreSQL 仅保存字段画像与最多 20 行预览（长文本截断为 500 字符）。
- 就绪健康检查、上传、列表和详情 API 可在 `/docs` 查看。浏览器通过 Next.js 同源代理连接后端，服务端地址由 `API_INTERNAL_URL` 配置。
