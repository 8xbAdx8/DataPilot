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

## 2026-10-18 · 安全 Text-to-SQL 后端

- 加入 Provider 协议、严格 SqlProposal、可替换的 OpenAI 兼容适配器和确定性 Mock；缺 Key/错误 JSON/超时/HTTP 错误明确失败，无隐式降级、重试或修复。
- SQLGlot AST 节点与函数白名单、Schema 语义验证、规范化 SQL；拒绝跨表、多语句、文件/网络读取等。专项回归曾发现 EXCLUDE 未知字段被解析器忽略，已显式拒绝所有星号修饰并加入回归。
- DuckDB 子进程只读执行，外部访问/扩展/Python replacement scan 关闭；截止时间、进程回收、并发/内存/行数/字节限制已测试。
- 新迁移 0002 保存分析元信息与审计；结果快照有界写本地，历史/详情/CSV API 已接通；导出对公式文本转义。
- 真实 PostgreSQL 全套：99 passed，1 skipped（未启用真实 LLM API）；SQLite 在新增分析测试中发现 naive/aware UTC 时间差，已统一 API 时间序列化。追加修复后相关 80 项测试通过。
- Alembic `upgrade head` 和 `check` 成功（ORM 与迁移无差异）。SQL、Provider、DuckDB 专项已实际执行；HTTP MockTransport 测试不代表真实模型已验证。
- 原生 Excel 日期类型推断修复并加入回归；CSV/DuckDB 大整数以字符串跨 JSON 边界，避免前端丢失精度。

## 2026-10-18 · 图表、历史、导出与清理验收

- Next.js 接入完整提问链路、SQL 展示、真实结果表格、ECharts 柱/线/饼/表格策略、事实/推测/局限性、历史恢复和 CSV 下载。
- 图表直接使用 SQL 返回列和行；缺值、重复类别、精度敏感大整数或行数不足时保留表格。新增图表选择回归用例。
- 浏览器回归暴露首次加载/上传及历史异步加载时的时序假设：加载期间禁用上传，测试显式等上传响应，并通过“分析历史”语义区域定位历史操作，不使用盲目延时或重试。
- 加入数据集删除：running 状态拒绝删除、失败恢复文件、成功清理历史/快照；添加审计与可恢复的 retention CLI（默认 dry-run）。
- 数据集列表只返回轻量元信息，详情按需加载，避免批量传送所有预览。
- 最终后端真实 PostgreSQL：113 passed / 1 skipped（真实 LLM）；默认临时 SQLite：112 passed / 2 skipped（PostgreSQL、真实 LLM）；Ruff、迁移 check、pip check 通过。
- 前端 ESLint、TypeScript/生产构建通过；生产依赖 `npm audit --omit=dev` 为 0 vulnerabilities。Edge Playwright 覆盖上传、错误恢复、移动布局、Mock+真实 SQL/图表/导出/历史、显式删除。最终结果见后续部署记录。
- 已查看真实查询图表截图，核对 East=40、West=20 与表格、CSV 相同。
