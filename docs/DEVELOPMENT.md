# 开发与验证记录

## 2026-10-21 · 阶段 13–14

阶段 12 `ae45190` 正常推送后，远端完整 SHA 相同，Actions 39445512904 两个 Job 成功。人工用户确认全部 44 条标签与分组后，阶段 13 在原生产逻辑上建立真实基线并本地提交 `af0cd2d`。阶段 14 完成开发矩阵、Ragas/FlashRank 真实运行、配置冻结及一次留出对照；后续提交不推送。

检索改善与下游失败分开报告：默认 vector/0.70 漏 1 个正例，留出误召回降到 6/13，但错误采用仍 1/4，严格 SQL 因列名/超时未通过。不改题、不放宽 5 秒、不重跑留出刷分。生产依赖锁不变，评测锁独立。详见 [阶段 13](PHASE13_RAG_BASELINE.md)、[阶段 14](PHASE14_RAG_CALIBRATION.md)；完整命令见 [RAG_EVALUATION](RAG_EVALUATION.md)。

## 2026-10-17 · 环境检查

- 项目工作目录初始为空，无 Git 历史/用户文件；已初始化仓库。
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

## 2026-10-20 · 真实模型接入准备

- 已复核 `5ded830` 的 Actions `39376772394`：真实 PostgreSQL 228 passed / 1 skipped、Chromium 11 passed；application 与 compose 均成功。容器日志确认真实查询和密码/会话撤销两段冒烟通过。随后按用户要求将 GitHub 仓库设为 PUBLIC，公开前检查全部 10 个提交与 215 个 blob，未发现 .env、运行数据库或常见密钥特征。
- 对照 DeepSeek 官方入门与 Chat Completions 文档，将代码默认值和 `.env.example` 的示例模型更新为 `deepseek-flash`。当前 API 默认开启思考，适配器仅在 deepseek Provider 显式关闭 thinking，避免耗尽原有 1000 Token 的 SQL JSON 输出预算；其他兼容 Provider 不收到专用参数。
- 新增 REAL_MODEL 中文操作文档：开放平台 Key、本地 .env、重启、显式真实 API 专项、合成样例核对、费用与错误边界；未读取或写入用户实际 Key，也未修改本地 .env。
- Provider/真实调用开关/遥测/分析回归 **46 passed / 1 skipped**，包含三种 Provider 的请求合同；真实 API 专项因未显式启用而跳过，不能把 MockTransport 测试记作真实模型验证。Ruff check / format 通过；后续真实调用须由用户本地配置凭据并明确启用验收。

## 2026-10-20 · 首次真实 DeepSeek 验收

- 用户已在本地配置并授权真实调用。只输出 Provider、模型、官方 API host、是否存在 Key 及超时等非敏感配置，未输出 Key；本机为 deepseek / deepseek-flash，HTTPS，超时 60 秒，无同名进程环境覆盖。确认 `.env` 被 Git 忽略且未跟踪。
- 显式设置 RUN_LIVE_LLM=1，仅执行 `backend/tests/test_live_llm.py`，完成 **1 passed**（2.42 秒为整个 pytest 测试过程，非独立模型延迟）。真实 API 生成 SQL，严格解析和 AST 校验后，由受限 DuckDB 对 10/20/30 合成数据执行，单字段实际结果为 3。未替换 Provider、HTTP 或数据库执行器；测试结束后移除该进程中的 RUN_LIVE_LLM 开关。没有继续发起其他付费模型调用。
- 原 API 仍缓存 Mock 配置；核对此前启动的本项目服务命令与 8000 端口归属后重启。后端直连与 Next 同源 `/api/config` 均为 deepseek / deepseek-flash，数据库就绪；本地服务保持运行。没有修改用户 `.env`、账户或业务数据。
- 复核接入准备提交 `f9a8ca7` 的 Actions `39377994467`：application 与 compose 均成功。该 CI 使用 Mock Provider，不能充当真实模型验收。当前仅有单个真实 API / 安全 SQL / DuckDB 用例，尚未据此宣称复杂分析、图表和真实模型浏览器端到端通过，也不推断准确率或实际账单金额。

## 2026-10-20 · 阶段 9 真实集成验收与推送前审计

