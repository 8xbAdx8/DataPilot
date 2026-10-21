# 阶段21：业务指标自动发现整改

开始日期2026-10-21，交付日期2026-10-22（Asia/Shanghai）。起点为已推送且CI通过的`d8063cf7480941239f71b187a824bdaffc152086`，分支codex/agent-rag-upgrade，开始时工作区干净。本阶段只创建独立本地提交，不推送，不启动阶段22。

## 结论与证据边界

新增独立开发集4/4通过：正式名称、已登记别名、已登记自然语言别名均由真实DeepSeek工具调用及SQL生成得到North=50、South=22；未登记定义由后端返回澄清，Agent与SQL生成模型零调用（初始Embedding仍执行）。另一次旧格式真实补充验收失败：初始Embedding为embedding_unavailable，Agent首个模型调用返回agent_failed，未取得该调用usage，已停止进一步真实调用。不得写成旧格式真实通过、全部真实质量通过或17,230为完整总用量。

原阶段18选定方案正例8/8、误召回3/8和阶段19真实五案例4/5、独立精确诊断通过保持原样。新开发用例不是旧留出，也不是旧分数修订；本阶段不宣称RAG误召回已解决或开放自然语言泛化已通过。

## 先调查，再修复

1. 阅读README、架构、安全、开发、RAG评测、阶段18/19报告与真实JSON；确认旧追踪只有工具名、计量和来源，未保存实际工具参数。因此不能断言旧metric案例传错了名称，也不能补造这些参数。
2. 在修改解析行为前增加PlannerContext.record_tool和确定性诊断测试（与既有Agent回归16 passed）。生产诊断只有有界类别/长度/计数；原始参数及工具返回只由独立合成验收observer在本地捕获，owner/dataset必须属于当次临时夹具，无Web导出开关。
3. 新合成四行服务数据：North已结70/20→50，取消30/5排除；South已结40/10及NULL/8→40-(10+8)=22。预期先按独立算式登记，覆盖SUM分项忽略NULL，不能改成SUM(fee-labor)。正式名“已结服务净额”，别名“服务实收”“service_net”和“扣除人工成本后的已结服务收入”。
4. 原实现的真实直接调用：metric_definition(正式名)→retrieved/1引用；metric_definition(已登记别名“服务实收”)→no_match/0引用。原函数只有lower(title)==name，没有别名解析。原分析后端只保证计划ID来自检索，不检查公式/过滤是否适用。
5. 一次真实新基线中，初始上下文使Agent自行找到正式名：实际调用get_metric_definition(name=正式名)、inspect_dataset_schema(scope=current)，最后成功；3次聊天/4230 Token。这未复现旧线上失败，但显示成功依赖模型从候选恢复正式名称。实际参数/返回/SQL/Schema与状态保存在[新基线](metric21-baseline-v1.json)，不能把这次成功说成旧失败已重跑修复。

证据支持的根因是协议缺口：别名不能确定解析、向量未命中时缺少独立的适用定义路径、计划来源仅作成员检查。旧失败的单次参数原因仍未知。改动针对这些可复现缺口，没有降低向量阈值或增加Agent预算。

## 最小实现与实际文件

- `metric_resolution.py`：复用既有KnowledgeDocument/Chunk，SQL先做owner、dataset/个人范围、ready、Embedding空间限制，再建立最多100条目录。MetricContract/MetricFilter严格Pydantic格式，正式名/显式别名规范化，唯一→resolved；多候选→ambiguous；无登记→not_found；缺字段/非法公式→inapplicable；不充分正文→insufficient_evidence。
- `knowledge.py`：原get_metric_definition实现改为复用目录解析。旧精确读取保持可读，但不充分定义明确分类，不能当作有效业务计算证据。引用保持真实持久化正文与ID，未伪造canonical excerpt。
- `analyses.py`：按问题中实际登记名称/别名解析，输出列别名不构成指标授权。唯一适用来源优先进入最多4条规划上下文；缺失/冲突/不适用时澄清。Agent忽略必需来源不能进入SQL；无原生字段依据却声明指标的计划也澄清。关闭Agent不解除业务证据门槛，合法定义来源仍保留。
- `analysis_graph.py`：增加evidence_guard和有界注册上下文。原SQLGlot/Schema校验后核对实际SELECT聚合表达式及WHERE必须AND条件，再允许execute。错误公式、漏过滤、OR绕过为metric_contract_violation，不进入一次字段修复或DuckDB。
- `analysis_agent.py`：安全工具结果分类诊断，并说明get_metric_definition支持登记别名；未扩展五个工具、模型/工具/Token上限或重试策略。
- 新合成CSV/JSON、新工具诊断与指标测试、新独立opt-in验收CLI。没有依赖、数据库表/迁移、前端生产代码或检索阈值变更。

