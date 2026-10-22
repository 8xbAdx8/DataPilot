# 阶段 10：真实 Embedding 与语义 RAG 验收

验收日期：2026-10-20（Asia/Shanghai）。基线 HEAD `0e7b1c7bb3fca440f94c3239c9d7748b591f1169`，分支 `codex/agent-rag-upgrade`。阶段开始时工作区干净，执行真实验收时已有本阶段测试与报告草稿未提交；未改生产执行代码或依赖。

**结论：百炼真实 Embedding、pgvector 语义检索/权限/生命周期、DeepSeek 原生 Agent + RAG + SQL 和生产容器浏览器链路通过。LangSmith 云端仍关闭、未验证；统一 29 题真实评估及泛化准确率未测量。**

## 验收计划与边界

先推送用户指定的基线功能分支，再检查独立配置和数据库，建立真实专项并先验证默认关闭；配置齐备后只用合成数据执行真实请求，保留失败记录，最后运行回归、审计、清理和本地提交。不使用 Fake 冒充真实服务，不降低检索阈值或 SQL 安全要求，不启用云端追踪，不合并 main 或部署公网。

初检 Embedding 为 none、缺独立 Key。用户在本地配置百炼后，助手仅将仍为旧默认值的两个非敏感字段改为 openai_compatible / text-embedding-v4，保留用户填写的地址、Key、其他配置。之前按用户反馈追加过缺失的六个模板字段；没有覆盖已有凭据，真实 Key 不在报告、命令或 Git 中。

| 项目 | 实际环境 |
| --- | --- |
| Embedding | 阿里云百炼，北京工作空间 HTTPS 兼容地址，openai_compatible / text-embedding-v4 |
| 向量 | 配置、返回值、PostgreSQL 列均为 1536；pgvector 0.8.7；模型响应必须匹配配置，数值有限、归一化非零 |
| 聊天 | DeepSeek / deepseek-flash；独立 Key，不复用给百炼 |
| 数据库 | 原生独立 datapilot_test，迁移 0006；真实专项外层事务最终回滚 |
| 临时真实容器 | datapilot-phase10-live；独立 PostgreSQL/数据卷，端口 25432/28000/23000，仅回环地址；迁移 exit 0、三服务 healthy |
| LangSmith | 显式 false，无云端请求或云端接收证据 |

所有知识、字段、问题、订单及测试账户均为合成。没有上传原始用户行、文件或业务知识。只读观察真实 Provider 返回和原生 tool_calls，不替换网络、向量、工具响应或 SQL。

## 首次失败与后续验证

首次显式真实专项 **3 failed / 2.50 s**：Provider 返回 embedding_http_error，知识状态为 failed；均在索引阶段停止，没有调用聊天模型或进入 SQL 执行。原实现未导出供应商状态码，因此不猜测首次 HTTP 状态或具体原因。

随后一条英文合成文本诊断请求得到 **HTTP 200 / 551 ms**，返回 model=text-embedding-v4、1536 维。基于这项新证据，分步重跑并设置 maxfail=1，结果为 **3 passed / 13.98 s**。首次失败摘要另存忽略目录，未把失败改成 skip，也未用自动 SQL 修复或 Fake 绕过它。根因未独立确定，不宣称修复了某个已知服务故障。

## 真实 Embedding 与语义检索

实际入口：backend/app/embeddings.py、knowledge.py、models.py；验收：backend/tests/test_live_rag.py。

合成定义“净贡献额”：仅统计 status='paid' 的订单，SUM(revenue)-SUM(cost)，退款不计入。测试提问：

> 只算已付款交易，把收到的钱扣除花出去的成本，剩下的数额怎样计算？

这个问题没有复用指标标题。真实向量检索命中实际定义，相似度 **0.735559**。逐条核对 chunk_id、document_id 在 PostgreSQL 中存在且指向授权条目，摘录等于实际存储内容。默认阈值未降低；这是一个合成语义改写案例，不能写成大规模 Recall/准确率 100%。

