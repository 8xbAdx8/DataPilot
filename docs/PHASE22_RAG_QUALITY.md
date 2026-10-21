# 阶段22：RAG检索质量与证据筛选

起点 `4db494a8141b4a9866b32540e85dd384a84eb12d`，分支 `codex/agent-rag-upgrade`，工作区干净、领先远端1提交。仅本地交付，不推送、不开始阶段23。以下调查在修改生产代码之前完成。

## 根因调查

1. `knowledge.eligible_chunks` 在 MATERIALIZED CTE 内先过滤 owner、dataset/personal、ready、document/chunk embedding space；`retrieval.select_candidates` 在数据库余弦距离条件中应用阈值，然后排序并 LIMIT。vector策略没有后置业务过滤，hybrid两路也共用阈值，不会证明业务适用性；RRF与FlashRank改变排名，不建立口径。
2. 原 `retrieve_knowledge` 的 `status=retrieved/no_match` 只取决于候选列表是否为空；未检查公式、必需字段或问题中的指标名称。`candidate_stages` 保存vector/selected等候选数量和ID。故高相似的错误指标、缺字段文档和指令都可能出现；不是权限CTE失效。
3. 阶段18原始报告列明开发support-n01/n03，留出delivery-n02/n03/i01误召回；尤其i01返回注入知识。原留出正例8/8、应拒答3/8误召回保持。0.65是该阶段实验推荐，报告也明确生产默认未变；当前Settings与.env均为vector/0.70/none。用户任务中“生产策略0.65”与现有代码不一致，本阶段不改历史来消除差异。
4. `PlannerContext.capture` 将工具返回citations添加到knowledge，`analyses.make_plan` 按模型选择ID缩减，`AnalysisRun.generation_context` 再传给SQL模型。旧路径的选择表示模型选了候选，不能说明相关性或定义正确。`test_retrieved_prompt_injection_cannot_bypass_sql_guard` 明确把恶意正文提供给受控模型再验证DROP零执行：这是SQL安全证据，不是检索质量或模型抗注入证明。
5. 阶段21 `MetricRegistry` 使用授权目录、明确名称/别名、严格契约和Schema验证，不依赖向量相似度。多份命中ambiguous、缺字段inapplicable、无明确公式insufficient_evidence。`analyses.configure_evidence` 在生成前要求采用登记来源，原SQLGlot后还有独立evidence_guard。目录可在向量no_match时补充真实来源，不能把这个补充回填为向量命中。原阶段21测试已明确保留这种初始no_match与后续used并存的情况。
6. 空目录和旧非结构化知识仍有兼容路径，因此不能声称阶段21已解决广义误召回。新筛选将单独标识向量候选、证据状态、目录补充、所选与实际传给SQL模型的来源；不把未验证来源送给严格模式的规划器。
7. 现有Ragas适配器只绑定旧development两条SQL响应，并额外请求聊天裁判；不适用新纯检索/契约评测，不复用旧样本、不新增裁判调用。已有ID标签rank_scores可复用，人工标签仍为主要标准。FlashRank保持关闭，不增加依赖。

## 实施与验收计划

- 新建独立图书馆合成数据、语料、查询、逐条理由和评分契约，冻结SHA256；复核状态单独记录，不伪造人工确认。
- 固定同一候选策略vector/0.70/none、同一向量：比较原候选基线与登记证据筛选，不改变多个变量。目录补充单列，不计作向量召回。
- 新增显式 `RAG_EVIDENCE_POLICY=registered`，默认legacy兼容；真实质量未验证之前不静默切换部署行为。严格模式会在Agent前过滤，并对歧义/不适用/证据不足澄清，保留阶段21SQL证据门禁。
- 先做确定性、真实PostgreSQL/pgvector（Fake向量单独标识）、SQLite及安全回归，之后工程检查、独立Compose与迁移。
- 现有 `metric21_acceptance.check_prior_usage` 因阶段21未知usage拒绝新真实调用。本轮不修改或绕过停止规则，不重跑旧失败；真实Embedding与语义质量验收标记阻塞。离线报告不冒充真实结果。LangSmith仍关闭。

后续结果将补充到本文件；阶段11–21原始题集、冻结文件、报告、JSON不改、不重跑历史留出。

