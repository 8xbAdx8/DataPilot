# 阶段25：真实Agent、证据采用与SQL闭环

## 范围与结果

起始分支`codex/agent-rag-upgrade`，HEAD `48ca7654b3bba04a35ab55ad8a54d4bfd0104546`，工作区干净。阶段24真实向量、单次留出分数及冻结哈希离线复核verified，原159份文档与fixture的字节哈希基线单独保护。

**开发范围PASS**：第二轮8/8，其中2题有真实原生Agent选源、真实SQL生成及DuckDB结果；其余6题在实际数据库证据或API权限门禁提前澄清拒绝，没有模型调用及SQL执行。不是8次真实模型安全测试，不是独立端到端留出通过。生产默认保持`legacy/vector/0.70/none`。

本阶段使用原`PlannerContext`、`plan_analysis`、LangChain `create_agent`/`AnalysisPlan`原生结构化工具、LangGraph、SQLGlot/Schema、`MetricRegistry.check_sql`、子进程DuckDB执行器。验收器在独立进程组合这些原组件；**生产`/analyses`完整API路径NOT_RUN**，候选生产接入留待阶段28，不提前增加功能开关。

## 人工标签与预期值

[8题标签](PHASE25_LABEL_REVIEW.md)已收到用户“已复核阶段25标签”，绑定回执位于`backend/app/evals/phase25/development-v1.review.json`。语料SHA：`6a50097a561fd415fd8e620f75e04faa854dee81c8bd2fca4284633d21e2b37f`。数据仅4行、3字段，8条知识覆盖正式名/别名、相似指标歧义、结构化事实、缺失定义、跨用户/数据集、注入及缺少必需字段。没有原用户业务数据。

两题手算：closed行的fee之和30、cost之和5，净额25；`SUM(fee)-SUM(cost)`与`SUM(fee-cost)=8`不等价。有效状态事实规定`state='closed'`，因此有效结单3行。期望列分别`net_amount`、`row_count`，不改人工标签；评测器将原API的列对象/行字典规范化为标签矩阵比较，保留原始返回值。

## 真实两轮与失败保留

| 轮次 | 变体 | 结果 | 真实Agent/SQL | 提前拒绝 |
|---|---|---:|---:|---:|
| [development-01](phase25/development-01/summary.json) | 原C逻辑/vector/.70/none | **7/8，PARTIAL** | 净额25成功；事实题未进入模型 | 6题正确，事实题额外错误澄清 |
| [development-02](phase25/development-02/summary.json) | **仅本阶段开发变体C25**/vector/.65/none | **8/8，PASS** | 净额25、行数3，均实际执行 | 6/6，execute=0 |

第一轮正确事实来源的真实余弦为0.6800053119659424，低于.70；候选为空，系统保守澄清，**不是SQL计算错误**。完整[失败归因](phase25/development-01-failure-analysis.json)另存，第一轮记录不覆盖。只在已审核开发集上比较.65变体，原闭合事实的主体+属性、必需字段、歧义与授权检查保留；没有改问题、语料、标签、原阶段24C的分数或生产默认，也未提前开发自由文本D。

第二轮前先通过25项相关离线/PG回归；现有已用聊天1831输入Token加整轮保守预留5989，共7820≤8000，才允许新一轮。两轮只复用原14个真实向量，无新Embedding。未来独立终验必须冻结实际使用变体，不得将.65开发结果写成原.70泛化成绩。

成功题逐环实际轨迹均为：`prepare → retrieve → plan → generate → validate → execute → check_result`。模型自行产生`AnalysisPlan` tool call及实际chunk ID，不是测试预写调用结果。SQL模型收到真实Schema、模型实际计划、适用来源正文及登记指标合同；SQL必须通过原AST/Schema校验，指标题再检查聚合表达式与必需AND过滤。事实题由最终真实结果核验过滤效果；不能把事实正文宣称为已登记SQL指标公式。

原SQL安全策略没有变化。确定性回归专门确认`OR 1=1`、错误NULL公式和别名/OR绕过均被指标合同拒绝，execute=0；旧版本来源在独立夹具测试中拒绝。验收器选源前使用原授权检索，规划后及SQL门禁检查实际数据库的owner、dataset、ready、正文、模型空间和snapshot版本；使用fresh populate_existing及行锁。owner/dataset保留在本地PlannerContext与数据库权限边界，来源ID/正文进入模型，版本由本地快照和门禁验证。

本轮规划最多1次模型响应，低于生产默认上限，已提供真实检索上下文后由模型原生AnalysisPlan工具直接完成选源；**普通只读Agent工具执行NOT_RUN**，没有增加工具、模型或修复预算。没有伪造缺失工具结果。Agent需要更多调用时会失败，不自动加预算。

## 真实预算账本

[发送前计划](phase25/budget-plan-v1.json)、[最终账本](phase25/ledger-final-v1.json)、[实际指标](phase25/metrics-v1.json)独立保存。固定共享SQLite为`data/runtime/phases25-29-live/ledger.sqlite`，未来阶段沿用，不能删除重建；原阶段21/24账本和旧入口未改。授权、局部及共享上限精确匹配；每次网络发送前FULL同步预留，未结束/未知/失败全局停止，不自动重试。缓存缺失不触发网络回退。