生命周期专项通过：

- 三个作用域各自用真实接口索引同内容知识：当前用户当前数据集、同用户另一数据集、另一用户数据集。检索仅返回各自授权条目；跨用户读取/删除、搜索外部数据集返回 404。
- 重复导入返回原 ID 和 duplicate=true，不新增 Embedding 调用。
- 更新到 version 2，重新请求真实向量；旧 chunk ID 从数据库移除，新检索指向新版实际内容。
- 临时清空测试 settings 的 Key 作**本地故障注入**，重索引明确 failed / embedding_not_configured，无外部请求；失败文档不进入检索。
- 恢复独立 Key 后真实重索引 ready / version 4；删除后条目与 chunks 清理，后续检索 no_documents、空引用。该注入不冒充真实服务超时或故障。
- 测试最后回滚独立库，引用是验收当时存在的合成条目，清理后不能作为线上永久 URL 使用。

该专项记录 **8 次成功 Embedding 请求**，每次输入 1 条，4214 ms 包含 API/数据库/上传与断言。另一个真实 Agent 专项记录 2 次成功 Embedding 请求。共 10 条详细耗时为 382、422、391、382、378、375、560、360、357、387 ms，中位数 **382 ms**、范围 **357–560 ms**。基础专项另成功请求一次；诊断和首次失败单独列示，不混入这 10 条统计。

## 真实 Agent、LangGraph 与 SQL

合成 CSV：

```csv
region,revenue,cost,status
East,10,4,paid
West,20,8,paid
East,30,12,refund
```

真实百炼索引/检索 → DeepSeek 原生 inspect_dataset_schema、get_metric_definition → 原生 AnalysisPlan 选择实际 chunk ID → 真实 SQL 生成 → 原 SQLGlot/Schema 校验 → 受限 DuckDB 执行。

```sql
SELECT SUM("data"."revenue") - SUM("data"."cost") AS "net_contribution"
FROM "data" AS "data" WHERE "data"."status" = 'paid'
```

实际结果 **net_contribution=18**，与 (10+20)-(4+8) 一致，退款行排除。Graph 路径为 prepare → retrieve → plan → generate → validate → execute → check_result；approved / attempt 0，修复 0 次。RAG source=provider / used=true，引用相似度 0.669632，真实知识 ID 被计划选择，历史、CSV、开始/成功审计检查通过。

- Agent 模型请求 2 次，实际工具 2 次；输入 2780、输出 268、总 3048 Token。
- SQL 模型请求 1 次；输入 567、输出 66、总 633 Token。
- 合计 3 次聊天模型请求、**3681 Token**，该专项耗时 **7577 ms**。
- Graph 检索 411 ms、规划 3717 ms、生成 1075 ms、校验 64 ms、DuckDB 1647 ms；不是跨平台性能承诺。

## 真实生产容器浏览器 E2E

新增 frontend/e2e/live-rag.spec.ts，RUN_LIVE_BROWSER_RAG=1 才运行。实际复用已构建的生产 Next standalone/后端/PG 镜像；生产执行代码相对基线未改。使用独立容器网络与正常 Next 同源代理，**无 Playwright 请求拦截、路由替换或响应伪造**。

Edge 实际完成账户认证、上传、在 UI 添加指标知识并等待真实索引、提问、ECharts、来源检查、导出、刷新历史恢复，**1 passed / 21.0 s（测试体 16.3 s）**。

问题要求按 region 统计净贡献额。模型实际生成单表分组 SQL，筛选 paid，结果为 **East=6、West=12**；页面柱状图、结果表、CSV 和历史完全一致。来源文档经真实 GET 确认存在、内容匹配，页面不显示 Fake 向量提示；RAG source=provider / used=true，相似度 0.664374。

本次 Agent 2 次模型请求、2 次工具，2972 Token；SQL 1 次、651 Token；合计 **3623 Token**。检索 1794 ms、规划 3386 ms、生成 2257 ms、校验 36 ms、DuckDB 1192 ms。浏览器流程的 Embedding 网络计数和 Token 未另作汇总，不从其他数字推算。

