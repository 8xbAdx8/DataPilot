# 阶段 9：真实集成验收与推送前审计

本页保留阶段 9 时点的配置与结果；后续按用户授权推送基线并解除真实 Embedding/RAG 阻塞的证据见[阶段 10](PHASE10_EMBEDDING_RAG.md)。下文“未推送/阻塞”指阶段 9 验收时点。

验收日期：2026-10-20。基线 HEAD `e4015b38054ac8963c185dcd0813904d4d50f02f`，分支 `codex/agent-rag-upgrade`，开始时工作区干净。本阶段不扩展生产功能、不修改个人 `.env`、不推送 GitHub。

## 验收计划（先于真实 API 调用建立）

1. 核对配置、实现入口、服务、数据库版本和迁移；只输出凭据是否存在。
2. 用合成销售数据执行明确配置的真实 DeepSeek 工具调用和服务端 Graph/SQL 流程；记录实际响应、调用数、Token 和耗时，不预设工具返回。
3. 真实 Embedding、语义检索及 LangSmith 仅在独立配置齐备且已启用时执行；缺少条件直接标记阻塞。
4. 强制 Mock/Fake、关闭全部 live 开关和追踪，运行 PostgreSQL/SQLite、安全回归、迁移回退、前端检查与浏览器测试、Docker 冒烟。
5. 扫描全部可达 Git 历史和当前文件的凭据风险、公开文档、忽略规则、依赖、默认开关，汇总证据。仅提交已验证的验收脚本和文档，不推送、不重写历史。

## 初始配置和环境

- Python 3.12.14、Node 24.15.0、PostgreSQL 17.10；原生业务库和独立测试库均为迁移 0006，pgvector 0.8.7，向量列 1536 维。
- 聊天：DeepSeek / `deepseek-flash`，Key 存在，官方 HTTPS 地址，60 秒超时。真实专项采用有限请求，不使用原始用户数据。
- Embedding：`none`，独立 Key 缺失；配置维度 1536。**真实 Embedding、向量持久化和语义 RAG 阻塞**，不复用聊天 Key，不用 Fake 冒充。
- LangSmith：显式开关关闭、Key 缺失。**云端追踪阻塞/未执行**，保留脱敏与关闭设置。
- 当前本地及隔离容器服务均为 Mock/Fake；不能用其健康检查证明真实 AI。
- Docker Engine 29.6.2 可用；隔离项目 `datapilot-upgrade-e2e` 的 PostgreSQL、后端、前端均 healthy，端口 15432/18000/13000。

## 实现入口

`backend/app/analysis_graph.py`（StateGraph）、`analysis_agent.py`（create_agent/ToolStrategy/工具预算）、`providers.py`（真实 SQL 生成）、`embeddings.py`（独立向量 Provider）、`knowledge.py`（授权后 pgvector 检索）、`sql_guard.py`（AST）、`execution.py`（受限 DuckDB）、`analyses.py`（历史与审计）、`tracing.py`（关闭原始自动追踪、仅白名单元数据）、`evaluation_cloud.py`（独立实验上传）。

## 执行结果

### 真实 DeepSeek：通过部分服务端链路

显式启用的三个专项 **3 passed，9.13 秒**：原有真实 SQL 专项、原有真实 Agent 专项、新增 `test_live_integration.py`。没有 MockTransport、预制 AIMessage、FakePlanningModel 或预制 SQL；新增专项仅给真实 `ChatOpenAI` 添加只读回调，观察实际响应中的 `tool_calls`，不替换网络或模型输出。只发送合成字段/问题及受控工具输出，不发送原始用户数据行。

固定合成数据：`region,revenue`，三行 `East,10` / `West,20` / `East,30`。提问明确要求先检查 Schema，再按 region 对 revenue 求和并排序。真实响应轨迹：

