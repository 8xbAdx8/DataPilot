# 阶段 12：评测契约与 Agent 稳定性

## 开始时的范围

起点 HEAD `702c48e32874aeb1e5f9a80f12193df1128e8273`，分支 `codex/agent-rag-upgrade`，工作区干净。用户授权先推送该提交，再启动本阶段。Git HTTPS 默认解析地址传输停滞，使用公开 DNS 查询得到的地址，通过单条命令的 `http.curloptResolve` 和 Schannel 完成推送；TLS 校验、系统 DNS 与全局 Git 配置保持原状。GitHub REST 已确认远端 SHA 一致，CI 状态后补。

本阶段的最小完整切片是：在原有预算和安全策略下让 Agent 为结构化计划预留最后一次响应，并用清晰输出契约的独立版本题集验证。保留阶段 11 的全部题目和评分，不追改历史分数。

实施与验收顺序：

1. 先冻结 `contracts-v2.json` 的开发/验证分组及 SHA-256，明确聚合别名、列集、排序、空结果和业务口径；验证组仅做一次正式真实运行，不按结果改题。它是小型回归验证集，不是独立盲测或泛化基准。
2. 最后一次模型响应或工具预算耗尽时，只保留 AnalysisPlan 结构化输出工具。缺必要信息时由模型提出澄清，不构造计算结果；模型若忽略限制则拒绝，不增加模型/工具/Token/修复预算。
3. 多工具响应在调度前检查整个批次的授权和剩余额度；越权或超额不执行其中任一工具。原工具仍逐次核验权限与预算，SQL 仍经原 AST/Schema 校验。
4. 修复评测的 Token 组合统计：明确 `sql_model_calls=0` 才将 SQL 分项视为未调用的 0；真正调用但用量缺失继续未知。保存 SQL/Agent 分项，保留原始结果评分。
5. 运行受控原生工具 HTTP 合同、真实 PG/SQLite、安全回归，再执行有界真实合成验证；失败照录，不重复抽样挑高分。LangSmith 关闭。
6. 新增检索负例保持当前阈值，明确报告误召回；本阶段不在已知单个负例上调阈值，也不宣称已经解决语义召回质量。
7. 更新文档、审计、创建本地阶段提交；不自动推送本阶段新提交。

冻结题集 SHA-256（任何模型或提示词修改前已写入并记录）：`df217f146d66731d2912b132ea18e0ff1976d970b074901daf8e13f540508bd0`。开发、验证各 6 题，标识不交叉；数据仍为原 4 行合成 CSV，不能称为独立新数据分布。