## 交付行为与边界

新增 `retrieval_evidence.assess_evidence/filter_evidence`，复用阶段21目录解析、名称/别名、Schema与SQL契约，不引入新框架或模型裁判。严格模式在候选之后筛选，并允许独立目录补充；不会通过降低阈值来猜口径。候选已先过原数据库授权，筛选不能赋予新权限。工具搜索/指标查询再按**原始用户问题**筛选，模型工具参数不能换成另一个指标。

末次协议复核发现工具查询另一个指标时不应静默替换成原指标。最终实现要求工具实际返回的来源与原问题证据有交集，否则显式no_match/insufficient_evidence；目录补充由初始检索提供。测试断言换指标工具返回空，同时不污染原计划预载的正确来源。

逻辑状态由现有 `rag` 加一个 `evidence` 子对象表达：

- `candidate_status/candidate_stages` 保留初始检索事实、排名和过滤前数量；`status/citations` 在严格模式表示筛选后可提供的证据。legacy字段含义保持兼容。
- `evidence.status` 区分resolved、not_found、ambiguous、inapplicable、insufficient_evidence、not_configured；这些是有限登记契约判定，不是普适语义分类器。
- `source_origins` 区分vector_candidate与registered_catalog；目录正文取持久化来源，缺向量命中时similarity=null。不能把目录补充计成向量Recall。
- `selected_source_ids` 是计划选择；`context_source_ids` 是实际传给SQL模型的来源；`used_source_ids` 仅在SQL通过原安全门槛、指标契约并完成DuckDB执行后填入。它证明上下文传递与契约执行，不证明模型内部注意力或业务真值。拒绝、澄清、执行异常分别记录final_decision，不能冒充答案。

严格模式不向规划器提供未验证的自由文本规则。歧义、不适用、缺登记证据时先澄清；原生字段/行数查询仍可不依赖业务知识运行。合法字段映射等非指标知识仍有兼容缺口：本轮筛选只接受登记metric_definition，不宣称已覆盖field_alias/data_dictionary的完整语义；显式启用严格模式可能使旧别名演示无法得到相同答案，需要后续独立接入与验收。因此**没有切换生产默认**。legacy仍保留旧候选风险，不能据严格模式离线成绩宣称默认路径已消除误召回。

阶段21的 `metric_resolution.py` 和SQL `evidence_guard`未修改；SQLGlot/Schema、权限、工具白名单、3次模型/4次工具、修复次数与执行限制未放宽。LangSmith仍opt-in；配置哈希加入policy以区分策略，白名单不增加正文/用户/原始ID导出。

## 新语料、标签与冻结

用户已回复“已复核 RAG22 标签”。[复核回执](../backend/app/evals/rag22/review-v1.json)绑定[冻结清单](../backend/app/evals/rag22/freeze-v1.json)，[逐条复核文本](RAG22_LABEL_REVIEW.md)及[语料说明](../backend/app/evals/rag22/README.md)可检查。

| 文件 | SHA-256 |
|---|---|
| corpus-v1.json | `3edc6b4e9a076e7469be9e2803cd93dc59eb0582f3c377156f8bd5b9f482e7c7` |
| contract-v1.json | `150ec05c219c03ab3a32a7b8dd03900adf3331a89fdbfa27f8ea560b6667a222` |
| library.csv | `d28ab58e875bfd1149a1f71384fada7768ae39c2f29ad8e9fff727a1ae6e8191` |

18条图书馆合成知识、4行合成数据、26条开发查询：9个正例（含同一指标3种登记表达，不声称26个独立意图）、16个授权负例、1个越权。负例为hard-negative3、缺字段1、缺定义1、无关1、无知识1、冲突2、注入3、数据集范围1、用户范围1、personal不适用1、别名边界1。知识包括returned/lost罚金、净额、AVG分钟、COUNT非空标记、字段缺失、冲突别名、自由文本、额外指令字段、危险表达式及不同scope。

本阶段**没有留出集**。fixture中的authoring pending状态被冻结保留，有效人工确认以独立SHA回执为准。没有修改旧标签或生成新的历史分数。

## 相同候选的两策略对照（仅离线）

