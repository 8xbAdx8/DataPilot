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

## 2026-10-18 · 最终数据准确性复核

- CSV 的字段数完整全空记录现在保留，并计入行数/缺失率；物理空白行跳过，Excel 全空行策略在画像中说明。
- 整数导入改用 nullable dtype，避免带空值时先转换 float 导致精度丢失；超出 int64 的整数字符串保留文本。新增 9007199254740993 和超大整数回归。描述统计采用浮点计算的限制在画像中说明。
- retention 的 dry-run 现在预测中断请求恢复后的删除候选，避免预览未显示的过期数据集在 apply 时被删除；加入回归。
- 最新真实 PostgreSQL：117 passed / 1 skipped；默认 SQLite：116 passed / 2 skipped。Ruff check、format --check 均通过。
- 环境命令复核发现普通 `py -3.12` 无法选择本机 Astral 发行版；已验证 `py -V:Astral/CPython3.12.14 --version`，README 启动命令改为完整标签（3.12.14）。

## 2026-10-18 · 部署配置与交接

- 加入前后端多阶段/非 root Dockerfile、Compose 的 PostgreSQL → migrate → backend → frontend 依赖链、健康检查、独立卷和本机端口绑定。
- 加入 GitHub Actions（固定官方 Action commit）：应用检查/真实 PostgreSQL/Mock E2E 与独立 Compose 冒烟两个作业；YAML 结构验证通过，尚未推送或运行 CI。
- `docker compose config --quiet` 通过；实际 `compose up --build --wait` 停留在失效 Docker 引擎连接，主动终止客户端。没有声称镜像构建/容器启动成功，未重置 Docker 或其数据。
- 本机 FastAPI + Next.js 同源 HTTP 冒烟通过：上传 → Mock SQL → 实际 DuckDB East=40 / West=20 → 删除测试数据。
- Next 代理缓冲设为 11 MiB，给 10 MiB 文件的 multipart 封装留空间；通过同源代理实测恰好 10 MiB CSV（20,127 行）导入完整，超出 1 字节返回 413，测试数据已删除。
- 最新前端 ESLint、生产构建（含 TypeScript）、5 项 Edge E2E 均通过；可用 E2E_BASE_URL 复用已启动的 Next dev。
- 本地服务运行于 127.0.0.1:3000 / :8000，保留合成 sales.csv（8 行）与一次真实分组分析作为演示。原生 PostgreSQL 位于忽略目录，密钥 .env 未提交。
- 未解决的验收项：Docker 引擎恢复后的容器验收、用户配置真实 API 后的模型测试、推送后实际 CI。当前不具备鉴权/租户隔离，边界见 SECURITY.md。

## 2026-10-19 · GitHub CI 复核与历史检索

- 复核首轮 GitHub Actions 运行 `39051736631`（提交 `da76f81`）：`application` 和 `compose` 均成功。日志确认真实 PostgreSQL 后端 117 passed / 1 skipped、Chromium E2E 5 passed；Linux 镜像构建、全部容器健康和 HTTP 真实查询冒烟通过。Windows 本机引擎问题仍未修复，真实 LLM 仍无凭据；已更新 README / PLAN / OPERATIONS，保留此前开发记录的历史状态。
- 增加 `GET /api/analyses` 的问题/SQL 关键词与状态组合筛选。关键词最多 200 字符，使用 ORM 绑定参数并转义 LIKE 通配符；排序增加 ID，保证相同创建时间下的顺序。保持列表响应兼容，无需数据库迁移。
- 拆出前端 AnalysisHistory：每页 20 条，请求 21 条判断后页；支持搜索、状态筛选、翻页、重置、空结果和错误重试。过期请求不覆盖新筛选结果；分析详情与历史请求分别处理。
- 新增 18 项后端历史用例。初次真实 PostgreSQL 回归暴露测试默认假设数据库为空，已有演示历史导致 2 项失败；修正测试数据集范围，并创建独立 `datapilot_test` 库后重跑，未删除演示记录。
- 完整后端：真实 PostgreSQL **135 passed / 1 skipped**（真实 LLM）；默认临时 SQLite **134 passed / 2 skipped**（PostgreSQL、真实 LLM）。Ruff check / format、Alembic check、pip check 通过。
- 前端 lint、TypeScript/生产构建、7 项 Edge E2E 通过。新增真实持久化 SQL 搜索/组合筛选/跨 20 条分页/历史加载失败恢复。失败注入仅用于验证 503 恢复，不替代真实上传与查询。
- 移动截图复核发现关键词框被其他控件挤窄，改为窄屏独占一行，并补充 390px 视口下输入框至少 200px 的可用性断言。
- 新增 DEMO.md，给出可复现的五分钟演示、真实期望数值、面试讲解及尚未验证的边界。下一重点：真实 API 凭据验收与登录/所有权，不将 Mock 测试当作真实模型评测。