| 步骤 | 实际证据 |
| --- | --- |
| Agent 请求 1 | 返回模型 `deepseek-flash`；原生调用 `inspect_dataset_schema`、`get_profile_summary`，参数均通过当前 Pydantic 工具契约；1077 ms；输入 1113 / 输出 65 / 总 1178 Token |
| 工具执行 | LangChain 实际分派两个当前数据集只读工具；重新读取授权数据集，模型调用计数与工具计数分别累加 |
| Agent 请求 2 | 原生 `AnalysisPlan` 结构化调用，契约通过；1030 ms；输入 1279 / 输出 186 / 总 1465 Token；计划为 SUM(revenue)、region、region ASC |
| SQL 请求 | 真实 CompatibleProvider 请求 1 次，1208 ms（含请求和解析）；输入 380 / 输出 48 / 总 428 Token，来源 provider |
| Graph | prepare → retrieve → plan → generate → validate → execute → check_result；规划 2225 ms，校验 21 ms，DuckDB 子进程执行 1028 ms；修复 0 次 |
| 安全 | validations 为 approved / attempt 0，SQL 经过原有 SQLGlot AST/Schema 校验后才进入执行器 |
| 结果 | East=40、West=20，精确匹配预期；柱状图配置 x=region、y=total，直接使用同一结果 |
| 历史与导出 | PostgreSQL 中保存分析与开始/成功审计事件；GET 历史 SQL 一致；CSV 含 East,40。测试外层事务最后回滚，不污染业务数据库 |

实际经校验执行的 SQL：

```sql
SELECT "data"."region" AS "region", SUM("data"."revenue") AS "total"
FROM "data" AS "data"
GROUP BY "data"."region" ORDER BY "region" ASC
```

这个分组案例有 **3 次真实模型请求**（Agent 2 + SQL 1），工具调用 2 次，供应商报告总用量 **3071 Token**（输入 2772、输出 299）；请求、历史读取和导出检查共 4541 ms。它不是重复采样的延迟分布，不代表真实模型泛化准确率。另两项旧专项通过，分别验证真实工具调用和 COUNT=3；它们未保存逐请求 Token/时长，不推算整个 pytest 进程的总请求数或总 Token。**账单金额未知**，没有按固定价格估算。

该案例 RAG 实际返回 `not_configured`、`embedding_not_configured`、`used=false`、空引用；没有假装使用知识。它使用 TestClient 进入真实 FastAPI 路由、真实独立 PostgreSQL 和实际 DuckDB，不等于浏览器连接真实 AI 的完整 E2E。真实模型本次没有触发超时、工具故障或修复；这些异常只在下述受控故障回归中验证。

### 真实 Embedding / 语义 RAG：阻塞

有效配置为 provider=none、独立 Key 缺失。配置和数据库列均为 1536 维，扩展 0.8.7 已真实安装；**没有真实 Embedding 请求、返回维度、向量写入、相似度结果或知识引用证据**。未复用聊天 Key，未自动配置其他外部服务。

真实 PostgreSQL 中的向量写入、cosine 距离、索引、先权限后距离过滤、跨用户/数据集隔离、引用 ID 存在性，以及更新/删除/去重/失败/竞态等回归均通过，但向量由 Fake Provider 或受控 HTTP 响应产生。Docker 的知识 CRUD/来源及 SUM=60 也为 Mock/Fake。这些证据证明数据库及应用边界，不能证明真实语义检索质量或真实 Agent 根据语义知识规划。

解除阻塞需要独立配置真实 Embedding Provider、Base URL、模型和 Key，确认其返回 1536 维且 model 与配置匹配，再用合成知识索引/检索；不能只补 Key 后将旧 Fake 索引当作真实索引。

### LangSmith：关闭，云端未验证

`DATAPILOT_LANGSMITH_ENABLED=false`，Key 缺失，**未连接云端、无 run URL、不能声称上传成功或云端可定位失败节点**。没有为了验收启用云端或关闭脱敏。

代码及 `test_tracing.py` 受控 HTTP 回归确认：Graph/Agent 原始自动追踪使用 disabled context；关闭/缺 Key 不创建导出请求；最终 SDK 载荷只含固定节点/工具名、状态、计数及真实时间，输入输出为空；故障不影响分析。`test_evaluation_cloud.py` 验证独立实验上传合同。这是离线隐私/合同证据，不是云端接收证据，也不是本机网络抓包结论。

### 本轮回归结果

普通回归全部关闭真实调用开关和云端追踪；没有删除旧测试、放宽 SQL 策略或把失败项改成跳过。新增真实集成专项默认跳过，因此相较阶段 8 多一个显式 live skip。

