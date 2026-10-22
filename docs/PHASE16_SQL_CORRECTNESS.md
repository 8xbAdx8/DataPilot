# 阶段 16：先证据、后整改

起点为干净的 `85ac25bd70d8ccf9a66b5d20e1b7d5f1f9c045dc`，分支 codex/agent-rag-upgrade。本地领先远端 3 个提交；实际 ls-remote 仍为 ae451900d34241736ab1815b32183ce6b5846592。本阶段不推送。

## 旧失败逐题根因（修改生产代码前记录）

完整问题、预期、逐题检索、Agent 计划/工具/调用/Token、SQL、校验与实际行保存在 [原始证据摘录](phase16-root-cause.json)，附原始文件 SHA。阶段 15 沿用阶段 14 的这次留出结果，没有另一套“阶段 15 留出”。

|题目|人工预期与约束|原 0.20 基线|选定 0.70 方案|
|---|---|---|---|
|h-p-02：按定义求目前可以售出的库存件数，只输出 metric。|SUM(quantity)，status='available'，仅 metric 一列/一行=30，无分组/排序要求|值实际30，别名 available_inventory 不符，严格失败|SQL 已正确使用 metric；AST批准；execute节点 query_timeout，rows=null|
|h-p-04：遵循已登记口径求可售库存价值，只输出 metric。|SUM(quantity*unit_cost)，status='available'，仅 metric 一列/一行=80，无分组/排序要求|AST批准后 query_timeout，无实际结果；SQL 别名 available_inventory_value 也不符|同样 AST批准后 query_timeout；SQL 别名仍 available_inventory_value，次要契约缺陷仍在|

合成库存四行 quantity=10/20/5/NULL、unit_cost=2/3/2/4，前三个 available 记录中的 10+20=30、10*2+20*3=80；transit 的5排除，NULL按SUM忽略。原始预期没有发现算术或语义标注错误，保留原版。

h-p-02 的选定方案初始检索/最终采用均为可售库存 chunk 8201c637-b09d-5d85-9909-5a380ee41c99，相似度0.740787。h-p-04 初始语义 no_match（原相似度0.678461低于0.70），Agent 实际 get_metric_definition 找到可售库存价值 chunk 015d636d-1429-598f-8826-f6f79c73ac88 并采用/引用，精确查询的 similarity=null 是未计算而非零。两题 native tools 均为2模型/2工具，未耗尽3/4上限，零修复；计划中的过滤与公式正确。没有证据支持错误知识或预算问题是这两道失败的原因。

严格评分沿 summarize_rag → same_rows：实际列表必须存在且行数一致，每行键集精确一致，数值 isclose(rel/abs=1e-8)，行序按列表比较。rows=null 首先失败；旧 h-p-02 基线实际键集不等也失败。旧比较器未单独验证空结果的列描述，亦未比较非空结果的列顺序，这是评测诊断覆盖缺口，不影响这两道 null/别名失败的真实性。新版本应明确列顺序、排序契约及状态，不能放宽旧分数。

历史评测 configuration.langsmith_enabled=false，没有可靠对应 run ID；阶段15后来的真实浏览器追踪属于另一条分析，禁止挪用关联。

### 失败分类

- A 计算语义：两题生成的公式/过滤与标注一致；超时不能证明实际数值正确，原结果仍失败。
- B 输出契约：旧基线两题与选定方案 h-p-04 的别名不符；缺少提示词对问题输出约束相对计划中指标名的明确优先级。
- C 知识：价值题语义检索漏召回，但实际精确指标工具补足，最终采用正确；不是最终超时根因。
- D Agent：计划、来源、工具预算未发现问题。
- E 歧义：原两题明确要求metric且知识已登记，无需改标注。
- F 评测/标注：原严格失败成立；新评测补齐可诊断列描述/列顺序，保留旧比较器与分数。
- G 执行基础设施（A–F以外）：两题主失败为 query_timeout；下述独立合成实验确认当前入口存在可重复的 Windows spawn 重导入开销。

### 独立超时诊断（不是重新评旧题）

同一个合成 quantity*unit_cost 查询，轻入口 child target 172.455ms、结果1549.851ms，原执行器1491.224ms成功。只在入口顶层加入与旧CLI相同的 app.rag_evaluate 导入后，spawn 子进程重导入 Web/Agent 栈：target直到5684.936ms才进入，实际结果5716.638ms，进入target后的计算/返回仅约31.7ms；原执行器5秒限制5036.152ms返回query_timeout。实验使用独立合成文件，未调用模型或改旧分数。Windows诊断表明重型main重导入足以产生该错误，不能反推旧运行每次的唯一耗时来源。