精确登记格式、兼容语法和安全边界见[指标契约说明](METRIC_DEFINITIONS.md)。不匹配的陌生自然语言改写仍可能需要登记别名或澄清；个人/数据集同名也保守澄清。本阶段不实现完整指标语义证明或任意代数等价；不同AST的等价公式可能被保守拒绝。业务定义本身仍需人工确认。初始向量状态与最终目录来源并列，统一UI状态留待授权的后续阶段。

## 真实新开发证据

| case | 真实调用/行为 | 实际结果 | 判定 | 耗时ms |
|---|---|---|---|---:|
| official | get_metric_definition + inspect_dataset_schema；Agent2次/SQL1次 | [{'division': 'North', 'service_net': 50}, {'division': 'South', 'service_net': 22}] | 通过 | 10913.08 |
| alias | get_metric_definition + inspect_dataset_schema；Agent2次/SQL1次 | [{'division': 'North', 'service_net': 50}, {'division': 'South', 'service_net': 22}] | 通过 | 8600.57 |
| natural-alias | get_metric_definition + inspect_dataset_schema；Agent2次/SQL1次 | [{'division': 'North', 'service_net': 50}, {'division': 'South', 'service_net': 22}] | 通过 | 10438.978 |
| missing | 后端not_found澄清；Agent0次/SQL0次 | clarification，无execute | 通过 | 2310.721 |

[开发原始记录](metric21-development-live-v1.json)包含实际工具参数、结构化返回、来源/SQL/行值、Schema校验、Graph路径、Token、源码哈希和事务回滚确认。真实工具参数依次为正式名称、“服务实收”、service_net，而不是测试预写的模型响应。每次真实计算经过validate→execute→check_result，三个结果与手算常量一致。缺定义没有伪造SQL或结果。

### 费用与失败保留

- 真实Provider=DeepSeek，模型别名deepseek-flash；Embedding=openai_compatible/text-embedding-v4，1536维；服务权重修订号和账单金额不可获得，私有地址不写报告。仅外发新合成Schema/问题/作者业务定义，原始CSV行不发送。
- 基线3次聊天＋开发9次＝12次有完整usage，已知17230 Token：SQL3496、Agent13734。旧格式补充另有1次真实Agent尝试usage未知，因此聊天尝试13次，**完整聊天Token总数未知**。原始补充JSON的total_chat_tokens在unknown_usage=true时是已知小计，权威汇总将总数记null，不把未知当0。
- Embedding共9次尝试，8次成功、已知344 Token；1次embedding_unavailable未取得usage，Embedding完整总数及费用也未知。
- 预算预估8–20次/20–40k，停止线90k，每次分析前保守预留70k；旧格式调用前已知17230+70000<=90000。遇未知用量后停止，未自动重跑、加预算或改成Mock。CLI新增持久护栏：发现已有报告unknown_usage=true或未完整结束，即使改用新输出路径也会在服务请求前拒绝；3项离线测试通过，包含2项新增预算记录测试。
- [旧格式补充记录](metric21-legacy-live-v1.json)：目录resolved，但初始向量error/embedding_unavailable，第一模型调用agent_failed，SQL0、execute0、无工具返回；agent_failed的更底层原因未记录，不能凭耗时断言模型超时。运行器state=complete只代表收集结束，contract_passed=false。
- LangSmith本轮关闭；未用旧云端记录冒充本阶段新追踪。每次临时owner/dataset事务回滚计数一致。

真实运行后补充了诊断枚举、旧文本/无指标兼容，以及Agent关闭分支的引用保存；结构化开发集算法和期望未改变，后续差异通过本地完整/定向/容器回归覆盖，没有再次调用模型追分。[用量与完整性汇总](metric21-evidence-v1.json)保留真实原始文件SHA。

## 确定性与工程回归

| 检查 | 结果 |
|---|---|
| 原Agent与新增安全诊断（修复前） | 16 passed |
| 初次新指标测试 | 16失败/9通过，定位SQLGlot自动结果别名误拒绝；已修复，不关闭规则 |
| 指标/诊断第二轮 | 25 passed |
| 兼容修复后评测器＋新指标PG定向 | 42 passed，旧测试/标签未改 |
| 最终全部PostgreSQL | 452 passed / 6既有条件跳过，187.76s |
| 最终全部SQLite | 431 passed / 27既有条件跳过，132.49s |
| 最后Agent关闭引用补丁后定向 | SQLite71 passed/17.97s；PG71 passed/15.80s；再补CLI停止护栏后已完整重跑为上述452/431 |
| 前端 | lint、typecheck、生产build通过；无前端生产源码修改 |
| 普通Playwright终验 | 13 passed / 3真实专项显式关闭跳过，55.5s |
| 当前后端容器 | 最终5个生产文件SHA与工作区一致；独立栈健康、migrate退出0，Mock/Fake冒烟通过 |
| 静态/依赖 | Ruff检查、88个Python文件格式、pip check通过；无新增依赖 |