## 2026-10-19 · 管理员账户、会话与所有权

- 复核上阶段提交 `72a1329` 的 GitHub Actions `39194911404`：应用与 Compose 均通过，日志为 135 passed / 1 skipped、7 项 Chromium E2E。
- 新增管理员账户 CLI、scrypt 加盐哈希（N=2^17/r=8/p=1）、不透明随机会话、摘要持久化、HttpOnly/SameSite Cookie、会话轮换/退出撤销/过期、每账户最多 10 会话。密码仅交互或 stdin 输入，没有公开注册或默认账户。
- 登录失败按账户持久化 15 分钟窗口，5 次错误后 429；未知账户执行相同 scrypt 工作量，密码计算最多 2 个并发槽。CSRF 自定义头、精确 Origin 和 Fetch Metadata 校验覆盖所有写请求。API 禁止缓存，Pydantic 验证错误去除原始输入，避免密码回显。
- 数据集上传强制使用当前 user.id；列表、读取、删除、分析创建、历史搜索、详情及 CSV 导出均检查数据集 owner。外部/不存在 UUID 同为 404，拒绝发生在模型调用与读取结果文件之前。其他业务 API 回归使用数据库真实会话行，未覆盖掉认证依赖。
- Alembic 0003 在本机业务库与独立测试库升级成功。旧数据保留且 owner 为空，默认隐藏；CLI assign-legacy 先 dry-run，再由操作者 --apply 更新并审计，没有自动分配用户数据。独立临时 PostgreSQL 库实际验证 0002 → 0003 → 0002 → 0003、旧 8 行记录保留、owner 保持空以及 Alembic check，随后仅删除本次新建临时库。
- 保留命令增加过期会话与登录窗口清理；apply 使用包含过期谓词的 DELETE，避免并发登录重置窗口后被旧候选按主键删除。账户本身不自动删除。
- 后端真实 PostgreSQL 全套 **159 passed / 1 skipped**（真实 LLM）；默认 SQLite **158 passed / 2 skipped**（PostgreSQL、真实 LLM）。Ruff、format、迁移 check 与 pip check 通过；清理条件复核修改后重跑认证/保留专项。
- 前端 lint、TypeScript/生产构建与 **8 项 Edge E2E** 通过。新增两个随机账户实际 UI 登录/退出、刷新会话、Cookie 属性、真实查询及跨用户 UUID/导出拒绝；其他 7 项流程改为真实登录后运行。E2E 账户经 CLI 创建，密码不出现在参数或 trace 中；各测试清理自己的上传并退出，测试账户元信息保留。
- 初轮浏览器检查连接到尚未重启的旧后端，登录返回 404；确认并重启本任务进程后，修正登录错误断言与 Next 路由播报器的重复 alert 定位，最终全部通过。已检查 390px 登录截图。没有将失败回合标记为验收成功。
- Windows Docker 引擎问题仍在；本次认证版容器验收须以新提交的 Linux Compose CI 为准。真实 LLM 仍无凭据，未执行；下一阶段为请求限流、可观测性与密码生命周期。

## 2026-10-19 · 原子配额与执行诊断

- 复核认证阶段提交 `d9d1936` 的 GitHub Actions `39197001804`：application / compose 均成功，真实 PostgreSQL 159 passed / 1 skipped，Chromium E2E 8 passed，容器内账户创建、登录、真实查询与清理通过。
- 0004 增加 PostgreSQL 配额桶及分析 request_id / telemetry。条件 UPSERT 在数据库内原子检查并递增，默认每用户每 60 秒 30 次分析、10 次上传。分析配额与初始历史同事务，上传解析失败仍计入配额；限流返回 429 / Retry-After，不调用 Provider 或创建分析记录；计数存储不可用返回 503。
- 服务端生成每请求 UUID 并返回 X-Request-ID，覆盖成功、认证/CSRF/参数错误、配额与内部异常。JSON 白名单日志只记路由模板、状态/错误代码、ID、时间和有界诊断，不记 URL 参数、凭据、问题、SQL、文件名、结果行或异常正文；关闭原始 Uvicorn 访问日志，Compose backend 增加日志轮转。
- Provider、AST、DuckDB、结果快照分别计时，失败阶段仍记录已用时间；Token 仅取提供商响应外层 usage。无效、缺少或矛盾用量保持未知；Mock 明确为 0；SQL 被拒绝或内容格式无效也保留已知用量。不预置价格，不估算账单；HTTP 合同测试不代表真实 API 或计费验收。
- 前端增加“执行详情”和错误关联 ID，展示真实耗时与 Token 来源；旧记录无诊断时不补造数值。真实配额耗尽后显示等待提示，没有自动重试。浏览器已核对请求头/分析记录/界面一致，并检查限流反馈截图。
- 新回归发现 SQLite 已加载对象的 naive 时间与 aware 截止时间在 ORM DELETE 同步时冲突；改用数据库匹配并 fetch 同步，保留到期谓词避免并发重置窗口被误删除。追加检查总 Token 小于已知输入的矛盾边界，以及数据库在结果执行后提交失败时只记录失败和已知用量。
- 最新完整后端：真实 PostgreSQL **186 passed / 1 skipped**（真实 LLM）；临时 SQLite **184 passed / 3 skipped**（真实 LLM、PostgreSQL 连接和并发配额）。并发专项实际启动 8 个独立数据库会话竞争 3 个额度，恰好 3 次放行、5 次 429。Ruff check / format、Alembic check、pip check 通过。
- 前端 lint、TypeScript/生产构建与 **9 项 Edge E2E** 通过，包括真实 429、不自动重试和执行诊断。最终异常处理修改后重跑限流与真实查询诊断专项。
- 独立临时 PostgreSQL 库实际验证 0003 → 0004 → 0003 → 0004、旧历史与空诊断保留、ORM 无迁移差异；只删除本次新建临时库，未回退业务库。
- Windows 本机 Docker 引擎仍未修复，新提交的 Linux Compose 验收以本轮 CI 为准。真实模型和实际费用仍未验证，完整公网/IP 网关限流不在本阶段范围；下一步可处理密码生命周期或有限 SQL 修复。