## 有证据支持的最小整改范围

1. 以独立、轻量、隔离的 Python 子进程启动 DuckDB worker，避免 multiprocessing spawn 重放父入口；仍以5秒含启动的截止时间、双槽、只读、环境清理、外部访问关闭、行/字节上限执行，不引入常驻池或重试。
2. SQL Provider 系统契约明确问题指定的输出别名、列顺序、过滤、排序/Top-N、NULL含义必须保留；Agent 的指标说明不能覆盖用户结果格式。仅澄清规则，不拼装/改写固定SQL，也不机械改别名掩盖语义错误。
3. 新增独立严格评测器，区分语义结果、列集/列序、行序、状态、引用、安全零执行；旧评测文件不改。新题集先人工复核，再开发评测；冻结后留出只跑一次。

新题集与原库存两题不同域/意图组合，旧两题只作根因资料，不转作新的留出。正式准确率取决于后续真实调用，不由提示词或离线测试推断。

## 实施、人工复核与冻结

生产整改仅 `execution.py`（启动/超时回收）、新增 `query_worker.py`（搬出原DuckDB逻辑，轻量JSON入口）及 `providers.py`（5行输出与NULL语义提示）。`sql_guard.py`、`analysis_agent.py`、`analysis_graph.py`、`knowledge.py`、`retrieval.py`、`config.py` 与起点字节完全相同；未扩大工具/Token预算或修复次数，没有新依赖、迁移或前端应用改动。容器四个关键模块指纹也与本地一致。

新 `sql_evaluate.py` 使用独立 PostgreSQL `_test` 库与合成文件，经真实FastAPI认证/所有权、原生Agent、Embedding/RAG、SQL校验/真实DuckDB、历史读取完成，逐题保存原始SQL、columns/rows、初始检索、计划/来源、节点/校验、状态与用量。外层事务最终核对所有表计数回滚，临时分析文件随目录删除。它不向模型提供参考SQL、期望值或标签。

独立开发17题/留出13题，经用户“已复核，确认标签与意图分组”确认。没有同义改写交叉；订单与工时不同字段域，按完整意图组合而非单个SQL函数分组。COUNT/SUM等原语共享是必要能力覆盖，不声称统计独立或外部盲测。原库存两题不进入新留出。

参考SQL的确定性执行首次6 passed/1 failed：h04导入后work_date是TIMESTAMP，直接投影返回T00:00:00，而标签要求DATE字符串。这是新题集F类参考契约错误，不是模型错。完整保留 [v1](../backend/app/evals/sql-correctness-v1.json) 及人工receipt；新增 [v2](../backend/app/evals/sql-correctness-v2.json)，仅明确h04的DATE显示并CAST，用户再次“确认 h04 日期契约修订”。之后7项评测器/参考测试通过，才运行真实开发集。见 [v1复核](SQL16_LABEL_REVIEW.md)、[v2修订复核](SQL16_LABEL_REVIEW_V2.md)。两个receipt分别绑定确切JSON和CSV SHA，创作快照中的pending字段不是有效审核状态。

真实开发只运行一次，17/17规则通过；之后未调提示词、阈值或预算。回归与格式检查完成后创建 [冻结清单](sql16-freeze-v2.json)，绑定v2语料、两个CSV、16个实现模块、SQL提示词模块及实际配置；父HEAD为85ac25b，dirty=true表示待提交实现。正式留出使用该清单一次，输出和本地claim独占创建，失败也消耗一次运行资格。提交后由 [LF格式映射](sql16-source-format.json) 对照实际运行的原始字节SHA和Git规范化源码；execution.py原工作副本含CRLF，未因此重写冻结记录或重跑。

## 真实开发与一次留出结果

|指标|开发|冻结留出|
|---|---:|---:|
|严格SQL结果正确|12/12|7/8（87.5%）|
|查询成功|12/12|7/8|
|列集 / 列顺序 / 带行序结果，各自以全部SQL题计|12/12|7/8|
|在实际完成查询中，完整输出契约通过|12/12|7/7|
|预期澄清题正确澄清|2/2|2/2|
|登记业务指标来源准确采用并引用|3/3|2/2|
|全部题的来源集合一致（包括应为空的情况）|17/17|13/13|
|危险请求/无权数据集的零执行|3/3|3/3|
|整体规则|17/17|12/13|
|危险执行 / query_timeout|0 / 0|0 / 0|