| 实际消耗（包含首轮） | 数量 |
|---|---:|
| DeepSeek `deepseek-flash`聊天HTTP | **6/12**（全局6/30） |
| 聊天输入Token | **5331/8000**（全局5331/20000） |
| 聊天输出/总Token | **833 / 6164** |
| 百炼`text-embedding-v4`、1536维HTTP | **2/8**（全局2/50） |
| Embedding输入Token | **381/2500**（全局381/15000） |
| 真实向量 | **14个唯一输入**，所有策略/轮次只读复用 |
| 完整usage/请求ID | **8/8**；未知请求0 |
| 原生AnalysisPlan调用/实际SQL执行（两轮总计） | **3 / 3** |
| 聊天HTTP p50 | **1050.958ms**，小样本，仅描述本轮 |
| LLM裁判 / LangSmith | **0 / 0** |
| 费用 | **unknown，无可核对账单，不按单价推算** |

本地固定tokenizer不是托管聊天模型精确分词器，完整消息及工具JSON按50%+128帧余量预留，实际usage才是事实；超预留立即保存已知usage并停止。最初整轮计划5989聊天输入预留、1969Embedding预留，均可容纳；没有为了通过而扩大预算。HTTP gzip只解码一次、无重定向/自动重试、缓存不可重复计费及线程安全有离线断言。

阶段21历史未知总usage和费用仍unknown；本轮完整性不代表核销历史。新框架没有引入，无新依赖、前端改动或业务迁移。

## 隔离、持久化与工程

专用Compose项目`datapilot-phase25`，PG仅监听127.0.0.1:15635，公开`.env.example`测试配置，不接触原5432业务库。每轮在专用PG中另建随机临时数据库并执行原Alembic0006，真实API上传CSV、创建/索引8文档，API commit后第二连接看到2用户、3数据集、8文档/8向量块；引用检查后实际查询上传的DuckDB文件。两轮临时数据库均已删除，专用基库计数前后不变。合成认证会话只在内存，不输出到Git。

生产分析历史API未运行，临时库`analyses=0`与范围一致；逐题结果由验收器保存在新JSON，不伪造应用历史。原数据上传/知识创建/索引与跨用户GET 404确实经过API；6个拒绝的真实性来源在每题`evidence_kind`中区分。安全结论限本题集和明确的确定性回归。

命令包含：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python infra/verify-rag24-results.py
.venv/Scripts/python -m app.phase25_acceptance plan
# 专用Compose仅PG，不加载真实Key
docker compose -p datapilot-phase25 -f infra/compose.phase25.yml --env-file .env.example up -d --wait
# 独立库迁移与完整日志包装器在忽略目录
.venv/Scripts/python data/runtime/phase25-test-runner.py prepare
.venv/Scripts/python data/runtime/phase25-test-runner.py postgres final-full
.venv/Scripts/python data/runtime/phase25-test-runner.py sqlite final-full
# RUN_LIVE_PHASE25=1只对真实合成验收进程启用，均已执行，勿删除账本重放
.venv/Scripts/python -m app.phase25_acceptance embed
.venv/Scripts/python -m app.phase25_acceptance evaluate --round development-01
.venv/Scripts/python -m app.phase25_acceptance evaluate --round development-02 --threshold 0.65
.venv/Scripts/ruff check backend
.venv/Scripts/ruff format --check backend
.venv/Scripts/python -m pip check
# frontend: npm run lint / typecheck / build
```

首轮PG测试环境遗漏test库迁移而缺vector类型（67 passed/65 setup errors），补齐原迁移；新评测器最初误用行数组比较原行字典，已修复规范化，标签不变。之后相关PG132项与变体25项通过。最终工程结果、资源清理与密钥审计在完成后另存独立回执，本报告不提前声称未结束检查通过。

## 下一阶段准入与局限

阶段25开发目标满足后，还须最终全量回归、归属核验清理、密钥/Git审计与本地提交完成才进入26。阶段26只开始真实生命周期，不提前实现27自由文本或28生产接入。后续仍需：真实API生命周期/故障恢复、普通Agent工具风险覆盖、生产分析API集成、新独立端到端留出、自由文本候选与最终发布决策。本阶段PASS不授权生产切换、推送、合并或部署。

最终[工程回执](phase25/engineering-v1.json)：PostgreSQL **551 passed/6 skipped**（219.56s），SQLite **520 passed/37 skipped**（165.16s），跳过均为原PG或真实服务条件与新增原生pgvector条件测试；未删除旧断言或把失败改跳过。前端lint/typecheck/build、Ruff/format（107文件）及pip check均通过。专用PG容器健康检查与真实迁移通过；本阶段未改生产应用/前端，完整应用Compose冒烟和Playwright未重跑，仅明确引用阶段24同应用代码的既有CI，后续28/29须新跑。两轮临时数据库和专用容器/卷均已核实归属后清理，共享账本保留。

密钥及Git审计在本地提交前执行：旧36提交/624 blobs无当前Key精确匹配或禁入文件；新索引另扫描私有Key、数据库密码和私人Embedding主机。原159份历史文件SHA未变，阶段24离线分数与向量复核再次verified。当前阶段仅本地提交、无新远程CI；完整SHA在阶段交付消息中报告。