两个分析案例实际聊天请求合计 6 次、提供商报告 **7304 Token**。当前 Embedding Provider 接口没有导出 Token 用量，本轮未采集；**金额未知**，不按静态价格估算账单。

测试清理自己的数据集并退出；随后 down --volumes 仅清理本轮新建临时栈、网络和两个合成数据卷，容器中的环境凭据随容器移除。截图与无凭据摘要保存在忽略目录，已人工检查图表和来源。原开发服务及 Mock/Fake 隔离栈保持原样。

## 回归结果

普通回归显式关闭全部 RUN_LIVE 开关、LLM_PROVIDER=mock、LangSmith=false。不删除旧测试、不降低断言、不将失败项改成 skip。

| 检查 | 实际结果 |
| --- | --- |
| PostgreSQL 全套 | **328 passed / 6 skipped，162.44 s**；新增两个 live 专项默认跳过，真实专项已单独运行 |
| SQLite 全套 | **310 passed / 24 skipped，112.56 s**；额外 18 项需要 PostgreSQL |
| SQL/授权/故障回归 | 包含在全套：危险/多条/未知表列、零执行拒绝、有限修复、超时/结果上限、每次工具授权、知识竞态与假引用、跨用户边界 |
| 前端 | lint、生产 build、tsc --noEmit 均通过；新增测试后再跑 lint/typecheck |
| 默认浏览器回归 | 生产 Mock/Fake 栈 **13 passed / 1 skipped，1.3 分钟**；跳过新增真实浏览器专项，不拿它充当真实证据 |
| 迁移 | upgrade head、check 通过；全套含临时库 0005 → 0006 → 0005 → 0006 与平台行保留 |
| 静态/依赖 | Ruff check / format、pip check、diff --check 通过 |
| LangSmith 云端 | **未运行/未验证**，本阶段未开启 |
| 统一真实评估 | **未运行**，不能引用离线 29/29 作为本次真实模型准确率 |

## 实际命令

从仓库根目录运行，测试数据库 URL 在私有环境中指定，明确为独立 datapilot_test，不回显连接串。真实专项不设置 LLM_PROVIDER=mock：

```powershell
$env:TEST_DATABASE_URL=$testDatabaseUrl
$env:RUN_LIVE_EMBEDDING='1'
$env:RUN_LIVE_RAG='1'
$env:RUN_LIVE_RAG_AGENT='1'
$env:DATAPILOT_LANGSMITH_ENABLED='false'
# 初次使用 phase10-live-tmp-2，3 failed；诊断成功后使用新的目录重跑：
.venv/Scripts/python -m pytest backend/tests/test_embeddings.py::test_real_embedding_requires_its_own_explicit_opt_in backend/tests/test_live_rag.py -q --tb=short --maxfail=1 --show-capture=no -p no:cacheprovider --basetemp=data/runtime/phase10-live-tmp-3
```

普通回归在独立子进程设 LLM_PROVIDER=mock、六个后端 RUN_LIVE 开关为 0、LangSmith=false；PG 两个 TEST 数据库变量指向独立库，SQLite 清除它们：

```powershell
.venv/Scripts/python -m pytest backend/tests -q --tb=short --show-capture=no -p no:cacheprovider --basetemp=data/runtime/phase10-pg-tmp-1
.venv/Scripts/python -m pytest backend/tests -q --tb=short --show-capture=no -p no:cacheprovider --basetemp=data/runtime/phase10-sqlite-tmp-1
.venv/Scripts/python -m ruff check backend infra/audit-git-history.py
.venv/Scripts/python -m ruff format --check backend infra/audit-git-history.py
.venv/Scripts/python -m pip check
.venv/Scripts/python -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python -m alembic -c backend/alembic.ini check
.venv/Scripts/python infra/audit-git-history.py
git diff --check
```