- 基线 `e4015b3` 工作区干净，先建立[验收计划和报告](PHASE9_INTEGRATION.md)，不扩展生产代码。明确聊天为真实 DeepSeek、Embedding 未配置、LangSmith 关闭且无 Key，常驻验收服务仍为 Mock/Fake。
- 显式真实专项 **3 passed**。实际原生 Schema/画像工具 → 结构化计划 → LangGraph → 真实 SQL → AST → DuckDB，合成分组 East=40、West=20；校验图表配置、历史、CSV 与审计。该案例 Agent 2 次 + SQL 1 次请求、3071 Token，金额未知；不是完整真实 RAG 或浏览器验收。
- PostgreSQL 全套 **328 passed / 4 skipped**，SQLite **310 passed / 22 skipped**；新专项默认关闭，旧测试保留。前端 lint/build、本机和生产容器 Playwright 各 **13 passed**；Docker 启动/健康/查询/知识/账户冒烟通过，均明确 Mock/Fake。迁移重复执行与临时库回退、Ruff 和 pip check 通过。
- 先前系统临时目录权限错误已通过独立仓库临时目录修复后完整重跑，未跳过失败用例。全部可达 20 个提交、328 个文件版本审计未命中真实 Key 或私钥特征；公开本地示例与测试值已分类。当前泛化开发路径，历史仍保留非敏感路径，未重写历史。
- 真实 Embedding/语义 RAG、LangSmith 云端和包含它们的完整真实 E2E 标记阻塞，未推送 GitHub。所有逐项证据、命令与边界见阶段报告。

## 2026-10-20 · 阶段 10 真实 Embedding 与语义 RAG

- 按用户明确授权推送 `codex/agent-rag-upgrade` 至 `0e7b1c7`，官方 REST 核验远端 SHA 一致，Actions `39412395408` 实际成功。Git 连接出现间歇性重置/超时，使用单次 OpenSSL 设置推送成功，未关闭证书校验或改全局配置。
- 用户本地配置独立百炼 Key 和北京空间地址；只补齐原先缺失模板并修正 Provider/模型两个非敏感字段，保留其他设置。实际使用 openai_compatible / text-embedding-v4 / 1536 维，聊天仍为 DeepSeek，云端追踪关闭。
- 首次三个真实专项返回 HTTP 错误而失败，知识停在 failed，未进入聊天或 SQL；保留失败摘要。随后单条探测 HTTP 200，再分步重跑 **3 passed / 13.98 秒**，首次原因未独立确认，不将失败改成跳过。
- 真实语义改写检索命中相似度 **0.735559**；pgvector 实际存储、引用 ID、跨用户/数据集隔离、去重、更新、失败恢复及删除通过。受控缺 Key 是本地故障注入，不冒充远端故障。
- 真实 Agent 原生 Schema/指标工具、实际知识来源 → SQLGlot → DuckDB，净贡献额 **18**。临时独立真实容器栈的 Edge 浏览器 **1 passed / 21.0 秒**，页面/ECharts/CSV **East=6、West=12**，刷新恢复历史、引用 GET 验证通过；没有拦截或伪造浏览器请求。
- 两个分析案例共 6 次聊天请求，供应商报告 **7304 Token**；Embedding Token 未采集、金额未知，不据固定案例推断准确率或召回率。LangSmith 云端、统一 29 题真实评估仍未执行。
- PostgreSQL **328 passed / 6 skipped**，SQLite **310 passed / 24 skipped**；默认 Mock/Fake 浏览器 **13 passed / 1 skipped**。新增三项真实专项默认关闭，旧测试保留。前端 lint/build/typecheck、Ruff、pip check、迁移检查/回退通过。
- 临时真实栈及本轮新建的两个合成数据卷已清理，原服务保留。工作空间 Key 格式扫描新增点号支持，未发现实际 Key 进入 Git。无生产执行代码或依赖变更；阶段 10 只本地提交，未自动推送，完整证据见[验收报告](PHASE10_EMBEDDING_RAG.md)。

## 2026-10-20 · 阶段 11 推送与固定真实评测