| 类别 | 本轮结果与边界 |
| --- | --- |
| PostgreSQL 全套 | **328 passed / 4 skipped，184.12 s**；四项为真实 SQL、Agent、Embedding、新增部分链路专项；其中三项聊天相关专项已另行显式执行通过，Embedding 阻塞 |
| SQLite 全套 | **310 passed / 22 skipped，118.03 s**；另 18 项需 PostgreSQL，不用 SQLite 替代 |
| SQL/执行安全 | 上述全套覆盖危险/多条/未知表列/文件与外部函数、拒绝后零执行、危险修复拒绝、最多一次字段修复并重新校验、执行超时/空结果/行数与字节限制 |
| 模型/工具异常 | 上述全套的受控 HTTP/Fake/故障注入覆盖模型超时、错误 JSON、未知工具、工具/模型/Token/时间预算、假引用、每次工具授权；不是现场真实服务故障演练 |
| 迁移 | 独立测试库 upgrade head 连续执行两次、check 均通过；全套含临时库 0005 → 0006 → 0005 → 0006，旧用户保留、共享 vector 扩展保留，临时库清理 |
| 静态与依赖 | backend Ruff check / format、pip check 通过；新增审计脚本 Ruff / py_compile 通过 |
| 前端 | ESLint、TypeScript 与 Next 生产构建通过；本地 Edge/Playwright **13 passed（2.0 分钟）**，使用 Mock/Fake 和真实 HTTP/PG/DuckDB |
| Docker | Engine 29.6.2 可用；隔离栈 up --no-build --wait 通过，迁移 exit 0，三服务 healthy；健康/上传/查询、知识 CRUD/引用/SUM=60、改密/会话撤销冒烟通过，均 Mock/Fake |
| 容器前端 E2E | 生产容器前端 Edge/Playwright **13 passed（1.0 分钟）**；实际 HTTP/PG/DuckDB 和 ECharts/来源展示，模型仍为 Mock/Fake |
| GitHub Actions | 本阶段不推送，因此新提交的远程 CI 未运行；不沿用旧提交的成功结果冒充 |

首次 pytest 因旧系统临时目录和缓存访问权限在 setup 阶段报错（真实专项 3 errors，未调用模型），同期 SQLite 首次运行中止。改用仓库内忽略的独立 `--basetemp` 并禁用缓存插件后，按上述命令完整重跑通过；没有修改应用或测试断言来绕过失败。

## 实际命令

从仓库根目录执行；`$testDatabaseUrl` 表示事先配置的**独立** PostgreSQL 测试库 URL，不回显私有连接串。每组在独立 PowerShell 子进程执行，真实调用开关不留给普通回归。`--basetemp` 使用本轮新建的独立目录；后续复验应更换目录名，避免 pytest 清理已有资料。

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git log -8 --oneline

$env:TEST_DATABASE_URL=$testDatabaseUrl
$env:RUN_LIVE_INTEGRATION='1'
$env:RUN_LIVE_LLM='1'
$env:RUN_LIVE_AGENT='1'
$env:RUN_LIVE_EMBEDDING='0'
$env:DATAPILOT_LANGSMITH_ENABLED='false'
$env:PHASE9_EVIDENCE_PATH="$PWD/data/runtime/phase9-live.json"
.venv/Scripts/python -m pytest backend/tests/test_live_integration.py backend/tests/test_live_agent.py backend/tests/test_live_llm.py -q --tb=short -p no:cacheprovider --basetemp=data/runtime/phase9-live-tmp-1
```

普通回归另开进程，显式关闭全部真实开关：

```powershell
$env:LLM_PROVIDER='mock'
$env:RUN_LIVE_LLM='0'; $env:RUN_LIVE_AGENT='0'
$env:RUN_LIVE_EMBEDDING='0'; $env:RUN_LIVE_INTEGRATION='0'
$env:DATAPILOT_LANGSMITH_ENABLED='false'
$env:TEST_DATABASE_URL=$testDatabaseUrl
$env:TEST_POSTGRES_URL=$testDatabaseUrl
.venv/Scripts/python -m pytest backend/tests -q --tb=short -p no:cacheprovider --basetemp=data/runtime/phase9-pg-tmp-1
Remove-Item Env:TEST_DATABASE_URL,Env:TEST_POSTGRES_URL
.venv/Scripts/python -m pytest backend/tests -q --tb=short -p no:cacheprovider --basetemp=data/runtime/phase9-sqlite-tmp-1
.venv/Scripts/python -m ruff check backend
.venv/Scripts/python -m ruff format --check backend
.venv/Scripts/python -m pip check
$env:DATABASE_URL=$testDatabaseUrl
.venv/Scripts/python -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python -m alembic -c backend/alembic.ini check
```

前端在 `frontend/` 执行 `npm run lint`、`npm run build`，随后分别设置 `E2E_BASE_URL=http://127.0.0.1:3000` / `http://127.0.0.1:13000` 执行 `npm run test:e2e`；容器浏览器测试的账户 CLI 使用 `DATABASE_URL` 指向隔离 PG15432，而非原生业务库。

