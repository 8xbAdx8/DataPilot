# 开发与验证记录

## 2026-10-17 · 环境检查

- `D:\codexproject\DataPilot` 初始为空，无 Git 历史/用户文件；已初始化仓库。
- Node.js 24.15.0、npm 11.12.1、Git 2.54.0。
- Python Launcher 存在默认 3.14 和独立 3.12.14；显式选用 3.12.14 创建 `.venv`，项目限制 `>=3.12,<3.13`。
- Docker 29.6.2、Compose v5.3.1 已安装，初次检查引擎未运行；正在尝试启动。
- 没有发现适用的 AGENTS.md。

后续每阶段在此记录实际执行的命令、结果、问题与下一步；不会将尚未执行的检查记为通过。

## 2026-10-18 · 骨架验证

- 使用独立 PostgreSQL 17.10 原生进程（仅监听 127.0.0.1:5432）；数据位于忽略的 `data/pg-local/`。
- 执行 `alembic upgrade head` → `downgrade base` → `upgrade head` 成功。
- 骨架测试 3 项通过，包含真实 PostgreSQL 的连接与迁移检查；HTTP `/api/health/ready` 返回 `database: ok`。
- 前端 Next.js 16.4.2 / React 19.3.0：ESLint 与生产构建通过（包含 TypeScript 检查）。
- pip check 和 Ruff 通过。Starlette TestClient 对 httpx 的迁移弃用警告来自上游，当前测试正常执行。
- Docker Desktop 4.83.0 启动在 `dockerInference` Windows 套接字处失败，未重置用户 Docker 环境；容器部署仍未验证。
- 官方 EDB 大包下载超时，改用固定版本 `@embedded-postgres/windows-x64@17.10.0-beta.17` 的 PostgreSQL 二进制完成验证；临时下载和运行数据不入 Git。
- npm 首次下载 Next 原生依赖时连接停滞；终止该安装后，用缓存和显式请求超时重试成功。

## 2026-10-18 · 上传与画像切片

- CSV/XLSX 实际内容校验、分块请求大小限制、行/列/ZIP 资源限制、工作表选择、公式/宏/外链拒绝、保守类型推断、DuckDB 导入、元信息事务与失败清理已实现。
- 前端接入真实 API：数据集列表、上传、工作表选择、加载/错误恢复、质量统计和预览；加入 shadcn/ui 源码组件与响应布局。
- `TEST_DATABASE_URL` / `TEST_POSTGRES_URL` 指向本机 PostgreSQL，后端 22 项通过；默认临时 SQLite 模式 21 项通过、1 项 PostgreSQL 专项跳过。
- Edge / Playwright：3 项通过，覆盖真实上传及刷新持久化、重复列错误后恢复、390px 移动布局。已查看桌面/移动截图并修正文件选择区布局。
- 浏览器测试首次失败是 Next.js 路由播报器也具有 alert 角色，已将错误断言限定到包含实际错误内容的 alert；产品正确返回了重复列拒绝信息。
- 原始数据与结果仅在忽略目录中；样例 sales.csv 为本项目合成数据。下一阶段：Provider、AST 安全校验与隔离执行。