- 起点为干净的 `4b95daf`，按用户授权推送 `codex/agent-rag-upgrade`；GitHub REST 远端 SHA 完全一致，Actions `39432044305` 的 application / compose 最终成功，之后才运行真实评测。
- 保持原 29 题、4 行合成 CSV 和严格评分。仅为评测 CLI 加实际 SQL/结果/节点/工具/引用证据与拒绝覆盖的逐题检查点，将执行时限和字节限制故障也明确标注。没有修改生产分析、安全策略、提示词、配置、迁移或依赖。
- 首次计时包装遗漏 Windows spawn 入口保护，12 道已完成题超时；停止核实的本轮进程并保留部分记录，修正包装后从头有效运行一次。首次 3984 已报告 Token 不计入有效得分，其额外开销仍未知，不归因于模型，也没有通过提高查询超时掩盖。
- 有效运行 58 次分析耗时 299474.704 ms，18 次真实百炼 Embedding 全部成功且 1536 维；39 次真实 SQL Provider + 71 次真实 Agent 调用尝试，71 次工具调用。39 条实际执行 SQL 的最终批准与独立 AST 复核均通过，事务结束后 8 张表记录数恢复。
- 严格结果符合率基础 1/13、增强 0/13；完整规则均 6/29。保留别名、列集、排序差异导致的失败，也记录平均值/缺失定义的工具预算错误、无关知识 0.213698 误召回与澄清问题。登记净销售额的真实结果从基础 36 改善为增强 30，不另造放宽后的准确率。增强 9 个安全题均未 execute，但指定拒绝码仅 1/9，原评分不改。
- 已记录有效运行聊天 Token 小计 60534，14 道增强题和 1 道基础题组合用量未知；Embedding Token、总金额未知。LangSmith 保持关闭，无云端验收。本题库是实际 API/PG/Agent/DuckDB，不包含本次浏览器测试，真实浏览器证据仍来自阶段 10。
- 最终 PostgreSQL **329 passed / 6 skipped**，SQLite **311 passed / 24 skipped**；Ruff check/format、pip check、检查点覆盖保护及 diff 检查通过。已确认远程前端/迁移/Compose 检查成功，没有将其标为新阶段提交的 CI。
- 提交前历史 22 提交/348 blob 未发现实际 Key 或禁止路径；新增文件精确凭据/私有服务地址扫描为 0，运行产物均忽略。真实逐题[JSON](evaluation-live.json)与[报告](PHASE11_LIVE_EVALUATION.md)保留失败与边界；本阶段仅本地提交，不再次推送。

## 2026-10-20 · 阶段 12 契约与 Agent 预算内收敛

- 按用户要求推送 `702c48e`。默认 GitHub 解析地址传输失败，最终仅对单条命令临时使用公开 DNS 返回的地址和 Schannel，保留 TLS 校验；远端 SHA 一致，Actions `39439653579` 成功后再进行本阶段真实开发题评测，没有改系统 DNS 或全局 Git 设置。
- 先冻结 v2 开发/验证各 6 题，明确聚合别名、列集、排序、空集及缺口径澄清。保留 v1 的 29 题与两份历史 JSON，不事后提高旧分数。新题集是小型合成回归，不是独立盲测。
- Agent 每次知道剩余额度，最后一次模型响应或工具额度耗尽时只保留 AnalysisPlan；原 3 次模型/4 次工具/Token/修复上限不变。整批调用先验证授权和额度，违规批次不执行任何工具；各工具仍核对所有权。原生 HTTP 合同验证工具列表实际缩窄，模型违约仍拒绝，不通过提示词代替安全检查。
- 评测提供 suite/split 及 SHA 身份，增加 SQL/Agent Token 分项；明确无 SQL 调用时计入 Agent-only 用量，真正调用且未上报仍未知，不回填阶段 11 缺失指标。
- 真实开发/验证各一次，两种模式共 24 次分析、48 次真实聊天调用尝试、26 次原生工具、12 次真实 Embedding 全成功。增强两组均 6/6，含各 5 道严格计算和 1 道正确澄清；缺口径题第三次响应收敛，未调用 SQL。基础组各有错误口径和格式错误，全部保留。报告聊天合计 46658 Token，Embedding 用量和金额未知，LangSmith 关闭。
- PostgreSQL **337 passed / 6 skipped**，SQLite **318 passed / 25 skipped**，Ruff/pip、前端 lint/build/typecheck 通过。新后端镜像源码指纹一致、v2 题集可导入，独立 Compose 健康/迁移重复执行/check/真实查询/RAG/账户冒烟通过；生产容器 Edge **13 passed / 1 skipped**，明确 Mock/Fake。
- 临时项目及本次新建的两个卷已清理，原三服务保留。提交前历史 23 提交/358 blob 未发现当前 Key 或禁止路径，待提交报告精确匹配原始分数，源代码指纹与真实运行一致，私有服务地址/密钥扫描未命中。阶段 12 只本地提交，详情见[报告](PHASE12_AGENT_RELIABILITY.md)。
- 新契约结果不能与阶段 11 直接比较；未改检索阈值、旧误召回风险仍保留，未进行旧 29 题真实重跑、泛化基准、云端追踪或真实修复收益测试。后续先建立独立检索正负样本和留出评测。

## 2026-10-21 · 阶段 13–15 检索评测与真实云端验收

