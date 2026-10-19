# 启动、保留与清理

## Docker Compose

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose config --quiet
docker compose up --build --wait --wait-timeout 240
.\.venv\Scripts\python infra/compose-smoke.py # 使用本地 Python 验证 HTTP 闭环
```

浏览器打开 <http://localhost:3000>。PostgreSQL 健康后，`migrate` 先执行 Alembic，成功后启动 FastAPI；API 就绪后启动前端。命名卷 `postgres_data` 保存业务数据，`analytics_data` 保存 DuckDB 和有界结果快照。前端在构建时将 `/api` 指向 `http://backend:8000`；本地开发默认指向 `http://127.0.0.1:8000`。

首次启动后按下方创建账户，再登录工作台。健康检查无需登录；业务 API 一律需要会话。

`compose-smoke.py` 要求 Docker CLI、已启动的 Compose 栈和 Mock 模式：通过容器 CLI 创建随机合成测试账户，经同源 HTTP 登录，再检查健康、真实上传、分组 SQL、DuckDB 结果，最后删除自己创建的数据集并退出。密码只经子进程 stdin 和登录请求体传递，不打印；一次性账户元信息保留在该测试环境。设置真实模型后不要用它代替真实 API 专项测试。

默认上传文件上限 10 MiB；Next 代理缓冲为 11 MiB，留出 multipart 封装空间。提高后端 `MAX_UPLOAD_BYTES` 时，也需检查前端代理与容器资源限制。前端 E2E 默认使用独立的 3001 端口；已启动开发服务时，可设置 `E2E_BASE_URL=http://127.0.0.1:3000` 复用它，避免两个 Next dev 进程争用同一构建目录。

```powershell
docker compose ps
docker compose logs --tail 100 backend
docker compose down # 停止，不删除命名卷
```

不要随意执行 `down -v`，它会删除数据库与分析数据。所有发布端口仅绑定本机；平台登录账户与 PostgreSQL 连接账户彼此独立。数据库默认密码只用于本地演示。修改密码时，连接 URL 中的保留字符需 URL 编码。

