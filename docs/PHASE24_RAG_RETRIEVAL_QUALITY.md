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