- 阶段 13 `af0cd2d` 冻结已人工复核的 23 条知识、44 条查询和开发/留出分组；真实基线及失败完整保留，见[阶段 13](PHASE13_RAG_BASELINE.md)。
- 阶段 14 `b16651f` 对比向量/混合/真实 FlashRank，选择 vector / none / 0.70；独立锁定评测环境运行真实 Ragas。正式留出只运行冻结前后的两组一次，没有据此调参；选定配置仍有误召回 6/13、漏召回 1/8、错误知识采用 1/4，严格 SQL 0/2，见[阶段 14](PHASE14_RAG_CALIBRATION.md)。
- 阶段 15 完成真实 DeepSeek Agent 工具、百炼 Embedding、pgvector、Graph、AST、DuckDB、前端图表/导出/历史/引用的独立浏览器验收；实际 East=6、West=12，四次聊天请求共 4815 Token，费用未知。初始语义 no_match，由指标工具补足知识；不把这条成功改写成留出集通过。
- LangSmith 服务器实际读回 14 个成功 spans、4 个受控拒绝 spans，以及 22 条实验运行、标签和 17 个反馈。正文为空，元数据受限，应用开关保持默认关闭。云端四位反馈与本地全精度分数分别保存，没有重复创建实验。
- 完整 PostgreSQL 355 passed / 6 skipped、SQLite 334 passed / 27 skipped；之后新增的数值表示测试在 17 项定向中通过，不并入旧完整计数。Mock/Fake 容器 Playwright 13 passed / 1 skipped、真实浏览器 1 passed；Ruff、前端检查、依赖检查、Compose 冒烟和迁移验证通过。失败尝试与修复、清理及推送前审计见[阶段 15](PHASE15_LIVE_ACCEPTANCE.md)。
- 基线 `ae45190` 的远程 CI 成功；三个新增阶段只创建本地提交，未自动推送。真实服务证据、工程回归与留出质量分别报告，不宣称全面通过。

## 2026-10-21 · 阶段 16 严格 SQL 正确性

- 先读取旧h-p-02/h-p-04完整证据：均因query_timeout失败，价值题另有别名违约；实际知识采用正确、计划与预算无根因证据。Windows轻/重入口实验确认spawn重导入Web栈可在进入查询前消耗5.68秒，旧0/2保持原样。
- 独立轻量Python查询worker避免父main重放，保留5秒含启动、两槽、AST/所有权/只读/外部访问关闭及行字节限制。SQL提示词明确列契约与NULL语义；没有改Agent/RAG/安全白名单或预算。
- 人工确认开发17/留出13题；参考执行发现h04 TIMESTAMP/DATE标签契约问题，保留v1并经再次人工确认建立v2。开发一次17/17规则、严格12/12；代码/配置冻结后留出一次12/13规则、严格7/8。唯一失败HAVING超出原白名单，AST拒绝且零执行，不调参或再跑。
- 真实聊天100107 Token、真实Embedding34次/873 Token，费用未知；来源业务题3/3与2/2，未出现查询超时或危险执行。LangSmith保持关闭，未挪用旧云端记录。HTTP钩子的原生Agent覆盖限制如实记录。
- 完整PG373 passed/6 skipped、SQLite352 passed/27 skipped；前端lint/typecheck/build、Playwright13 passed/1 skipped、Compose冒烟/迁移/回退、Ruff及pip检查通过。公开ECR构建在首次Docker Hub连接失败后成功，临时项目清理、原服务保留。
- 新语料精确字节受Git属性保护，避免换行转换破坏人工SHA；历史结果/旧receipt未改。仅本地提交，不推送。根因、逐题SQL/结果/来源、冻结和限制见[阶段16报告](PHASE16_SQL_CORRECTNESS.md)。


## 2026-10-21 · 阶段17–19

- 阶段17先从保留的h03 unsafe_sql证据定位原策略不支持HAVING，只增加GROUP BY内的有限聚合比较/算术AST子集；38项新增确定性回归，两套数据库目标回归各124通过。独立提交963be80，不回写旧留出结果。
- 阶段18用户确认新20知识/34问题，六组开发比较、冻结后一次留出，真实text-embedding-v4 1536/pgvector/FlashRank均执行。选择vector0.65作为实验推荐，留出正例8/8但应no_match误召回3/8；默认仍vector0.70。真实生命周期与LangSmith检索失败回读通过，独立提交ddba77b。
- 阶段19PG415/6skip、SQLite394/27skip、前端lint/typecheck/build、普通浏览器13/3skip、Docker构建健康冒烟、独立迁移完整回滚重建及依赖格式检查通过。新增云端关联工具后定向23通过，包含2新增测试。
- 五案例真实专项4/5：普通聚合与HAVING结果正确，缺失口径澄清和危险零执行；自动业务口径发现失败被如实记录。另一次精确名称工具诊断成功，浏览器图表/引用/历史/CSV通过；不能合并成主验收5/5。
- 共16次真实聊天、20120 Token，费用未知；云端限流和验收脚本错误保留，最终六条真实服务记录只读回验通过，输入输出和环境为空，契约只本地关联。恢复后测试服务无真实Key、云端关闭，独立项目清理，业务DB和原卷保留。
- 历史39份评测文件与阶段18冻结哈希未变。本轮只本地提交；未将旧远程CI冒充新提交CI。详见[阶段19](PHASE19_RELEASE_READINESS.md)。
