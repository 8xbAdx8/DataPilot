# 启动、保留与清理

## Docker Compose

升级后的 PG 镜像基于原 `postgres:17-alpine` 编译 pgvector 0.8.7，保留原基础发行版和卷格式。首次从 0005 升至 0006 前先备份业务库，保证迁移账号有 CREATE EXTENSION 权限；已有旧 vector 版本需管理员先显式升级扩展。`alembic downgrade 0005` 删除新知识表/索引及其数据，会保留共享 vector 扩展和既有业务表，须先备份知识内容。单元回滚只在临时测试库执行，不能随意对业务库回退。

隔离的完整离线验收使用 Compose >=2.24.4（ports `!override` / env_file `!reset`）：

```powershell
$env:COMPOSE_FILE='docker-compose.yml;infra/compose.test.yml' # Linux 用冒号分隔
$env:COMPOSE_PROJECT_NAME='datapilot-upgrade-e2e'
$env:SMOKE_BASE_URL='http://127.0.0.1:13000/api'
$env:SMOKE_REQUIRE_RAG='1'
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example up --build --wait --wait-timeout 300
.\.venv\Scripts\python infra/compose-smoke.py
docker compose ps
docker compose down # 仅停止该测试项目，保留其独立卷
```

这套栈使用 15432 / 18000 / 13000 和独立命名卷，不读开发者 .env，强制 Mock/Fake、追踪关闭。冒烟验证健康、上传、Agent/Graph、实际聚合、知识索引/引用/删除，以及密码和会话撤销；配置不是 Mock 时在调用前拒绝，避免误用真实模型。

本次完整隔离栈已构建并验收通过：PG vector 0.8.7 / 迁移 0006，三个服务 healthy，HTTP 冒烟通过；生产容器同源 Edge 回归 13 passed。若要复跑浏览器测试，在上述终端另设 `DATABASE_URL=postgresql+psycopg://datapilot:datapilot_local@127.0.0.1:15432/datapilot` 和 `E2E_BASE_URL=http://127.0.0.1:13000`，再 `npm --prefix frontend run test:e2e`。CLI 测试账户必须创建在对应隔离数据库，不能误写本机业务库后去容器登录。恢复日常开发时关闭该测试终端的变量或新开终端。

Docker Hub 不通时可在本终端显式设置 `PYTHON_BUILD_IMAGE=public.ecr.aws/docker/library/python:3.12-slim`、`NODE_BUILD_IMAGE=public.ecr.aws/docker/library/node:24-alpine`、`POSTGRES_BUILD_IMAGE=public.ecr.aws/docker/library/postgres:17-alpine`，再构建隔离栈。这是 AWS 官方公布的 Docker Official Images 镜像源；Dockerfile 默认不变。镜像可配置不代表网络问题已解决，实际验收状态见 [升级验收](UPGRADE_ACCEPTANCE.md)。使用完测试终端变量后，新开终端恢复普通 Compose 配置。

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose config --quiet
docker compose up --build --wait --wait-timeout 240
.\.venv\Scripts\python infra/compose-smoke.py # 使用本地 Python 验证 HTTP 闭环
```

浏览器打开 <http://localhost:3000>。PostgreSQL 健康后，`migrate` 先执行 Alembic，成功后启动 FastAPI；API 就绪后启动前端。命名卷 `postgres_data` 保存业务数据，`analytics_data` 保存 DuckDB 和有界结果快照。前端在构建时将 `/api` 指向 `http://backend:8000`；本地开发默认指向 `http://127.0.0.1:8000`。

首次启动后按下方创建账户，再登录工作台。健康检查无需登录；业务 API 一律需要会话。

`compose-smoke.py` 要求 Docker CLI、已启动的 Compose 栈和 Mock 模式：通过容器 CLI 创建随机合成测试账户，经同源 HTTP 登录，检查健康、真实上传、分组 SQL 和 DuckDB 结果，删除自己的数据集后再验证改密、新旧密码、会话清单和全部退出。密码只经子进程 stdin 和请求体传递，不打印；一次性账户元信息保留在该测试环境。设置真实模型后不要用它代替真实 API 专项测试。

默认上传文件上限 10 MiB；Next 代理缓冲为 11 MiB，留出 multipart 封装空间。提高后端 `MAX_UPLOAD_BYTES` 时，也需检查前端代理与容器资源限制。前端 E2E 默认使用独立的 3001 端口；已启动开发服务时，可设置 `E2E_BASE_URL=http://127.0.0.1:3000` 复用它，避免两个 Next dev 进程争用同一构建目录。

```powershell
docker compose ps
docker compose logs --tail 100 backend
docker compose down # 停止，不删除命名卷
```

不要随意执行 `down -v`，它会删除数据库与分析数据。所有发布端口仅绑定本机；平台登录账户与 PostgreSQL 连接账户彼此独立。数据库默认密码只用于本地演示。修改密码时，连接 URL 中的保留字符需 URL 编码。