[最终原始结果v4](rag22-development-offline-v4.json)记录起点HEAD、11份实现SHA、冻结标签、逐题候选/证据/来源及持久化真实性检查。两个策略复用同一批Fake词法向量、同一PG库、同一授权范围；第二策略只增加证据筛选与目录补充。固定vector/0.70/none/k4，无阈值搜索、混合检索或FlashRank改动。

| 指标 / 分母 | 原向量候选基线 | 候选+登记证据 |
|---|---:|---:|
| Recall@4，9正例宏平均 | 0 | 1 |
| Precision@4，9正例、每题固定4位置 | 0 | 0.25 |
| MRR，9正例 | 0 | 1 |
| nDCG@4，9正例二元标签 | 0 | 1 |
| No-match recall，16授权负例 | 16/16 | 16/16 |
| 误召回率，16授权负例 | 0/16 | 0/16 |
| 业务适用性状态准确率，25授权题 | 9/25 | 25/25 |
| 引用真实性，实际返回来源数 | N/A，0条 | 9/9 |
| 越权拒绝，独立1题 | 1/1 | 1/1 |
| 跨用户/数据集泄漏 | 0 | 0 |
| 恶意候选/可用证据出现数 | 0 | 0 |
| P50 / P95，25授权题，毫秒 | 5.199 / 8.558 | 32.617 / 83.006 |

排名只在9正例计算，空候选得0；Precision分母固定4，因此只返回一份完全相关来源的得分是0.25。拒答分母16，不含越权；服务错误不算正确no_match。适用性按完整状态分类，不把ambiguous/inapplicable等改成not_found。引用检查document/chunk存在、从属关系、title、截断正文一致。P50为中位数、P95为nearest rank；第二列包含同一检索时间与额外目录/契约筛选时间。26条查询向量在计时前一次生成并缓存，缺缓存立即失败，不隐式调用或回退；计时排除向量生成与外部HTTP，仍包含本地向量取值/校验和数据库授权查询。

**解释限制：Fake在0.70下所有向量候选均为空，原候选正例p01–p09全部失败。第二策略9次成功全部来自目录补充，vector命中仍为0；这不是语义Recall从0提升到1的证据，也没有测出真实误召回率改善。** 新路径状态分类/目录兜底符合26题开发契约（越权单列），真实Embedding质量仍未验证。LLM采用率和SQL准确率在该检索评测器内为N/A，不能把9份证据等同9次模型采用。

v1、v2原始离线结果保留；v1后补异常脱敏、诊断计数、策略追踪哈希和完整实现指纹。复核发现v1/v2计时包含Fake向量生成，违反已冻结的延迟排除口径，因此这两版延迟不作为正式性能结果。v3通过预缓存修正计时；标签、阈值、候选和排名/状态结果未变，不按结果挑延迟。v4仅将末次工具响应范围修正纳入最终实现指纹，未改标签、排序策略或评分。四轮都是开发比较，不是冻结留出重跑；没有修改冻结契约来适应实现。

## 安全与真实执行证据

`test_rag22.py` 的可重复测试明确区分以下链路：

- 实际PG/pgvector受控向量将注入文档排第一（similarity=1），其余文档0.8；保持阈值0.70。断言原candidate_stages第一名确实是恶意文档；真实LangChain工具调度使用FakePlanningModel，SQL上下文中无恶意正文，实际DuckDB返回net=17。这是受控向量/模型安全证据，**不是**真实语义或真实聊天模型抗攻击验收。
- 相近但不同的SUM/过滤口径、缺reader_id、模糊文本、重复别名、额外instruction字段、非法多语句表达式分别分类；澄清前Agent/SQL生成零调用，execute节点不存在。
- 模型试图调用external_shell、提升预算，原白名单在调度前拒绝，tool_calls=0、配置仍4。模型已有合法引用但输出错误公式/过滤或DROP时，原SQLGlot和指标证据门禁拒绝、无repair绕过、执行器零调用。
- 源正文伪造不覆盖持久化证据；已删除定义不能通过新目录继续入选。原知识更新、去重、索引失败、超时、Embedding空间变化与权限生命周期旧测试保留。

旧严格SQL 0/2、阶段16唯一失败、阶段18正例8/8及误召回3/8、阶段19 4/5、阶段21开发4/4与旧格式补充失败均保留，不以本开发集替代。