分母明确保留未完成的SQL题，不以7/7已完成查询冒充留出全部通过；空结果也检查列描述，列顺序与行顺序都检查。来源指标同时核对plan采用集合和最终引用集合，不能只凭检索命中；全题13/13中包括无来源题，不等于13道独立业务指标题。两组无权数据集都404，无模型/执行；另一用户的合成知识未出现在初始/最终来源中，完整离线所有权回归另覆盖权限过滤。逐题见 [开发真实记录](sql16-development-real.json)、[留出真实记录](sql16-holdout-real.json)、[机器汇总](sql16-comparison-summary.json)。

唯一留出失败h03为已登记能力边界：按team聚合hours且HAVING SUM(hours)>=3。模型实际SQL为：

```sql
SELECT team, SUM(hours) AS total_hours FROM data GROUP BY team HAVING SUM(hours) >= 3 ORDER BY total_hours DESC, team ASC
```

问题无歧义、公式/投影/排序与人工预期一致，Agent 2模型/2工具且未采用知识；原SQLGlot白名单不支持HAVING，validate第0次unsafe_sql拒绝，轨迹止于prepare/retrieve/plan/generate/validate，没有execute、没有实际行、零修复。它仍计严格失败，不把逻辑上合理的SQL当作已成功计算，也不为得分扩展语法或再跑留出。本阶段工程回归通过，正式留出未全面通过。

已完成的业务题同时覆盖了语义而非单纯别名：结算净额按两个SUM分别处理NULL，真实37；不能改成SUM(amount-cost)得到41。核准人工费140与全部登记人工费190正确区分并各采用真实授权定义。

这组分数不能直接与旧库存0/2或阶段11–15其它题集比较：领域、意图和显式输出契约均不同，并有已知不支持的HAVING题；旧两题未重新调用模型、原始0/2与历史JSON不变。受控启动诊断和开发/留出成功消除了当前入口的已复现启动缺陷，但不证明任意复杂查询必然在5秒内完成或泛化准确率为87.5%。

## 实际调用、用量与隐私

DeepSeek deepseek-flash，独立百炼text-embedding-v4/1536。开发SQL12次+Agent33次、29工具；留出SQL8次+Agent25次、21工具，均来自实际工作流遥测。聊天Token开发56912、留出43195，合计100107，30题无缺失；包含澄清/拒绝Agent用量。Embedding实际HTTP开发19次/467 Token、留出15次/406 Token，共34次HTTP200/873 Token。费用、缓存计价、不可变模型部署版本未知，不估算金额。

HTTP观察器的chat列表仅捕获SQL Provider的20个同步请求；原生Agent SDK传输不在该钩子的覆盖范围，不能将它声称为全部网络请求数。58个Agent调用/其Token是工作流记录的调用与提供商usage，不是HTTP钩子的独立线包计数。报告保留这一限制，不补造58条HTTP记录。

逐题延迟完整记录：开发p50=4069.556ms，留出p50=4201.380ms；分析耗时分别合计61560.602/49223.329ms，不含启动/知识播种总开销，不能当作所有服务的总账单时长。真实服务只收到合成Schema、问题/合成业务定义，没有原始用户文件或原始用户行；数值预期从不进模型。LangSmith始终关闭，本阶段无新云端run，也不借用阶段15记录证明本次运行；opt-in、脱敏、关闭不发送和故障隔离回归保留。

## 工程验证与失败记录

- PostgreSQL完整373 passed /6 live opt-in skipped，263.39秒；SQLite完整352 passed /27 skipped，171.12秒。包括Agent、RAG、AST安全、真实DuckDB、权限、超时/字节/空结果、临时PG库0005→head→0005→head保留业务行的回滚测试。跳过项按原配置保留，没有把本阶段失败改成skip。
- 执行器/Provider定向34 passed，评测器/参考标签修订后7 passed；均已包含最终完整回归，不额外相加。新增确定性回归验证重型main不重放、真实子进程超时被回收且归还槽、PYTHONPATH注入与凭据继承被阻断、SUM的NULL语义差异、空结果列描述和错误/拒绝区分。
- Ruff check/format（77文件）、两套pip check通过；前端lint/typecheck/生产build通过。没有新增依赖、锁文件或迁移。
- 独立生产Compose栈Mock/Fake冒烟全部通过：健康、上传、真实SQL/结果、pgvector知识CRUD/引用和账户会话；upgrade head重复执行与check通过。Edge Playwright13 passed /1 live opt-in skipped（1.6分钟），本阶段没有重复真实浏览器或LangSmith专项，不冒称它们是本阶段新证据。
- 首次Docker Hub拉取认证连接超时导致构建失败；保留日志，改用官方公开ECR基础镜像后实际构建、启动成功，未关TLS。代码/镜像关键文件SHA一致，临时容器无三把Key且trace=false。测试后核对精确项目标签/两个卷名，清理仅datapilot-phase16容器、迁移任务、网络及两卷；原datapilot-upgrade-e2e三个服务仍healthy，原生库未清理。

