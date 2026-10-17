# DataPilot

基于自然语言的 AI 数据分析平台。Next.js 展示数据，FastAPI 执行安全策略，PostgreSQL 保存业务元信息，DuckDB 查询上传的数据。

## 开发状态

按可验证的垂直切片开发，见 [实施计划](docs/PLAN.md) 和 [开发记录](docs/DEVELOPMENT.md)。当前正在建立健康检查、数据库迁移和前端骨架。尚未声称 Text-to-SQL、真实 API 或 Docker 部署验证完成。

## 本地启动（PowerShell）

需要 Python **3.12**、Node.js 22/24 和运行中的 Docker Desktop。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e './backend[dev]'
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
```

默认 `LLM_PROVIDER=mock`，无需 API Key。真实模型调用将在对应阶段单独记录；不要把密钥放进前端或提交到 Git。本项目当前面向本机单用户演示，鉴权和所有权校验属于后续阶段。