## 外部服务与预算

本地配置只核对非敏感标识/Key存在性：openai_compatible、text-embedding-v4、1536维、Key存在；未输出私人地址。原 `check_prior_usage` 检出阶段21unknown_usage=true，故两个独占live预检回执[v1](rag22-live-blocked-v1.json)、[v2](rag22-live-blocked-v2.json)均blocked，Embedding/聊天请求0；没有网络尝试、自动重试、换路径重置预算或补报未知用量。CLI当前live只提供停止规则预检，完整新实时预算账本尚未实现；即使将来清理阻塞，也不会自动开始调用。

阶段22新外部请求为0、无新模型Token。阶段21已知聊天小计17,230及Embedding小计344仍非完整总量；完整Token/费用未知，不按零或固定单价补算。没有回跑阶段21失败，没有使用旧真实向量冒充新语料，没有新增Ragas裁判或LangSmith云端调用。

本轮PG17.10/vector0.8.7的真实持久化、余弦距离、materialized授权CTE、引用校验和回滚实际执行。v4实际读回18条向量，维度集合[1536]；四轮离线报告均rollback_counts_equal=true。其输入是Fake词法向量；真实Embedding请求、1536维真实语义向量与新语料质量验收**阻塞**，生产部署质量门槛未通过。

## 工程回归与命令