新确定性测试覆盖注册名/别名/大小写、近义冲突、多指标请求歧义、缺字段、数值类型、注入式公式/额外指令键、跨用户/跨数据集、COUNT/SUM/AVG与NULL、空结果、列别名不构成授权、模型忽略来源、关闭Agent及SQL公式/过滤违约零执行。既有工具预算、违规批次、模型忽略终止、安全函数、超时和权限回归保留。

### 失败命令及处理

- 首次完整PG2失败/441通过：空目录的普通/安全测试被过度澄清，以及既有明确“单字段聚合＋等值条件”的正文被判不充分。保留旧评测器、标签、测试；修复普通查询路径与通用显式格式兼容后，相关42项及完整449项通过；最后补丁后完整452项通过。不是通过放宽SQL权限或改预期结果过关。
- 误用了阶段19临时测试脚本，使其本地data/runtime/phase19-{postgres,sqlite}.log被本轮输出覆盖。已承认并保存本轮首轮日志为phase21-*-first.log，后续使用phase21专属脚本与路径；没有伪造恢复旧临时日志。Git中64份历史题集/原始JSON/报告字节完全未改。
- 全栈构建中Node新基础镜像下载缓慢；停止了核对过归属本任务的构建客户端。新后端/迁移镜像确已构建，前端采用UI源码未变的阶段19本地镜像；本机当前源码另通过生产构建。未声称本轮全新重建了前端容器镜像。
- 第一次冒烟在前端尚未ready时连接关闭；wait健康后冒烟通过。普通浏览器首轮10通过/3失败，均为localhost连接拒绝；检查容器running/healthy、OOM=false/restarts=0，具体瞬时原因未确认。更新最终后端并wait所有服务后13项通过，首轮日志保留。一次验收命令的编码名拼写错误发生在测试启动前，改正后执行，不计作测试通过。

## 主要命令

```powershell
.venv/Scripts/python -m pytest backend/tests/test_metric_diagnostics.py backend/tests/test_analysis_agent.py -q
# 独立datapilot_test下完整PG，以及默认隔离SQLite；连接串只置于进程环境
.venv/Scripts/python data/runtime/phase21-tests.py postgres
.venv/Scripts/python data/runtime/phase21-tests.py sqlite
$env:RUN_LIVE_METRIC21="1"
.venv/Scripts/python -m app.metric21_acceptance --mode baseline --output docs/metric21-baseline-v1.json
.venv/Scripts/python -m app.metric21_acceptance --mode development --output docs/metric21-development-live-v1.json
.venv/Scripts/python -m app.metric21_acceptance --mode legacy-development --output docs/metric21-legacy-live-v1.json
# 以上独占原始路径，禁止直接覆盖或自动重跑；未知usage后本轮停止真实调用
.venv/Scripts/python -m ruff check backend
.venv/Scripts/python -m ruff format --check backend
.venv/Scripts/python -m pip check
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
. data/runtime/phase21-env.ps1
docker compose --env-file .env.example build backend migrate
docker compose --env-file .env.example up -d --no-build --wait --wait-timeout 120
.venv/Scripts/python infra/compose-smoke.py
# frontend目录：E2E_BASE_URL=独立13100；DATABASE_URL=同一独立15532库，真实专项=0
npm run test:e2e
.venv/Scripts/python infra/audit-git-history.py --details-output data/runtime/phase21-history-audit.json
```

测试栈只在本机回环端口15532/18100/13100，使用.env.example和显式Mock/Fake，无私人Key或LangSmith启用；不接触原业务数据库/卷。没有迁移变更，既有head0006的容器迁移和就绪检查通过；本轮未再回退业务库。验收后核对两个新测试卷及项目标签后清理，仅保留忽略目录的证据。

## 审计、历史与交付限制

起点保护64份历史文件，包含阶段11–19题集、原始JSON、冻结/复核记录和报告；提交前逐字节核对均未改。新真实开发题只用新合成数据，普通回归中的旧controlled用例不是重新跑真实留出，不回填旧成绩。

提交前全可达历史31提交/496 blobs，当前3个Key精确匹配0，禁入路径0；96组模式候选与阶段20已复核清单一致（公开示例、合成测试值、历史非敏感路径）。新改动另检查当前密钥、私人Embedding端点/主机和运行产物；日志/数据库/截图/.env保持忽略。模式扫描不能证明绝对无秘密，人工复核告警后才提交。

已知未完成：旧格式补充真实服务调用未通过且usage未知；陌生/含糊自然语言不推断未登记定义；SQL契约是有限AST一致性，不是业务真值或任意等价证明；广义RAG误召回与UI统一来源状态未整改。无新远程CI，本阶段仅本地提交；上次CI39625387950属于父提交d8063cf，不能替代本阶段CI。