**历史验收状态（2026-10-19 复核）：** [GitHub Actions 首轮运行](https://github.com/8xbAdx8/DataPilot/actions/runs/39051736631) 的 `application` 与 `compose` 作业均成功。当时 Linux runner 已完成镜像构建、PostgreSQL/后端/前端健康检查和同源真实查询冒烟，Windows 引擎受阻。2026-10-20 本次引擎已可运行，升级后镜像和网络的实际验收单独记录在 [升级验收](UPGRADE_ACCEPTANCE.md)，不沿用历史绿色状态。

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
| 数据集知识与向量 | 随数据集保留，或手动删除 | 删除数据集级联清理；当前公开 API 仅创建数据集作用域知识 |
| 结果中的知识引用快照 | 随分析历史 30 天 | 删知识后新查询不再命中，旧历史仍保留有界来源文本 |
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

已有用户重名会拒绝覆盖；创建命令不会修改密码。自动化可用 `--password-stdin` 读取一行随机测试密码，不要用会打印输入的脚本或把真实密码保存进仓库。没有账户删除 UI，账户元信息不自动清理；测试应使用独立数据库，E2E 会保留随机合成账户但删除自己的数据集并退出。

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

## 密码与会话管理

登录后点击“账户安全”。修改密码需要当前密码、新密码与确认；新密码为 12–128 字符且须不同于旧密码。成功后撤销本账户全部会话，返回登录页。会话列表最多 10 条，只显示随机公开 ID、创建/过期时间和当前标记；它不识别设备型号或地理位置。撤销单条或全部会话同样需要当前密码。撤销其他会话保留当前工作台；撤销当前会话或全部会话立即返回登录页。

忘记密码时，由能进入后端运行环境的管理员执行；没有 Web 重置入口或自动发送邮件：

```powershell
.\.venv\Scripts\python -m app.accounts reset-password --username alice
docker compose exec backend python -m app.accounts reset-password --username alice
```

命令交互输入并确认新密码；也支持 `--password-stdin`。它只修改已存在账户，并在同一事务中更新哈希、撤销目标账户所有会话、归零密码失败窗口和写入不含密码的审计。不会修改数据归属或其他用户；数据库提交失败时全部回滚。管理员可重设相同密码来强制撤销会话，但用户自行改密须使用不同新密码。

0005 为会话新增独立公开 UUID 和创建时间。旧会话令牌继续有效，创建时间保留 NULL，界面显示“未知”；新会话记录真实时间。先 `alembic upgrade head`，再重启后端；就绪检查会拒绝缺少新列的旧 schema。回退仅在独立测试库验证，业务库回退前需备份并停止新版服务。

登录与密码再确认共享每账户 15 分钟 / 5 次失败窗口；换接口或换会话不能增加猜测额度。改密、重置、登录和会话撤销通过 PostgreSQL 行锁串行化，等待后重新读取密码与当前会话；不能只在请求开始时认证一次。会话撤销阻止后续请求，已被接纳的分析或导出可能完成，不会强行取消已运行的 DuckDB 进程。


## 阶段17–19验收入口

普通服务继续使用 `docker compose up -d --build`；PostgreSQL健康后migrate升级到head，再启动FastAPI，最后启动Next.js。各容器承担独立职责，前端通过代理调用API，DuckDB只查询上传数据，平台PostgreSQL从不交给模型执行SQL。

完整迁移演练可在仓库根运行 `.venv/Scripts/python infra/check-migrations.py --disposable --host 127.0.0.1`。脚本创建随机独立数据库，连续upgrade/check、downgrade到base后再upgrade/check；只删除自己成功创建的临时库，不回退配置中的业务库。需要本机数据库管理员允许CREATE DATABASE；不要在生产数据库使用直接downgrade做演练。

真实专项默认不运行。`frontend/e2e/phase19-live.spec.ts` 要求 `RUN_PHASE19_LIVE=1`，只允许合成数据，逐案例检查预算，独占证据文件防止自动重跑；另一精确名称诊断要求 `RUN_PHASE19_DIAGNOSTIC=1`，读取已存在的主验收预算记录，且不改原4/5结论。E2E_BASE_URL与DATABASE_URL必须指向同一独立测试栈。真实Key只经后端本地env_file传入，前端和测试输出不得包含Key。结束恢复Mock/Fake、关闭云端追踪并清理自己的测试项目。阶段19原始结果及失败见[发布准备](PHASE19_RELEASE_READINESS.md)。


阶段21注册指标可在现有“业务知识管理”中选择metric_definition，标题与JSON name一致，内容格式见[指标契约](METRIC_DEFINITIONS.md)。既有KnowledgeDocument.content存储即可，不需要迁移。复杂旧文本先转换为明确JSON；无法证明适用性时返回澄清，不能通过增加工具预算或降低检索阈值解决。真实开发CLI `app.metric21_acceptance` 显式要求RUN_LIVE_METRIC21=1；原始证据路径独占、90k停止线、下一案例70k保守预留。前次报告出现未知usage/未结束时，CLI在任何新调用前拒绝。本轮未知用量后保持停止，不重跑旧题；费用和完整Token未知。具体服务失败与已验证范围见[阶段21报告](PHASE21_METRIC_DISCOVERY.md)。