**实际验收状态（2026-10-19 复核）：** [GitHub Actions 首轮运行](https://github.com/8xbAdx8/DataPilot/actions/runs/39051736631) 的 `application` 与 `compose` 作业均成功。Linux runner 已完成镜像构建、PostgreSQL/后端/前端健康检查和同源真实查询冒烟。Windows 本机 Docker Desktop 在 `dockerInference` 套接字初始化时退出的问题仍未修复；本机使用原生 PostgreSQL / Python / Node。Linux 验收成功不代表 Windows 引擎恢复，也不代表真实模型 API 已验证。

## 当前机器的原生 PostgreSQL

本次已在忽略目录中准备了 PostgreSQL 17.10。如重启后需要继续本地开发：

```powershell
& './data/tooling/embedded-pg/package/native/bin/pg_ctl.exe' -D "$PWD/data/pg-local" -l "$PWD/data/pg-local/server.log" -o '-h 127.0.0.1 -p 5432' -w start
```

启动后按根 README 运行 Alembic / FastAPI / Next.js。Docker 修复后如要改用容器，先停止占用相同端口的本地服务；仅对本项目的原生数据库运行：

```powershell
& './data/tooling/embedded-pg/package/native/bin/pg_ctl.exe' -D "$PWD/data/pg-local" -w stop
```

## 保留策略

| 对象 | 保留期 | 实际行为 |
| --- | --- | --- |
| 原始上传文件 | 导入期间 | 导入后关闭上传临时文件，不保留原件 |
| DuckDB 与数据集元信息 | 创建后 30 天 | 清理命令删除过期数据集及其历史/快照 |
| 结果快照 | 分析后 7 天 | 清理后历史仍可查看，导出返回 410 |
| 分析历史元信息 | 分析后 30 天 | 删除历史，避免 PostgreSQL 持续增长 |
| 审计记录 | 90 天 | 独立保留事件、ID、原因；不复制整份问题和结果 |
| 会话与登录失败窗口 | 过期后 | 请求立即拒绝过期会话；清理命令删除到期会话及超过 15 分钟的失败窗口 |
| 用户操作配额 | 窗口到期后 | 到期自动允许新窗口，清理命令删除过期计数行 |
| 中断的 running 请求 | 10 分钟 | 清理时标记失败，不伪造结果 |
| 无元信息的 UUID 孤立文件 | 24 小时 | 只处理应用目录中的已知格式文件，不递归删除目录 |

```powershell
.\.venv\Scripts\python -m app.maintenance         # 只输出候选数量，不修改
.\.venv\Scripts\python -m app.maintenance --apply # 操作者明确执行清理
docker compose exec backend python -m app.maintenance # 容器内预览
```

保留期在运行清理命令时生效；MVP 不会在后台悄悄创建系统定时任务。运营部署应由操作者每日调用清理命令。删除单个数据集也可通过界面确认或 `DELETE /api/datasets/{id}`，同时清理其历史和快照；仍在执行分析的数据集返回 409。文件删除与业务事务使用可恢复重命名衔接，提交失败会恢复原文件；提交后若遇到 IO 失败，孤立文件由后续清理处理。

PostgreSQL 的画像中包含至多 20 行截断预览，因此也可能包含上传文件的敏感信息。删除数据集会删除这些元信息；不要把含隐私的数据用于公开演示。

## 账户与旧数据归属

管理员先运行迁移，再创建账户。没有默认密码，不通过 Web 开放注册：

```powershell
.\.venv\Scripts\python -m app.accounts create-user --username alice
# Docker 使用交互终端输入密码，不要把密码写在命令行中
docker compose exec backend python -m app.accounts create-user --username alice
```

已有用户重名会拒绝覆盖；创建命令不会修改密码。自动化可用 `--password-stdin` 读取一行随机测试密码，不要用会打印输入的脚本或把真实密码保存进仓库。当前没有密码重置与账户删除 UI，账户元信息不自动清理；测试应使用独立数据库，E2E 会保留随机合成账户但删除自己的数据集并退出。

0003 迁移新增 `users` / `auth_sessions` / `login_attempts` 和 nullable `datasets.owner_id`。此前数据保持原样，owner 为空时任何登录用户均不可访问；不会把旧数据自动授予首个账户。管理员在确认未归属数据的来源后显式操作：

```powershell
.\.venv\Scripts\python -m app.accounts assign-legacy --username alice         # 只预览数量
.\.venv\Scripts\python -m app.accounts assign-legacy --username alice --apply # 将全部未归属数据分配给 alice
# 容器内使用 docker compose exec backend python -m app.accounts ...
```

该命令只更新未归属数据，保留已有归属，并写审计事件；不会复制、删除或重算 DuckDB。多人来源的旧数据应先由管理员核对，不能盲目将全部未归属数据交给同一人。默认保留清理仍会处理过期未归属数据，升级后应及时检查归属。

回退 0003 会移除账户、会话与 owner 列；已有多用户数据时，应先备份并评估，不能直接恢复到没有鉴权的旧版服务。本次升级/回退验证使用独立临时数据库，未回退用户的业务数据库。

`SESSION_HOURS` 默认为 24，最多 168；每个账户最多 10 个会话，登录轮换浏览器原会话，退出只撤销当前会话。`SESSION_COOKIE_SECURE=false` 仅用于本机 HTTP；HTTPS 环境须设为 `true`。`AUTH_TRUSTED_ORIGINS` 为精确 Origin 列表；本地默认含 localhost / 127.0.0.1 的 3000 和测试 3001 端口，部署时改成实际 HTTPS 域名，避免通配或多余测试 Origin。CORS 配置不是身份认证。

所有写请求必须同时满足自定义 CSRF 头和来源校验。无 Origin 的非浏览器客户端仍需自定义头；携带未知 Origin、`Origin: null` 或 `Sec-Fetch-Site: cross-site` 的写请求拒绝。GET 导出依靠会话与所有权检查，不需要 CSRF 头。API 响应 `Cache-Control: no-store`，浏览器不在 localStorage 保存会话令牌或密码。

0004 迁移增加共享配额表及分析 `request_id` / `telemetry`，旧历史回填空诊断，不编造数据。升级后重新启动后端；配额、JSON 日志、请求 ID、Token 与日志轮转的配置见 [OBSERVABILITY](OBSERVABILITY.md)。所有实例须一致设置配额参数。Docker backend 日志默认按 `max-size=10m`、`max-file=3` 轮转；本机日志保留由操作者管理。