真实浏览器使用忽略的 data/runtime/phase10-compose.yml 覆盖端口/可信 Origin/实际提供商，凭据只由原 .env 注入后端，不写 override、不执行明文 compose config。前端明确复用缓存生产镜像 datapilot-upgrade-e2e-frontend：

```powershell
$env:COMPOSE_FILE='docker-compose.yml;data/runtime/phase10-compose.yml'
$env:COMPOSE_PROJECT_NAME='datapilot-phase10-live'
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example up --no-build --wait --wait-timeout 90
# frontend/ 目录，DATABASE_URL 指向隔离 PG25432，用于 CLI 创建合成测试账户：
$env:E2E_BASE_URL='http://127.0.0.1:23000'
$env:RUN_LIVE_BROWSER_RAG='1'
npm run test:e2e -- e2e/live-rag.spec.ts
# 回到根目录，验收后仅清理该临时项目：
docker compose --env-file .env.example down --volumes
```

前端另运行 npm run lint / build / typecheck；默认回归 E2E_BASE_URL=13000、CLI 数据库 PG15432、RUN_LIVE_BROWSER_RAG=0，执行 npm run test:e2e。后续复验必须用新临时目录，避免 pytest 清理已有目录内容。

## 推送、密钥和交付审计

- 用户指定的功能分支已推送至 GitHub，官方 REST 核验远端 SHA 为基线 0e7b1c7；对应 [Actions 39412395408](https://github.com/8xbAdx8/DataPilot/actions/runs/39412395408) 实际 completed/success。本阶段后续改动只作本地提交，**未再次推送**，不把基线 CI 当成本阶段 CI。
- 复核基线所有可达 **21 个提交 / 337 个文件版本**：当前两把实际 Key 在内存中精确比较，0 命中；无私人 .env、私钥、运行数据库或日志进入可达历史。源码和本阶段待提交文件另做扫描。审计扩展了带点号的工作空间 Key 格式，合成格式检查通过，不输出匹配值。
- 广义 URL/赋值告警仍为开发示例、测试固定值及运行时表达式；报告未公开个人工作空间域名、真实 Key、会话令牌、数据库连接串或原始用户数据。未重写历史。
- .env、runtime JSON/截图/override、测试结果与构建产物均被忽略。真实后端专项和浏览器专项默认关闭；云端追踪默认关闭。
- 未增加依赖、未修改生产 SQL/授权/执行策略。只增加 opt-in 验收、完善密钥格式扫描和更新文档；所有新生产外部行为均通过现有接口实测。
- 真实语义与浏览器结果只证明本页固定案例。第三方中断/超时的全面真实故障演练、并发容量、其他地域/提供商、真实用户数据和统一真实评估仍无证据。HTTP 首次失败原因未确认，费用未知。

私有证据：data/runtime/phase10-test_real_semantic_retrieval_permissions_and_lifecycle.json、phase10-test_real_semantic_rag_reaches_native_agent_and_duckdb.json、phase10-real-browser.json / .png；首次失败另存 phase10-attempt1-*。这些是合成验收产物，不进入 Git。

## 百炼配置参考

北京/默认业务空间可用以下配置，Key 只在本地填写，保留 LLM_* 的 DeepSeek 配置：

```dotenv
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=
EMBEDDING_DIMENSIONS=1536
EMBEDDING_TIMEOUT_SECONDS=20
```

本次实际使用控制台提供的北京空间专属 HTTPS 根地址（含 /compatible-mode/v1）。官方仍支持上面的通用地址，同时建议空间专属域名；Key、模型、空间和地域需匹配，不在 Base URL 末尾添加 /embeddings。text-embedding-v4 默认 1024 维，项目显式请求 1536；变更维度需迁移和重嵌入。

依据：[百炼 Key 说明](https://help.aliyun.com/zh/model-studio/get-api-key)、[官方兼容 Embedding/1536 维](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai)。DeepSeek 继续用于聊天规划和 SQL，不把未找到的公开 Embedding 能力当作已支持。