以下命令已实际执行，URL只在进程环境从Settings生成，不放命令行或公开日志。

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python -m pytest backend/tests/test_rag22.py -q --basetemp=data/runtime/pytest22-target-sqlite-b -p no:cacheprovider
.venv/Scripts/python data/runtime/phase22-tests.py postgres
.venv/Scripts/python data/runtime/phase22-tests.py sqlite
.venv/Scripts/python data/runtime/phase22-tests-final.py postgres
.venv/Scripts/python data/runtime/phase22-tests-final.py sqlite
.venv/Scripts/python data/runtime/phase22-tests-scope.py postgres
.venv/Scripts/python data/runtime/phase22-tests-scope.py sqlite
.venv/Scripts/python -m app.rag22_evaluate --mode offline --output docs/rag22-development-offline-v4.json
.venv/Scripts/python -m app.rag22_evaluate --mode live --output docs/rag22-live-blocked-v2.json
# live是预检阻塞，不能记作服务验收通过
.venv/Scripts/python -m ruff check backend
.venv/Scripts/python -m ruff format --check backend
.venv/Scripts/python -m pip check
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
.venv/Scripts/python infra/check-migrations.py --disposable --host 127.0.0.1
. data/runtime/phase22-env.ps1
docker compose --env-file .env.example build backend migrate
docker compose --env-file .env.example up -d --no-build --wait --wait-timeout 120
.venv/Scripts/python infra/compose-smoke.py
.venv/Scripts/python data/runtime/phase22-playwright-final.py
.venv/Scripts/python infra/audit-git-history.py --details-output data/runtime/phase22-history-audit-pre.json
```

第一轮新增夹具因批量知识写入触发生产限流（4 passed/16 setup errors），只在新测试夹具提高批量配额，生产限流未改；修复后新增SQLite19 passed/1 PG跳过、PG20 passed。第一轮全量PG472/6、SQLite450/28通过。随后补异常值脱敏、恶意首位候选、工具越权及追踪policy哈希测试；不删除旧测试或改失败标签。首次浏览器13 passed/3真实opt-in skipped，包装器在打印Unicode摘要时GBK编码失败；原始浏览器进程退出0及日志保留，末轮包装器改为ASCII JSON输出，不把脚本打印错误隐瞒为测试通过。

前端生产代码未变，因本阶段改变服务端证据筛选与诊断，不为本阶段增加UI；统一来源呈现仍属后续工作。现有前端lint/tsc/build通过，Docker复用与当前源码相同的既有前端镜像，本轮后端/migrate镜像实际重建。无新依赖或迁移；独立新库upgrade两次/check/downgrade base/reupgrade/check通过，head0006。测试栈仅回环端口、Mock/Fake、cloud=false、三类Key均不存在；不加载私人.env，不接触原服务卷。

| 最终工程检查 | 实际结果 |
|---|---|
| PostgreSQL全量（最终生产代码） | 478 passed / 6既有跳过，245.34s |
| SQLite全量（最终生产代码） | 455 passed / 29条件跳过，161.29s；新增2项PG专用条件跳过，未改旧skip |
| 普通Playwright，legacy容器路径 | 13 passed / 3真实opt-in skipped，1.4min，末轮包装器exit0 |
| 前端lint / TypeScript / 生产构建 | 通过，Next.js16.4.2 |
| Ruff / format / pip check | 通过，92个Python文件，无依赖变更 |
| 独立Compose健康/冒烟 | 通过，真实上传/DuckDB、Fake RAG SUM=60及会话操作 |
| 一次性库迁移及回滚 | 通过，重复upgrade/check/downgrade base/reupgrade/check，head0006 |

较早全量为PG477/6、SQLite454/29。后续增加离线预缓存计时测试并修正严格工具范围响应，最终再次运行完整回归，以上478/455为真实末次计数；不把后加测试虚加到较早运行中。

末次定向：`phase22-tests-postcache.py postgres/sqlite`针对test_rag22、test_rag18、test_tracing、test_metric_diagnostics，PG39 passed/19.98s，SQLite37 passed/2 PG专用跳过/17.41s，包含后加缓存测试。容器镜像已同步末次评测器并再次健康/冒烟通过；浏览器末轮之后另修正离线计时和仅registered模式的工具范围响应，legacy浏览器路径和前端未变；最后478/455全量覆盖了严格模式修正。

## 提交前审计

完整历史只读审计：32提交/517 blobs，当前三个Key精确匹配0，禁入文件路径0；候选组secret_assignment48、credential_url37、local_path12，与阶段21已复核历史一致，未发现新增真实秘密。待提交25文件另查三个当前Key、私人Embedding URL/hostname和密钥模式，均0匹配；这是有限扫描与人工复核，不是绝对无秘密保证。`.env`、运行日志/数据库、测试截图和浏览器产物继续被忽略；新的CSV仅为已人工确认的4行合成夹具。

暂存审计发现默认Git换行规范化会使新CRLF题集的索引SHA不等于人工复核SHA；仅为阶段22新文件增加.gitattributes -text字节保留规则，重新暂存（--renormalize限定新文件）后，三份冻结文件索引哈希均与回执一致。另从索引导出独立backend/app副本，load_fixture复核18知识/26查询通过，不修改标签或冻结哈希。最终容器45份Python源码均与工作区一致，cloud=false、三类Key均无；核对恰好两个新卷的项目标签与名称后只清理datapilot-phase22，原服务/卷与本机PG保留。

保护快照覆盖71份阶段11–21题集、报告、冻结/复核和原始JSON，提交前逐字节均不变。旧0/2及各阶段保留失败原样，不重算历史成绩、不追分重跑旧留出。当前.env未改，LangSmith保持false，真实测试仍opt-in，未新增依赖/迁移。公开报告不含私人端点、模型Key、Cookie或用户原始数据；云端本阶段无新导出。

交付为独立本地提交，SHA由完成后的`git rev-parse HEAD`及最终回复给出，不自引用或重写历史。无GitHub推送、合并、Tag、Release或生产部署，远程CI未运行。工程提交具备本地安全条件；**真实语义质量发布条件未满足**，默认策略没有切换。

## 修改与未完成项

新增证据筛选、独立RAG22评测CLI/语料/评分/冻结/复核、确定性测试和阶段报告；修改.gitattributes冻结字节保留、config/.env.example显式选项、knowledge检索包装、analyses原问题范围、Graph来源状态、tracing策略哈希及README。未修改阶段21指标解析、SQL安全策略、依赖、数据库结构或前端生产代码。

未完成项：真实语义检索质量和误召回改善没有新证据；默认legacy风险保留；严格模式非指标知识支持不完整；新指标的未知自然语言表达仍可能澄清；合法结构化契约不等于业务真值；无独立留出、无真实模型采用率/端到端质量成绩、无本阶段云端追踪或远程CI。后续必须先处理既有未知用量及新实时账本，再单独验收真实Embedding；是否启用严格默认需真实证据。阶段22结束即停止，不启动阶段23或24。