```powershell
$env:COMPOSE_FILE='docker-compose.yml;infra/compose.test.yml'
$env:COMPOSE_PROJECT_NAME='datapilot-upgrade-e2e'
$env:SMOKE_BASE_URL='http://127.0.0.1:13000/api'
$env:SMOKE_REQUIRE_RAG='1'
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example up --no-build --wait --wait-timeout 60
.venv/Scripts/python infra/compose-smoke.py
.venv/Scripts/python infra/audit-git-history.py
git check-ignore .env .env.production data/runtime/phase9-live.json frontend/test-results/example.json
git diff --check
```

真实证据和不含匹配值的历史扫描明细存于被忽略的 `data/runtime/phase9-live.json`、`phase9-history-audit.json`；本报告公开的数值和 SQL 均为合成数据。测试回滚独立库；未修改个人 `.env` 或常驻服务的 Mock/Fake 配置。

## GitHub 推送前审计

- 基线所有可达 refs 共 **20 commits、328 distinct blobs、140 历史路径**。逐树检查所有路径，使用 cat-file 逐对象检查所有文件版本与 commit/tag 内容；当前真实聊天 Key 在内存中逐字节匹配，**0 命中**；常见提供商 Key/PAT/AWS Key/私钥特征 **0 命中**；无个人 `.env`、私钥文件、运行数据库或日志进入可达历史。审计脚本只输出位置/数量，不输出匹配值。
- 广义告警 29 组 URL、41 组赋值、12 组路径已复核：URL 均为公开本地开发/CI 示例、Compose 插值或刻意无效的安全测试；赋值为这些示例、空 Key、提示占位符、运行时读取和合成测试密码。它们不是泄漏的真实模型凭据。开发数据库默认口令属于公开示例，不能视为生产机密。
- 历史 `docs/DEVELOPMENT.md:5` 自 `9887d63` 起含项目盘符路径，无用户名或凭据；当前已改为通用描述，**历史仍保留**，未宣称已从历史抹除。公开 Markdown 未匹配个人邮箱或用户目录路径；Git 作者使用 noreply 邮箱。未发现需吊销的真实秘密，未重写历史。
- `.gitignore` 实测覆盖个人 `.env`/变体、运行数据、日志、pytest/构建缓存和 Playwright 产物。合成样例、固定评估数据是有意公开的测试资产。origin 为无内嵌凭据/查询参数的 GitHub HTTPS URL。
- 真实测试必须显式 RUN_LIVE 开关；统一评估另外要求 `--live` 和 RUN_LIVE_EVAL。新验收测试同样默认关闭；云端追踪默认 false，独立 Key 为空，不因测试而自动开启。
- 后端锁文件 **77 项均精确固定**，pyproject 新框架版本有上下界；前端 lockfile v3，直接生产依赖精确固定，开发依赖有合理主版本约束。本阶段没有新增依赖。此项是锁定与兼容检查，不是完整第三方漏洞数据库扫描。
- README、接入文档及评估说明已更新，阶段 1–8 报告明确为历史快照；不再把真实 Agent 写成完全未验证，也不把 Fake RAG 或受控追踪说成真实集成成功。

**审计结论：在本次检查范围内，未发现阻止源码推送 GitHub 的凭据泄漏，具备推送源码供审阅的条件；本阶段不执行推送。AI 全栈真实集成验收尚未全部通过，仍缺真实 Embedding/RAG、LangSmith 云端接收及包含它们的真实浏览器全链路证据。** 未知旧秘密/其他编码形式无法由有限模式扫描证明不存在；如后续发现真实秘密，需先吊销/轮换并评估历史清理，不能只删当前文件。

## 本阶段改动与交付状态

只新增显式 opt-in 真实链路测试、只读历史审计脚本和验收文档，更新公开说明，共 9 个文件；生产执行代码与依赖未改动。已完成当前配置下可执行的验收和审计；缺少外部配置的项目按阻塞交付，不标记为通过。基线所有提交和本阶段待提交文件均已扫描；阶段 9 提交可用 `git log -1 --oneline` 查询，提交后的干净状态在最终交付消息记录。没有 push、merge、Release 或公网部署。