## 2026-10-20 · 密码生命周期与会话管理

- 已复核提交 `a193df1` 的 GitHub Actions `39200243752`：application / compose 成功，真实 PostgreSQL 186 passed / 1 skipped、Chromium E2E 9 passed，容器真实上传/查询闭环通过。
- 账户安全面板接入真实 API：当前密码再确认、两次新密码确认、改密并全撤销、最多 10 条私有会话、逐条/全部/当前撤销。成功退出卸载工作台，关闭面板清空密码；面板打开/关闭保留原工作台状态。会话不展示推测的设备或位置。
- 管理员 `reset-password` 只处理已有用户，交互/getpass 或 stdin 输入；哈希更新、全会话撤销、失败窗口归零及脱敏审计同事务，提交失败全部回滚。没有公开密码恢复入口，没有重置任何现有演示用户的密码。
- 登录、改密、重置与撤销统一失败窗口 → User → AuthSession 锁序，维护清理顺序同步调整。savepoint 处理首次窗口并发插入，不提前提交其他业务状态；锁后强制刷新缓存用户、重验当前会话和最新时间；登录只轮换本用户旧 Cookie。解决旧密码登录在重置后创建有效会话、缓存旧密码和双改密的竞态。
- 本机 PostgreSQL 启动在新沙箱限制下返回 restricted-token error 87；仅提升权限启动本项目原生 PostgreSQL 后正常，未重置数据。业务库/独立测试库均升级 0005，Alembic check 无差异。独立临时库实际完成 0004 → 0005 → 0004 → 0005，旧 token 保留、每行公开 UUID 唯一、创建时间保持 NULL；只删除本次创建的临时库。
- 37 项新增账户 API 回归真实执行 scrypt、Cookie 与事务；首次 3 项失败为测试误将错误说明中的单词 inputs 当作输入泄漏，改为检查 JSON 的 input 键后全部通过。默认 SQLite 完整测试在加入 PG 并发文件前为 **221 passed / 3 skipped**；最终真实 PostgreSQL 完整套件 **228 passed / 1 skipped**（无真实 LLM 凭据）。
- 5 项真实 PG 并发回归使用独立连接、事件屏障及 pg_blocking_pids 确认真实锁等待：重置/登录双顺序、expire_on_commit=False 的缓存身份/会话、有效新会话配旧缓存密码、两个已认证排队改密恰一次成功；单独再跑 5 项全部通过。
- 前端 lint、typecheck、Next 生产构建通过。新增 E2E 首次定位到 Next 的空 alert，按实际错误文本定位后两项通过；检查了 390px 账户安全截图，表单和双会话无横向溢出。发现 Playwright 自动失败 ARIA 附件会含密码值，使用当前 runner 支持的 PLAYWRIGHT_NO_COPY_PROMPT 禁用；同时保持 trace 关闭，旧失败附件已清理。
- 最终全套真实 API / Edge E2E **11 passed，exit 0**。Windows 沙箱使 Playwright 的 taskkill 无法回收 Next，测试全部完成后只结束本轮日志中确认的 3 个 Next 进程，runner 正常退出；没有结束其他 Node 或浏览器进程。Ruff check / format、pip check、git diff --check 均通过，未发现新增密钥或跟踪运行数据。
- Compose 冒烟增加会话清单、改密、新旧密码验证和全部退出。Windows Docker 引擎问题仍在；本轮容器与 Chromium 全套结果以新提交 CI 为准。真实模型、实际账单及公网部署仍未验收；后续可增加有限 SQL 修复，始终重新进行 AST 校验并限制重试次数。
