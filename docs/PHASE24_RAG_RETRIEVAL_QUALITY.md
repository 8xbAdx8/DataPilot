# 阶段24：真实Embedding与RAG检索质量准入检查

**最终判定：BLOCKED。** 未开始真实检索实验，不能宣称真实质量验收通过，也不能把未运行记成0分或通过。日期2026-10-22，分支`codex/agent-rag-upgrade`。本轮先完成用户授权的推送与准确SHA的远程CI验收，然后执行原用量护栏；护栏拒绝后仅完成本地检查、离线回归和阻塞交付。

## 起点、推送与远程CI

起点HEAD及最终受验生产代码SHA均为 `0ff41fcc6899ae3cbfc250f7980d2abf59672655`，开始时工作区干净。本阶段没有修改生产/测试代码、依赖、配置、数据库结构或既有评测器；仅新增本报告、准入JSON并更新README。本地文档交付提交SHA由完成后的`git log -1 --format=%H`和最终回复给出，不把父提交CI冒充新文档提交CI。

推送前远端为 `d8063cf7480941239f71b187a824bdaffc152086`，`origin/...HEAD`为0/3，线性祖先关系。正常快进推送包含：

| 阶段 | 完整提交SHA |
|---|---|
| 21 | `4db494a8141b4a9866b32540e85dd384a84eb12d` |
| 22 | `7d42405211248f5b382eb34745dd72e67f62d880` |
| 23 | `0ff41fcc6899ae3cbfc250f7980d2abf59672655` |

GitHub API及实际Git分支核实远端完整SHA等于阶段23SHA，推送后左右计数0/0。未强推、改写历史、合并、打Tag、创建Release或部署生产。