## 实际命令与复现边界

```powershell
$env:PYTHONPATH='backend'
# 配置EVAL_DATABASE_URL为独立已迁移、以_test结尾的PG数据库；不得指向业务库
$env:RUN_LIVE_SQL_EVAL='1'
.venv/Scripts/python -m app.sql_evaluate --live --split development --output docs/sql16-development-real.json
.venv/Scripts/python -m app.sql_evaluate --write-freeze docs/sql16-freeze-v2.json
.venv/Scripts/python -m app.sql_evaluate --live --split holdout --freeze docs/sql16-freeze-v2.json --output docs/sql16-holdout-real.json

# 两次全量分别设置/移除TEST_DATABASE_URL、TEST_POSTGRES_URL，指向测试库
.venv/Scripts/python -m pytest backend/tests -q -p no:cacheprovider --basetemp=data/runtime/phase16-complete-pg --tb=short --show-capture=no
.venv/Scripts/python -m pytest backend/tests -q -p no:cacheprovider --basetemp=data/runtime/phase16-complete-sqlite --tb=short --show-capture=no
.venv/Scripts/python -m ruff check backend infra/audit-git-history.py
.venv/Scripts/python -m ruff format --check backend infra/audit-git-history.py
.venv/Scripts/python -m pip check
data/runtime/eval-venv/Scripts/python -m pip check
# frontend/: npm run lint; npm run typecheck; npm run build; npm run test:e2e
# 浏览器DATABASE_URL和E2E_BASE_URL均指向同一临时栈
$env:COMPOSE_PROJECT_NAME='datapilot-phase16'
$env:COMPOSE_FILE='docker-compose.yml;infra/compose.test.yml;data/runtime/phase16-compose.yml'
docker compose --env-file .env.example build
docker compose --env-file .env.example up --wait --wait-timeout 240
.venv/Scripts/python infra/compose-smoke.py
docker compose --env-file .env.example exec -T backend alembic upgrade head
docker compose --env-file .env.example exec -T backend alembic check
docker compose --env-file .env.example down --volumes
```

上面是本轮命令记录，原输出/冻结/claim不得覆盖或删除来重跑正式留出。需要新实验应另立版本、人工复核与冻结；常规回归不联网，勿在普通CI设置真实开关。临时override和日志在被忽略的data/runtime中，复制端口/项目隔离方式见[运行文档](OPERATIONS.md)，不要直接复用可能占用的端口或其它项目数据卷。

## 推送前审计与交付

提交前可达27提交/425文件版本审计，当前三把Key精确命中0、禁止路径0；模式候选仍为已复核的48个赋值/35个凭据URL/12个非敏感历史路径组，没有新增真实密钥。待提交文件另扫描三把Key、私有Embedding地址、凭据/个人路径；只提交合成CSV和公开报告，.env、模型缓存、测试数据库、日志及运行产物均忽略。没有重写历史、推送、合并、发布或公网部署。

最终本地提交SHA和提交后审计/干净状态由最终交付消息给出，可用`git log -1 --format=%H -- docs/PHASE16_SQL_CORRECTNESS.md`定位。满足工程与凭据审计条件后可供审核推送，但HAVING仍是能力边界，正式留出7/8而非全部通过；旧RAG误召回等阶段14风险也不因本专项而消失。

交付复核发现新语料/CSV在Windows创作时使用CRLF，而默认Git规则会转LF，导致另一机器的人工receipt SHA失效。因此仅为新sql-correctness JSON/receipt与sql16合成CSV设置`-text`，保留已确认的原始字节；未改题目、标签、冻结SHA或重新评测。提交前逐个核对Git暂存对象与receipt一致；其它源码仍遵循原LF规则，源码的格式映射单独记录。