`702c48e` 的 [GitHub Actions 39439653579](https://github.com/8xbAdx8/DataPilot/actions/runs/39439653579) 最终 **success**，对应 SHA 一致。以下记录本阶段的新实现及单独验收，不冒充该远程 CI 已包含阶段 12。

## 实现与测量边界

`analysis_agent.py` 的 AgentBudget 每次提供剩余模型/工具预算；最后一次模型响应或工具额度耗尽时，把普通工具列表缩为 `[]`。已安装版本的 LangChain 会在 middleware 之后追加已声明的 AnalysisPlan 输出工具，因此实际 HTTP 合同验证最后一次仅有 AnalysisPlan。模型返回的整批调用在调度前检查未知工具与剩余额度，工具自身原来的所有权、时间和额度检查保留。模型忽略收敛约束时失败，不再调用工具或增加重试。上限保持 **3 次模型、4 次工具、64000 预留 Token**，SQL 修复仍最多一次并重新校验。

提示词中的剩余额度用于指导模型选择；服务端批次检查和各工具的权限校验才是授权边界。收敛并不把失败伪装成计划，缺口径时仍需模型输出带具体问题的澄清计划。

`evaluate.py` 新增 `--suite contracts-v2 --split development|validation|all`，默认仍为 legacy。原 29 题、原历史 JSON 和严格列名/行序/数值判定保持不变。只有遥测明确记录 SQL 调用数为整数 0 时，才把 SQL Token 分项视为未调用；已调用但未知用量仍为 null。逐题保留 SQL/Agent 分项及白名单用量，修复先前澄清题的组合计数缺口，不回填阶段 11 未采集的数字。

## 单次真实开发与验证结果

原始逐题记录、SQL、真实结果、实际知识引用、工具、用量与源代码指纹见 [evaluation-contracts-v2-live.json](evaluation-contracts-v2-live.json)。两个分组各仅运行一次真实基础/增强对照；开发组后没有再改提示词、实现、阈值或题目，验证组没有重抽样。

真实服务为 **DeepSeek deepseek-flash + 百炼 text-embedding-v4 / 1536 维**。独立原生测试 PostgreSQL/pgvector，真实 LangChain 原生工具 → LangGraph → SQLGlot/Schema → DuckDB。没有 SQL/模型/HTTP 响应替换；本地计时包装仅委托真实 Embedding 实现并记录耗时和维度。所有外部内容为固定合成 Schema/问题/人工知识，LangSmith 强制关闭。

| 指标 | 开发基础 | 开发增强 | 验证基础 | 验证增强 |
| --- | ---: | ---: | ---: | ---: |
| 全部契约规则通过 | 3/6 | 6/6 | 4/6 | 6/6 |
| 可回答题严格结果正确 | 4/5 | 5/5 | 4/5 | 5/5 |
| 可回答题生成/执行成功 | 5/5 | 5/5 | 5/5 | 5/5 |
| 缺失登记口径澄清 | 0/1 | 1/1 | 0/1 | 1/1 |
| 真实模型调用尝试 | 6 | 18 | 6 | 18 |
| 原生工具调用 | 0 | 13 | 0 | 13 |
| 已报告聊天 Token | 2143 | 21112 | 2178 | 21225 |
| 用量不完整题数 | 0 | 0 | 0 | 0 |
| 分析 p50 / p95（ms） | 3950.9715 / 5394.086 | 7881.723 / 11973.583 | 4568.327 / 5905.036 | 7089.791 / 9414.635 |

开发运行 UTC **14:37:45–14:39:16**，耗时 **90785.683 ms**；验证 UTC **14:40:02–14:41:22**，耗时 **80199.842 ms**。共 24 次分析，**22 次真实 SQL 模型 + 26 次原生 Agent = 48 次真实聊天调用尝试**，26 次实际工具调用。未注入模型故障。本批聊天响应报告合计 **46658 Token**；Embedding Token 与实际金额未知，不估算费用。

Embedding 共 **12 次请求均成功且 1536 维**：开发 8 次（p50 2094.76 ms），验证 4 次（p50 1976.902 ms）。净销售额来源相似度分别 **0.722078 / 0.717084**。两模式分别登记索引，基础模式不检索；因此基础模式也有 Embedding 索引费用。分析延迟不含上传、索引及额外的检索探测，不是吞吐量压测；运行中也有独立容器检查，不能把延迟差异归因于单一代码优化。

可复核的实际结果：

- 平均值与空结果开发题通过；验证 paid 行数 **3**、East 平均值 **20**、Top-N **East=30 / West=20**（只有约定两列）、2 月 revenue 总和 **30**，均来自 DuckDB。
- 基础模式的登记口径题开发返回 **36**、验证返回 **60**；增强均检索实际定义后返回 `metric=30`。源 ID 来自当次 PostgreSQL 知识，事务回滚后不再可在常驻工作台直接访问这些临时 ID。
- 两道缺口径题：基础模式为 `llm_invalid_response`，不是正确澄清；增强原生工具查询后在第三次模型响应只提供 AnalysisPlan，实际产生澄清，SQL 调用数 **0**，Token 分别 **4165 / 4067**。保留失败样本，没有把基础错误改为通过。
- 本批 Agent 没有工具/模型超限；每次调用不超过原 3/4 上限。最后计划收敛模式在两个缺口径题各进入一次，受控 HTTP 测试还验证忽略收敛约束时无法额外执行工具。
- 共 **20 条** SQL 实际进入 execute，均有最终 approved，额外重新调用原 AST/Schema 校验全部通过。两个基础缺口径题格式失败、两个增强缺口径题澄清，均未执行 SQL。两次运行相关 8 张表的记录数前后相同，测试事务回滚。

## 不能从本批结果推断的结论

- **不能把阶段 11 的 0/13 写成提升到 100%。** v2 列名、列集、排序和拒绝期望更明确，题数/覆盖范围也不同；阶段 11 原报告保留，旧 29 题本阶段没有重新调用真实模型。v2 是工程回归验证，不是独立盲测或未见分布准确率。
- v2 没有对抗安全题，安全通过率为 null；它不替代完整 SQL/Agent 安全回归，也不证明真实模型触发过危险 SQL 或修复分支。
- 开发组的无关知识查询本次返回 no_match，且模型未选入来源；**没有改检索逻辑或 0.20 阈值**。阶段 11 的旧问题相似度 0.213698 误召回风险保留，单个新问句通过不能宣称语义相关性已解决。需要独立正负检索集、阈值校准与留出验证，再决定是否增加重排。
- LangSmith 云端、Embedding 用量/账单、多数据集泛化与并发容量仍未验证；本次真实分析为服务端 TestClient 全链路，不冒充真实模型浏览器测试。

## 执行命令与当前回归

```powershell
# 推送重试最终仅使用单次 Schannel/HTTP1.1/curloptResolve 参数，无全局配置变更
git -c http.sslBackend=schannel -c http.version=HTTP/1.1 -c http.curloptResolve=github.com:443:<本次公开DNS返回地址> push origin codex/agent-rag-upgrade

$env:EVAL_DATABASE_URL=$testDatabaseUrl # 独立已迁移的 datapilot_test
$env:RUN_LIVE_EVAL='1'
$env:DATAPILOT_LANGSMITH_ENABLED='false'
# 实际计时包装调用以下 CLI；使用新的输出/检查点文件和仓库内临时目录
.\.venv\Scripts\python -m app.evaluate --live --suite contracts-v2 --split development --output data/runtime/phase12-development-live.json --checkpoint data/runtime/phase12-development-cases.jsonl
.\.venv\Scripts\python -m app.evaluate --live --suite contracts-v2 --split validation --output data/runtime/phase12-validation-live.json --checkpoint data/runtime/phase12-validation-cases.jsonl
```

首次 Agent/评测专项 **23 passed / 1 skipped**；PG 评测/Agent/图/SQL 专项 **94 passed**；最终预算提示的 Agent 专项 **15 passed**。旧测试未删除或降低要求，原离线 29 题及新 v2 严格 ground truth 均在 PG 测试中通过，明确不作为真实模型准确率。

独立 `datapilot-phase12-offline` 使用新后端镜像、独立 35432/38000/33000 端口和新卷；`env_file` 重置为空并显式 Mock/Fake、LangSmith=false，未注入用户 `.env`。新镜像实际包含 v2 12 题，Agent 文件 SHA-256 与本地一致。构建、启动健康、迁移、上传/真实 DuckDB、知识 CRUD/RAG/来源和账户冒烟通过；生产前端复用源码未改变的缓存镜像。完整 Edge **13 passed / 1 skipped**（真实浏览器专项默认关闭），未将 Mock/Fake UI 当作真实模型验收。

最终完整后端：PostgreSQL **337 passed / 6 skipped（267.88 秒）**；SQLite **318 passed / 25 skipped（149.87 秒）**。跳过项为默认关闭的真实专项及 SQLite 不支持的 PG 专项，不是将失败改为跳过。前端 lint、Next 生产构建、TypeScript 检查通过；Ruff check/format（62 文件）、pip check 与 git diff --check 通过。

容器中重复 `alembic upgrade head` 及 `alembic check` 成功，无新迁移差异；本阶段没有改数据库 Schema，未重复回退测试。浏览器完成后，先核实临时项目的两个卷名，再执行该项目的 `down --volumes`，成功删除本次创建的容器/网络/两个卷，原 `datapilot-upgrade-e2e` 三个服务仍运行。没有停止原生业务/测试 PostgreSQL，没有重置用户数据。

普通回归与容器实际命令（全部 Mock/Fake）：

```powershell
# 独立测试库环境，RUN_LIVE_* 关闭、LLM_PROVIDER=mock、LangSmith=false
.\.venv\Scripts\python -m pytest backend/tests -q -p no:cacheprovider --basetemp=data/runtime/phase12-pg-all --tb=short --show-capture=no
# SQLite 子进程清除 TEST_DATABASE_URL / TEST_POSTGRES_URL，使用新目录
.\.venv\Scripts\python -m pytest backend/tests -q -p no:cacheprovider --basetemp=data/runtime/phase12-sqlite-all --tb=short --show-capture=no

$env:COMPOSE_FILE='docker-compose.yml;infra/compose.test.yml;data/runtime/phase12-compose.yml'
$env:COMPOSE_PROJECT_NAME='datapilot-phase12-offline'
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example build backend
docker compose --env-file .env.example up --no-build --wait --wait-timeout 100
# SMOKE_BASE_URL=33000/api、SMOKE_REQUIRE_RAG=1
.\.venv\Scripts\python infra/compose-smoke.py
# frontend/，E2E_BASE_URL=33000、DATABASE_URL 指向隔离 PG35432、真实浏览器开关关闭
npm run test:e2e
npm run lint
npm run build
npm run typecheck
# 根目录，确认临时卷标签后清理本阶段项目
docker compose --env-file .env.example exec -T backend alembic upgrade head
docker compose --env-file .env.example exec -T backend alembic check
docker compose --env-file .env.example down --volumes
```

## 提交审计与结论

提交前历史扫描 **23 个提交 / 358 个 blob**，当前配置 Key 精确命中 0，禁止的 `.env`/日志/运行数据库路径 0。模式候选仍为既有公开示例和历史开发路径；新文件另作当前 Key、私有服务地址、密钥格式和新报告本机路径检查，未命中。真实原始分数与公开 JSON 逐项相同，两个源文件 SHA 与真实运行、镜像中的 Agent 一致。v1 题目和两份旧 JSON 经 Git 对照未改变。`.env`、计时包装、运行日志、临时 Compose 配置和检查点均被忽略，不提交真实凭据或运行数据。

本阶段仅本地提交；不自动再次推送、合并或部署。已完成的切片是预算内结构化收敛、调度前批次拒绝、Token 分项和版本化契约评测；固定小样本上有真实证据，不能将历史评估失败改写成全面验收通过。

下一阶段应先建立更多领域与表达的正负检索样本、明确 precision/recall 与无知识时的产品行为，再在独立开发集校准检索并冻结留出评测。旧问句的误召回、泛化质量、云端追踪、真实修复收益和并发容量仍需单独处理。