[DataPilot CI运行39756176681](https://github.com/8xbAdx8/DataPilot/actions/runs/39756176681)，event=push、headSha与上表阶段23完整SHA一致，整个run completed/success：

| Job | Job ID | 最终结果及日志证据 |
|---|---:|---|
| application | 119748624825 | success；Ruff、迁移upgrade/check、PostgreSQL全量494 passed/6 skipped（103.24s）、前端lint/build、Playwright15 passed/3真实opt-in skipped（1.3min） |
| compose | 119748625213 | success；隔离构建/健康、HTTP上传/Mock SQL/真实DuckDB、Fake pgvector RAG SUM=60、密码与会话撤销均PASS；执行compose down |

此CI不调用真实模型，不能证明真实语义质量。完整运行/Job/step元数据及日志仅保存在忽略目录`data/runtime/phase24-parent-ci-*`，不把运行日志、账户或凭据提交公开仓库。

### 推送网络失败保留

首次普通push发生RPC连接重置；随后默认DNS路径的只读核对及普通重试连接github.com:443失败。GitHub API始终可读，先确认远端没有变化。公共DNS核对得到另一GitHub地址后，仅本次Git命令用`-c http.curloptResolve`临时解析，保持原HTTPS主机、证书校验和既有认证；先核对远端祖先，再正常快进push成功。没有修改系统DNS、全局Git配置、个人凭据或禁用TLS验证。未把失败输出中的“Everything up-to-date”当成推送成功。

## 原护栏实际检查

阅读阶段21/22报告、`METRIC_DEFINITIONS.md`、原始三份metric21报告及汇总、`metric21_acceptance.py`、`rag22_evaluate.py`、Embedding Provider、检索/证据筛选和评分代码。独立[准入回执](phase24-preflight-v1.json)保存原报告SHA、真实状态标记、公开配置摘要及相关源码SHA；这是只读准入证据，**不是新实时用量账本或新真实评测结果**。

在CI全部成功后，直接执行现有 `check_prior_usage(ROOT / "docs")`，实际得到：

```text
ValueError: Prior uncertain usage blocks additional live calls
```

三份历史文件的SHA均与既有`metric21-evidence-v1.json`一致，没有改写：

| 历史文件 | state | unknown_usage |
|---|---|---|
| metric21-baseline-v1.json | complete | false |
| metric21-development-live-v1.json | complete | false |
| metric21-legacy-live-v1.json | complete | true |

`state=complete`只表示收集结束，不代表usage完整。旧格式补充有一次未知聊天usage与一次Embedding失败未知usage；聊天已知17230 Token、Embedding已知344 Token均只是已知小计。完整聊天Token、完整Embedding Token与实际金额仍为null/未知。本地没有供应商实际usage补充或可核对的官方账单证据，未按固定单价推算费用，未知值没有填0。

现有护栏位于验收CLI边界，并非所有生产Provider的全局网络封锁。`rag22_evaluate.evaluate("live", ...)`会先调用该固定历史路径护栏；即使未来通过，入口还会明确拒绝未实现/未授权的live预算账本。因此没有调用其他旧CLI、直接Embedding方法或HTTP接口绕过护栏，没有换输出路径、重置统计、删历史、改检查或增加预算。本轮只读检查在任何真实请求前停止。

## 配置与预算检查

仅检查本地字段，不打印Key或私有地址：Provider=`openai_compatible`，模型=`text-embedding-v4`，配置维度1536，Key字段非空，追踪false。**这不证明凭据有效、服务可达或本轮实际返回1536维向量。** 本轮未建立外部连接验证它们。

生产默认和本地检索设置保持`legacy / vector / 0.70 / none`、top-k=4；FlashRank关闭，Agent模型/工具/修复预算及SQL安全规则未变。

用户指定建议上限80次Embedding/20000输入Token仅记作后续设计上限，不代表本轮获准放行。历史总量仍未知，当前护栏允许的新真实请求为0；未计算/批准一套34题、语料索引、开发与A/B的可执行请求预留，不猜Tokenizer输入量或将每题当成固定一个请求。没有创建新live账本来替代旧不完整记录。

| 本阶段外部调用 | 实际数量 |
|---|---:|
| Embedding | 0 |
| 聊天模型/裁判 | 0 |
| LangSmith写入/读回 | 0 / 0 |

本阶段新增外部Token为0（没有发出请求），没有新增模型API用量；历史完整总量与费用仍未知。本轮无服务请求ID、实际usage或服务延迟，不能用本地检查耗时代替Embedding延迟。

## 题集、冻结与质量结果状态

准入已阻塞，按任务要求停止真实验收，未建立并宣称人工复核的新34题留出集，未冻结题目/标签/知识语料，未调参、未运行留出。阶段22的26条人工复核开发题仍为历史Fake对照，不挪作本轮新真实留出；阶段18旧留出及阶段11–23原题/成绩完全保留。

回执中的public_config_sha256和code_sha256只绑定**本次准入检查**，不是已冻结留出实验的配置/语料哈希。正式配置冻结、人审和唯一一次真实留出都未完成。

| 严格要求 | A：vector/0.70 | B：同候选+登记目录/适用性 | 结论 |
|---|---|---|---|
| 指标10题Recall@4≥9/10 | 未运行 | 未运行 | 未验证 |
| 非指标10题Recall@4≥9/10 | 未运行 | 未运行 | 未验证 |
| 无知识10题正确拒答≥9/10 | 未运行 | 未运行 | 未验证 |
| Precision@4、MRR、nDCG@4 | null | null | 不填0或100% |
| 无关召回数/错误来源采用 | null | null | 无逐题真实记录 |
| 4条注入/跨用户/跨数据集探测 | 未运行 | 未运行 | 只有既有/本轮离线回归证据 |
| 引用持久化、正文/owner/dataset/版本一致性 | 未运行 | 未运行 | 不宣称本轮100% |
| 维度/索引/授权错误及泄漏 | 未运行 | 未运行 | 没有真实服务安全结论 |
| 实际请求usage完整性 | 无请求 | 无请求 | 不据此判定真实验收通过 |

源码审查的已知风险仍在：`assess_evidence`只把唯一、适用的已登记指标作为严格模式证据，明确不提供通用非指标自由文本相关性分类。登记目录补充是确定性渠道，不能算向量语义召回；新34题非指标兼容目标尚无通过证据。纯检索不运行Agent/SQL，未来计划选择和执行后确认也必须标为未运行，不能从检索引用推断。

## 离线回归、隔离与复用证据

本轮生产和测试源码未改，在同一受验SHA下重跑相关检索、指标、预算、权限、SQL与trace回归；没有运行新的Fake质量比较，也没有新真实评分。

| 检查 | 实际结果/是否本轮重跑 |
|---|---|
| PostgreSQL/pgvector相关13测试文件 | 本轮215 passed/1真实Embedding opt-in skipped，45.83s |
| 相同SQLite测试集合 | 本轮201 passed/15真实服务或PG专用条件skipped，45.69s |
| Ruff check / format | 本轮通过，94个Python文件格式通过 |
| pip check | 本轮No broken requirements found |
| PostgreSQL全量、前端lint/build、Playwright、Compose | 本轮刚推送准确SHA的上述远程CI证据，不伪称新本机重跑 |
| SQLite全量 | 复用同代码阶段23末次471 passed/29 skipped、143.88s，本轮仅重跑上面的相关集合 |
| TypeScript | 复用同源码阶段23本机typecheck exit0；CI工作流不单独运行typecheck |
| 迁移重复升级/回滚/再次升级 | 复用阶段23同迁移/代码临时库检查，head0006；本轮远程CI另实际upgrade/check成功，无新迁移 |

13文件：test_metric_diagnostics、test_metric_resolution、test_retrieval、test_rag22、test_rag18、test_knowledge_vector、test_knowledge、test_embeddings、test_analysis_agent、test_sql_guard、test_tracing、test_rag_metrics、test_evidence_snapshot。预算不确定/未结束记录、新路径不能绕过、权限CTE、真实本机pgvector操作、受控注入零执行和本地脱敏合同均包含在离线回归中；这些不是新的真实语义质量证据。未删除旧测试、放宽断言或把失败改skip。

本轮本地PostgreSQL测试只使用现有独立`datapilot_test`的事务隔离夹具及测试自己创建的临时数据库，SQLite使用独立pytest临时目录；没有接触原业务库或真实用户数据。真实测试未启动，未创建阶段24容器、临时卷、测试知识或真实服务数据，因此没有本轮真实库前后行数/引用结果或需清理的新Compose资源。既有数据和历史日志保留；当前CI的隔离Compose由工作流正常down，不声称删除了远端runner之外的原资源。

本轮命令包括（包装器及完整日志在忽略目录，输出路径独立）：

```powershell
git fetch origin
git rev-list --left-right --count origin/codex/agent-rag-upgrade...HEAD
git push origin HEAD:refs/heads/codex/agent-rag-upgrade
# 网络重置后：临时http.curloptResolve、核对祖先再正常push；无全局修改
gh api repos/8xbAdx8/DataPilot/git/ref/heads/codex/agent-rag-upgrade --jq .object.sha
gh run watch 39756176681 --repo 8xbAdx8/DataPilot --interval 20 --exit-status
gh run view 39756176681 --repo 8xbAdx8/DataPilot --json headSha,status,conclusion,url,jobs
gh run view 39756176681 --repo 8xbAdx8/DataPilot --log
# PYTHONPATH=backend，所有RUN_LIVE_*关闭；原函数只读取固定历史路径
.venv/Scripts/python data/runtime/phase24-preflight.py
.venv/Scripts/python data/runtime/phase24-tests.py postgres <上述13个测试文件>
.venv/Scripts/python data/runtime/phase24-tests.py sqlite <相同13个测试文件>
.venv/Scripts/ruff check backend
.venv/Scripts/ruff format --check backend
.venv/Scripts/python -m pip check
.venv/Scripts/python infra/audit-git-history.py --details-output data/runtime/phase24-history-audit.json
git diff --check
```

## 密钥与历史保护审计

推送前34提交/556 blobs的原历史审计：当前三类Key精确匹配0、禁入路径0；候选secret_assignment51、credential_url40、local_path12。新增候选分别为沿用的公开演示连接串和测试运行时随机生成的密码表达式，已分类，不是真实密码字面量。没有推送.env、数据库、运行日志、截图或个人配置。

阶段24仅提交3份文档/准入产物；原86份历史题集、复核、冻结、JSON及阶段报告逐字节保护，SQL/配置/Provider/预算护栏及锁文件不变。提交前再次审计当前Key、非公开数据库密码和私人Embedding主机，全部0匹配；历史扫描与人工diff复核不构成绝对无秘密保证。准入JSON仅保留公开模型配置、Key是否存在及哈希/状态，没有Key、URL、Cookie、原始会话令牌或新业务数据。

## 尚需用户决定与后续条件

1. 优先提供可核对的供应商usage或官方账单记录，核清阶段21未知聊天和Embedding尝试；当前仓库证据不足，不能从错误耗时推断实际消费或伪造完整性。旧原始记录应保持不变，核对证据如后续新增需单独关联。
2. 核清之后仍需单独审核新的Embedding-only账本与保守预留方案，在≤80请求/≤20000输入Token及更严格既有约束内逐请求检查；缺usage立即停止、无自动重试。现有RAG22 live拒绝入口不等于已经具备这套运行器。
3. 此后才进入新的合成语料、按意图隔离的开发/留出、人审、最终配置冻结、唯一一次真实留出及逐题引用/权限核验。达不到预定门槛须如实保留失败，不能复跑追分。

用户本次要求没有授权在未知用量仍存在时放行。当前保持BLOCKED并停止真实验收；不自行设计豁免、重置或转用其他入口。本阶段文档提交仅本地，不自动推送。远程CI全绿只证明被推送代码通过相应工程检查，**真实RAG检索质量及生产发布条件仍未通过验收**。

## 2026-10-22 受控独立实验续验

以上是原准入检查的完整历史记录，原字节保留。用户随后明确下达“阶段24：真实RAG质量攻坚与PASS验收”，允许历史usage无法核清时开展独立、受控的Embedding-only实验；新授权不是补写历史usage，也没有改变旧通用入口的拒绝行为。以下为该新授权下的实际证据。

**最终判定：候选C在本次冻结合成检索题集上PASS，引用、权限、预算及本机工程回归通过。** 这不包含真实Agent采用、SQL计算正确性或生产发布批准。C仍为显式实验模块，生产默认保持`legacy / vector / 0.70 / none`；A/B失败全部保留，尚不能宣称生产默认RAG达到相同质量。

### 版本、授权与历史核算

起始分支`codex/agent-rag-upgrade`，HEAD `3e3f62fe4e73d0d8e60358d8d2b71f1215bbe52b`，开始工作区干净；其父阶段23代码为`0ff41fcc6899ae3cbfc250f7980d2abf59672655`。新增代码在工作区实现并按50个后端源码的规范化SHA冻结后评分，不把起始HEAD冒充评测代码版本。最终代码内容由`holdout-v1.freeze-v3.json`逐文件绑定，本续验与该文件一起提交，完整最终提交SHA可由`git log -1 --format=%H`核对，提交后在交付消息列出。此次不推送；[39756176681](https://github.com/8xbAdx8/DataPilot/actions/runs/39756176681)仅对应上述阶段23远端SHA，不是本次新增代码的CI。

先检查了原阶段21原始记录、护栏与本地可用凭据。没有可用阿里云BSS AccessKey/CLI账单凭据，聊天Key可用的[DeepSeek余额接口](https://api-docs.deepseek.com/api/get-user-balance/)只返回当前余额，无法核对历史请求usage；[阿里云账单API](https://www.alibabacloud.com/help/en/user-center/developer-reference/api-overview-1)有独立权限要求。因此没有发送无资格的账单请求，没有打印凭据，也没有把余额或单价推算成真实消费。只读核对结论见[历史核对回执](rag24/historical-usage-review-v1.json)。

旧聊天/Embedding总Token及费用仍为unknown，既有可核对的小计17230/344保留为小计，不代表历史总量。`metric21_acceptance.py`、`rag22_evaluate.py`和旧原始记录均未变，旧入口仍因原未知usage拒绝。新授权[authorization-v1.json](../backend/app/evals/rag24/authorization-v1.json)绑定用户指令SHA和旧报告SHA；没有重放、重置或覆盖阶段21尝试。

### 预算、实际请求与缓存

新账本`rag24_budget.py`以固定路径持久化：`data/runtime/rag24-live/ledger.sqlite`。首次创建使用独占写入，授权必须完全匹配；每请求前`BEGIN IMMEDIATE`、`synchronous=FULL`落盘预留，输入SHA唯一。未结束、失败、usage缺失、缓存缺失或写入失败均停止，不自动重试；改评测输出路径不能获得新账本。一次性正式留出claim也位于固定运行根目录，并有跨输出路径拒绝测试。

使用官方Qwen分词器固定[下载版本](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/resolve/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/tokenizer.json)，本地文件SHA `def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a`。托管模型未公开精确分词器版本，不能声称本地估计就是账单；预留取本地估计两倍与UTF-8字节包络的较大值，再每文本加32。开发开始前预留7191并为未来留出保留10000；最终实际留出保守预留8018，总15209，处于20000上限内。[预算计划](rag24/development-budget-plan-v1.json)在首次发送前建立。批量不超过[官方v4接口](https://www.alibabacloud.com/help/en/model-studio/text-embedding-synchronous-api)的10文本限制。

| 项目 | 开发 | 留出 | 全轮 |
|---|---:|---:|---:|
| 唯一合成文本/真实向量 | 60 | 62 | 122 |
| 实际Embedding HTTP | 6 | 7 | **13/80** |
| 本地tokenizer估计 | 1413 | 1399 | 2812 |
| 供应商实际输入usage | 1473 | 1461 | **2934/20000** |
| 保守请求预留合计 | 7191 | 8018 | 15209 |
| usage完整、状态complete、请求ID可查 | 6/6 | 7/7 | **13/13** |

实际Provider为`openai_compatible`百炼，模型`text-embedding-v4`，维度1536；向量完整校验后存入真实pgvector列。完整[请求账本](rag24/complete-ledger-v1.json)包含运行ID、案例/语料关联、时间、耗时、Provider/模型、供应商request ID、实际prompt/total usage和完整性标志。输入只保留合成文本哈希，未记录请求头、Key、私人主机或会话令牌。调用耗时p50 **503.678ms**、p95 **748.527ms**。新轮未知Token为0；**费用仍未知**，没有可信账单就不填金额。聊天、LLM裁判、LangSmith写入/读回均为**0**。

所有A/B/C和开发诊断复用同一真实向量，检索阶段新HTTP为0。`CachedRealProvider`只是只读缓存适配，不是Fake向量；缓存缺失直接失败，无网络回退。原始两份账本导出保持不变，完整关联导出另存新文件。

额外保存[真实向量快照](rag24/real-vector-cache-v1.json)：NPZ仅122×1536数值与输入SHA，不含正文或pickle，1043966字节，SHA `459908a72d0765270c8bc99c152380365d7313d148bf58bf1c45f48662e2d2c8`。离线复核器重新计算34题基线的float32 pgvector余弦候选（分数容差2e-6）、三种策略全部标签指标、账本输入去重、总usage与冻结代码；实际结果verified、0新服务请求。离线复核是对已有真实证据的算术审计，不是新增一次留出评测。

### 开发根因、候选与完整轮次

开发集24条知识/36题，原RAG22的26题只用于开发，重新生成真实Embedding；补充6条结构化非指标知识、6正例/4无答案题，均已收到人工确认。组成9指标、6非指标、20负例、1权限题，语料SHA `1d1ea0eaec2c5bcae9e8c48aab3cf6a5e41f66680f4e4377c34dc6a6b8e7281b`。

真实A开发指标仅3/9、非指标6/6、正确拒答15/20；B指标9/9、非指标0/6、拒答20/20。[17个逐题失败](rag24/development-failure-analysis-v1.json)显示两个不同根因：正确登记指标的相似度低于.70，目录能补足；原registered证据策略只接受指标，导致非指标知识全拒绝。A另外把同主题却不包含所问属性的来源返回给负例。阈值0的最近邻诊断能找齐指标，却引入27个指标无关源、全部20个负例误召回及13次恶意候选出现；因此没有用降低阈值换取正式成绩。

C最小修复：沿用B的原指标解析/公式/必需字段/歧义检查；未识别为指标时，仅从**真实授权向量候选**选择符合闭合JSON事实契约的非指标知识，必须同时匹配主体和所问属性/已登记别名，必需字段须存在，多事实匹配时拒答。额外字段和错误结构拒绝。没有把目录补充标成向量命中，没有新增聊天判断、混合检索、重排或Agent预算。`source_origins`分别保存`vector_candidate`与`catalog_supplement`；计划来源/实际采用为null/NOT_RUN。

这依赖人工登记的结构化事实，不是任意散文知识的通用真实性分类器；不能推断所有恶意内容都可识别。C未接入生产API默认请求，原registered行为不变。安全题证明本题集内过滤效果，不能等同真实Agent不会执行任何注入。

| 开发轮次 | 实际情况 |
|---|---|
| development-01 | 索引缓存SQLite连接跨FastAPI线程失败，未评分；6次已完成Embedding完整保留，无未知请求 |
| development-02 | A/B真实基线，以上失败原样保留 |
| development-03 | 加C闭合非指标事实契约，C为9/9、6/6、20/20 |
| development-diagnostic-01 | 阈值0，仅最近邻失败诊断，不是验收策略 |
| development-04 | 增加分组、能力及延迟统计，同语料/配置得分不变；此时记录候选选择 |
| development-05 | 改为原API真正commit及第二连接确认持久化，同分数；强化证据而非调参 |

所有轮次以新目录写入，未覆盖失败。跨线程连接修复及预算、适用性、冻结准入、一次claim等均增加确定性回归。首次修复过程的测试失败已修复，最后全量绿；未删旧测试、降断言或新增跳过来隐藏失败。

### 独立人工标签与冻结

候选开发达到原用户预设≥90%门槛后，才构建新维修工坊28知识/34题；开发为图书馆领域，非简单同义改写复用。每题保存来源、拒答/权限、意图和依据。已收到“已复核RAG24独立留出”，[复核文档](RAG24_HOLDOUT_REVIEW.md)和绑定回执保留。知识正文全部合成，不上传原用户数据。

- 新留出语料SHA：`c084d21d5703a1a09333deaeb359002aa774cc0e9f9922dabaa48b5213cff79e`。
- 最终冻结SHA：`2f6a5d08e07ce6d07e8a8c14248ca47a132324c93e43c0c71213fb5f8eaa0098`。
- A/B/C配置SHA：`62cd453a9850d361fabad1074d8fbd891d28092b8d1917935c97fac694e48bd1`。
- 预设验收标准SHA：`8cd9889024410368ce26b5152d070a8da900161ee415db7e8647c6ba92bbbd90`。

冻结v1原样保留，未评分：Windows既有CRLF源码与Git规范LF造成原始字节校验不便复核。v2采用canonical-LF，但执行器残留重复raw-byte比较，导致`holdout-01/admission-failure.json`准入失败：**0题评分、无临时库、无正式claim**。修复该冗余比较，增加CRLF准入回归后产生v3；标签、语料、策略、阈值和标准均未变，尚未有任何留出分数。v3为唯一实际评分版本。正式`holdout-02`只运行一次34题×A/B/C；其后未调参、改代码或再次评分。

### 真实冻结留出成绩

[逐题原始记录](rag24/holdout-02/records.json)、[分组汇总](rag24/holdout-02/summary.json)、[失败清单](rag24/holdout-02/failure-analysis.json)、[冻结评分判定](rag24/holdout-02/verdict.json)均独立保留。

| 指标 | A：legacy向量 | B：registered指标 | C：指标+闭合非指标事实 |
|---|---:|---:|---:|
| 指标Recall@4（10题） | **6/10** | 10/10 | **10/10** |
| 指标Precision@4 / MRR / nDCG@4 | .15 / .60 / .60 | .25 / 1 / 1 | **.25 / 1 / 1** |
| 非指标Recall@4（10题） | 10/10 | **0/10** | **10/10** |
| 非指标Precision@4 / MRR / nDCG@4 | .25 / 1 / 1 | 0 / 0 / 0 | **.25 / 1 / 1** |
| 无答案正确拒答（10题） | **6/10** | 10/10 | **10/10** |
| 无答案无关源数 | **4** | 0 | **0** |
| 安全探测正确空结果（4题） | **3/4** | 4/4 | **4/4** |
| 恶意最终检索源（非Agent采用） | **1** | 0 | **0** |
| 正式指标名称 / 登记别名 | 4/5、2/5 | 5/5、5/5 | **5/5、5/5** |
| 引用完整一致 | 21/21 | 10/10 | **20/20** |
| 跨用户/跨数据集泄漏 | 0 | 0 | **0** |
| 检索p50 / p95（ms） | 50.475 / 62.586 | 47.434 / 61.148 | **51.291 / 63.982** |

Precision@4固定分母4，每题标签仅一个相关源，因此返回唯一正确源时Precision@4=.25，**不能写成100% Precision@4**。正例内无关源均0；A另有1条安全探测无关源，不能只报告负例4条而隐藏它。A总9题失败（4指标漏召回、4负例误召回、1注入源返回）；B总10题非指标失败；C无标签失败。向量候选和目录补充可从每题`candidate_stages/evidence/source_origins`区分。

C歧义识别且空结果1/1、不适用且空结果1/1、证据不足且空结果2/2。未登记指标/无知识在检索层返回no_match；本轮没有自然语言澄清模型调用，**真实澄清准确率NOT_RUN**。指标公式定义只验证检索适用性，**生成SQL公式及必需过滤违约率NOT_RUN**。

三种策略的Agent规划、来源实际采用、SQL生成/执行、端到端结果正确率均为**NOT_RUN**；不能把A的1次恶意来源返回写成Agent已执行，也不能把C过滤结果写成真实Agent注入全防御。此纯检索执行器没有分析execute调用路径，不安全SQL进入execute为0次，但这不是本轮真实SQL安全攻击执行证据，原SQL安全回归另行通过。

### 真实数据库、引用与资源隔离

真实调用采用独立`datapilot-rag24`Compose、回环端口15632/18200/13200和`.env.example`公开测试配置；不读取原业务库URL作真实验收数据库。每次最终检索轮另创建随机`rag24_…`临时数据库，原Alembic升到0006，原数据集上传/知识创建/索引/检索入口完成真实操作。至少一个search请求经原HTTP API，其余策略复用同一原检索函数；缓存适配只改变Embedding获取来源，不伪造检索结果。

正式留出在空库写入2用户、2会话、3数据集、28文档/28向量块，并生成59条审计；`analyses=0`与NOT_RUN一致。API确实commit，独立第二连接看到ready文档和向量，验证持久化，不只是在未提交事务内读回。每个最终引用检查document/chunk关联、正文excerpt、标题、owner、dataset或personal授权、version=1、ready、模型空间及1536维。C20/20全部真实存在且一致，维度/索引/权限未拦截错误0。

当前没有独立personal创建REST接口，测试通过现有数据集知识接口创建后，**仅对本次合成夹具**转换为personal作用域，检索仍走原权限过滤；这属于权限夹具验证，不宣称完整personal上传产品流程已验证。版本一致性在本题集检查version1，没有开展新的真实更新/删除/重建专项；生命周期仍由现有回归证明。

每轮临时数据库finally删除，基库所有表计数前后不变；正式结果保存完整计数。最后核实无残留`rag24_`库，50份后端容器源码与本地字节一致，三个模型Key均未进入容器。仅对核实Compose项目标签的本阶段容器、网络及`datapilot-rag24_postgres_data`、`datapilot-rag24_analytics_data`两个卷执行down --volumes，清理后均0残留；没有prune、清理原PG集群或删除旧日志/账本。[清理回执](rag24/resource-cleanup-v1.json)保留。专用本地账本、tokenizer与原始命令日志继续在被忽略目录保存。

### 本机工程回归与依赖

[最终回归回执](rag24/engineering-regression-v1.json)绑定每个实际日志SHA，不把旧CI当成本轮本机结果。

| 检查 | 最终实际结果 |
|---|---|
| PostgreSQL全量（独立datapilot_test与测试临时库） | **530 passed / 6 skipped**，194.08s；跳过为既有真实服务opt-in |
| SQLite全量（独立pytest目录） | **506 passed / 30 skipped**，142.62s；原PG/真实服务条件测试 |
| 预算/检索/指标/权限/Agent/SQL安全/追踪 | 包含于上述全量；没有删旧测试或新增跳过条件 |
| Ruff check / format --check | 全通过，103文件格式已符合 |
| pip check | No broken requirements found |
| 前端lint / TypeScript / 生产构建 | 本轮全部exit0 |
| Playwright普通E2E | **15 passed / 3 skipped**，1.2m；既有3个真实专项默认关闭，Mock/Fake模式 |
| Compose build / up / 健康 | 本轮源码重建；PG/backend/frontend healthy，迁移成功退出 |
| Compose冒烟 | 上传、Mock生成真实DuckDB结果、Fake知识CRUD/引用/SUM60、密码会话撤销均PASS |
| 迁移 | 原0006：upgrade两次/check/downgrade base/reupgrade/check全部通过；无新迁移 |
| 已有真实证据离线复核 | 122向量、34基线余弦候选、三策略评分、13请求/2934usage、冻结SHA全部verified |

新文件主要为`rag24_budget.py`、`rag24_evaluate.py`、`rag24_policy.py`、`rag24_judge.py`，4份对应测试、合成fixture/冻结文件、离线复核脚本与报告。生产SQLGlot/Schema/执行限制、权限、原Provider、旧预算护栏、原检索/指标解析、前端与迁移均未改。唯一依赖声明变化是在可选eval extra显式列出`tokenizers==0.23.3`，与**既有**eval锁一致，两份锁文件没有变化；不会引入新生产模型依赖。

### 实际命令与复核方式

完整输出保存在被忽略的`data/runtime/phase24-*.log`，新回执含日志SHA；执行器产物采用独占写入，已有轮次不能覆盖。以下是本轮实际运行入口及其等价检查，不要删除账本或全局claim后重放真实留出：

```powershell
$env:PYTHONPATH='backend'
# 已执行的受控真实动作；RUN_LIVE_RAG24=1只对验收进程显式启用
.venv/Scripts/python -m app.rag24_evaluate embed --corpus development-v1
.venv/Scripts/python -m app.rag24_evaluate evaluate --corpus development-v1 --round development-02 --config backend/app/evals/rag24/baseline-config-v1.json
.venv/Scripts/python -m app.rag24_evaluate evaluate --corpus development-v1 --round development-05 --config backend/app/evals/rag24/candidate-config-v1.json
.venv/Scripts/python -m app.rag24_evaluate embed --corpus holdout-v1
.venv/Scripts/python -m app.rag24_evaluate evaluate --corpus holdout-v1 --round holdout-02 --config backend/app/evals/rag24/candidate-config-v1.json
# 固定日志包装器调用pytest -q，postgres只注入独立测试库
.venv/Scripts/python data/runtime/phase24-upgrade-final-tests.py postgres
.venv/Scripts/python data/runtime/phase24-upgrade-final-tests.py sqlite
.venv/Scripts/ruff check backend infra/verify-rag24-results.py
.venv/Scripts/ruff format --check backend infra/verify-rag24-results.py
.venv/Scripts/python -m pip check
# frontend目录：npm run lint; npm run typecheck; npm run build
# 隔离E2E包装器设置E2E_BASE_URL和独立测试PG后调用npm run test:e2e
.venv/Scripts/python data/runtime/phase24-final-playwright.py
# datapilot-rag24专用环境、.env.example、独立loopback端口
docker compose build
docker compose up -d --wait
# 原infra Compose冒烟/迁移脚本，通过本轮独立环境运行
# 最后核实归属后docker compose down --volumes
.venv/Scripts/python infra/verify-rag24-results.py
.venv/Scripts/python infra/audit-git-history.py --details-output data/runtime/phase24-upgrade-history-precommit-v1.json
git diff --check
```

可公开重复的离线步骤见[评测协议](../backend/app/evals/rag24/README.md)和[证据索引](rag24/README.md)。真实测试默认关闭，普通CI不需要云端凭据。代码内容按canonical-LF哈希能与Git提交blob复核，不依赖本机行尾。

### 提交前审计、局限与交付边界

提交前已有35提交/559 blobs的可达历史扫描：当前三类Key精确匹配0，禁入路径0；规则候选secret_assignment51、credential_url40、local_path12，与旧历史一致。新增代码/文档/NPZ及待提交索引另做Key、非公开数据库密码和私人Embedding主机精确扫描；公开测试连接串、随机生成的测试密码表达式和空Key赋值人工分类，匹配位置记录不输出值。没有提交.env、运行SQLite、数据库文件、完整终端日志或个人服务地址；NPZ为明确合成评测向量快照。模式扫描与人工复核不构成绝对无秘密保证。

原保护基线共88项：87个文件逐字节未变，另一项是本报告原BLOCKED正文完整字节前缀，只追加续验。阶段11–23旧题、冻结、真实分数及阶段16原0/2等均保持原样。没有重写历史、覆盖旧成绩、推送、合并、打Tag或部署公网。

最终PASS仅针对已人工审核、单次冻结的34题及本次权限/引用/预算和本机回归。仍待后续另行授权的工作包括：任意散文知识与跨领域泛化、将实验C安全接入生产请求、真实Agent采用及SQL端到端、真实更新删除重建专项、更大独立题集。旧阶段21总usage与所有真实费用仍未知。本轮新用量完整，但不能据此核销历史unknown，也不能借检索PASS宣布生产发布条件全部满足。

最终[交付判定回执](rag24/delivery-v1.json)分别绑定检索原始判定、本机回归、资源清理、审计及真实向量的SHA；原始`verdict.json`中当时工程pending文字不覆盖，由新回执补充最终完成证据。首轮待提交63文件审计为0私密值匹配、0禁入路径、0新增规则候选，59个冻结代码/资产在Git索引中与规范化哈希完全一致；最终本地提交后再次审计可达历史和工作区。
