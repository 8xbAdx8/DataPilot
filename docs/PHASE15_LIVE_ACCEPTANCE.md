# 阶段 15：真实浏览器、LangSmith 与交付审计

验收日期为北京时间 2026-10-21。阶段起点 HEAD `b16651f3724f5a7aeaf1790cdff8815da388a5e9`，分支 `codex/agent-rag-upgrade`；本阶段代码/文档在验收时尚未提交，源码与结果分别保留，提交完成后再检查工作区。阶段 13 本地提交 `af0cd2d`、阶段 14 `b16651f`。最终本地交付提交见 Git log；本阶段不自动推送。

基线 `ae451900d34241736ab1815b32183ce6b5846592` 已正常推送，远端完整 SHA 相同，[Actions 39445512904](https://github.com/8xbAdx8/DataPilot/actions/runs/39445512904) 的 application / compose 均 success。它是阶段 12 的真实远程 CI，不冒充本阶段远程 CI。

## 分清各类证据

| 验证类别 | 实际结果 |
|---|---|
| Mock/Fake 离线及安全回归 | PostgreSQL 355 passed / 6 live opt-in skipped（263.33 秒）；SQLite 334 passed / 27 skipped（151.61 秒） |
| 最后新增的云端数值表示/隐私/故障定向 | 17 passed（1.61 秒）；其中 7 项为后加的精确值/四位量化/错误值判断，不伪称它们已包含在前面的完整测试计数 |
| 真实 Embedding、pgvector、开发/一次留出 | 见[阶段 14](PHASE14_RAG_CALIBRATION.md)，有真实请求与实际查询记录；误召回改善但严格留出 SQL **未通过** |
| 真实 Ragas | 两条合成开发样本、24 个真实 HTTP；上下文 precision/recall≈1，SQL 文本相关性 0.609566/0.484123；是 judge 补充而非权威准确率 |
| 默认 Mock/Fake 生产容器 Playwright | 13 passed / 1 live opt-in skipped（1.3 分钟） |
| 真实模型浏览器专项 | 1 passed（测试本身 19.2 秒，命令总计 25.1 秒） |
| 真实 LangSmith 成功追踪 | 同一浏览器分析在服务器读回，14 个实际 spans、输入/输出为空 |
| 真实 LangSmith 实验 | 22 条标签、22 个运行、17 个反馈及配置/源码摘要均读回验证 |
| 受控安全失败的真实云端发送 | SQLGlot 拒绝 DROP，零执行，4 个实际图节点读回；明确是 Mock 提案，不是模型生成危险 SQL |
| Docker / 迁移 | 独立 Linux Compose 健康、上传/查询/知识/会话冒烟通过；upgrade head 重复执行及 check 通过；完整 PG 回归含临时数据库迁移回滚并保留业务行的测试 |

Ruff、格式、前端 lint / typecheck / 生产构建、两套环境 pip check、Git diff 检查通过。前端本阶段只给真实测试增加可选证据前缀，应用界面代码未改；已读安装的 Next.js Playwright 指南。没有新数据库 Schema，迁移仍为 0006。

## 真实浏览器分析与知识来源

独立栈只装入合成数据，用 DeepSeek `deepseek-flash`、百炼 `text-embedding-v4` / 1536、PostgreSQL pgvector 与真实 DuckDB。浏览器实际上传 3 行销售数据、建立业务指标，提问后真实原生 Agent 调用 inspect_dataset_schema / search_business_knowledge / get_metric_definition，最后生成并执行：

```sql
SELECT "data"."region" AS "region", SUM("data"."revenue") - SUM("data"."cost") AS "net_contribution" FROM "data" AS "data" WHERE "data"."status" = 'paid' GROUP BY "data"."region" ORDER BY "region" ASC
```

实际结果 `East=6、West=12`；ECharts、CSV、刷新后的历史和知识 GET 返回内容均通过断言，见[原始合成证据](browser-real-acceptance.json)。引用在测试期间真实存在，测试结束按原流程删除自己的数据集，不能期待已清理测试环境的引用继续可访问。

这条问题的初始语义检索为 no_match；Agent 的精确指标工具找到授权定义后才采用一个真实来源。因此它证明工具能补足当前问题的知识，不证明 0.70 阈值下所有问法都能语义命中。rag_status 保留初始 no_match，rag_used=true 与 retrieved_count=1 表示最终实际采用，两个阶段应分开理解。

Agent 3 次真实模型请求、3 次工具调用，SQL 1 次真实请求；供应商报告 Agent=4160 Token、SQL=655 Token，合计 **4815 Token**。后端分析 12619 ms。该浏览器流程没有汇总 Embedding usage，所以这一项未知；费用、缓存计价和不可变模型部署版本均未知。不以 Token 数猜测金额。

## 云端服务器证据与隐私

成功 run ID：`0ec2ecdb-f3c1-4a43-8989-442305b20ba7`，直接 [LangSmith 记录](https://smith.langchain.com/o/7117ff9a-d5cf-4d63-baf2-64eac6e4ea95/projects/p/cdbe7c38-c22e-4901-8b41-e7378fb652db/r/0ec2ecdb-f3c1-4a43-8989-442305b20ba7?poll=true)。实际服务器返回的 ID 与容器发送事件的 local_trace_id 一致，读取完整子树核对了节点/工具/模型、状态和起止时间，而不只凭一个本地 UUID 或发送 success。

受控安全拒绝 ID：`6432fba9-92b2-4262-9a94-2bbe71633373`，见[失败追踪](https://smith.langchain.com/o/7117ff9a-d5cf-4d63-baf2-64eac6e4ea95/projects/p/cdbe7c38-c22e-4901-8b41-e7378fb652db/r/6432fba9-92b2-4262-9a94-2bbe71633373?poll=true)。能定位 generate / validate，sql_validation=rejected、status=rejected，execute 不在轨迹中，云端没有 SQL 文本。其用量 0 来自 Mock Provider，不能与真实模型调用混为一谈。

实验 ID：`6535e31b-7875-4f31-b95f-7c6a72d5fc07`，见[实际实验](https://smith.langchain.com/o/7117ff9a-d5cf-4d63-baf2-64eac6e4ea95/projects/p/6535e31b-7875-4f31-b95f-7c6a72d5fc07)，数据集 ID `14a30d00-ff8c-4e2a-905c-2154f85d6022`。先生成只读上传清单，再显式上传。源码父提交、dirty 标记和实际模块 SHA、语料/提示词 SHA、检索配置与模型均从项目元数据读回一致；逐条 labels 一致，17 个反馈也读回比对。云端反馈实测量化为四位小数，完整精度仍保留在本地报告，不能用云端显示覆盖本地权威分数。[合并的服务器验收证据](langsmith-real-acceptance.json)保留原始返回 ID/URL、状态与已核验的元数据。

所有 14 个成功 spans 及 4 个受控失败 spans 的 inputs/outputs/serialized 都为空，extra 只有 metadata；没有原始提示词、SQL、Schema、数据行、文件、知识正文、原始 source ID、用户 ID、Key、会话令牌或数据库连接串。来源哈希按每条 trace 随机 UUID 加盐，不能作为跨请求稳定用户标识。SDK 的输入/输出/元数据过滤与 omit_traced_runtime_info 保留。未知模型名称只记 custom，不能借模型名上传任意字符串。

第一次回读因服务端新增 ls_run_depth 被严格拒绝，先关闭临时容器追踪。只读确认该字段是与真实树一致的 0/1/2 整数后，回读校验才允许它；上传白名单不接受这个任意新增字段。错误类型、字符串/布尔层级或不符的深度仍拒绝。另测试 LANGSMITH_TRACING=true 也不会绕过应用关闭开关，发送故障不能破坏分析结果。

## 失败记录与未解决项

1. 第一轮本机合成分析成功，但旧 500 ms 连接/1000 ms 读取时限下发送未确认；认证只读在较长有界超时下实际成功。发送后台改为 5/5 秒，独立验收 5/10 秒，零自动网络重试。第二轮本机分析 query_timeout，未发送追踪；之后改用独立 Linux 服务完成一次真实浏览器验收，没有放宽 SQL 时限。
2. 第一次全量 Playwright 错误地把账户建到本机库，页面连接临时容器库，14 项登录相关失败。修正 DATABASE_URL 指向同一容器数据库后，原断言完整重跑 13 passed / 1 skipped。仅清理本轮时间窗口中新建、无数据/会话/知识/限流记录的 15 个合成账户，原用户数据未改。
3. 受控失败辅助适配器先只实现带 context 的方法，遇到无上下文路径报 mock_unsupported；补全 generate 后本地定向通过并真实发送拒绝轨迹。没有把适配器失败算作 SQL 安全通过。
4. 阶段 14 正式留出仍有 6/13 误召回、1/8 漏召回、1/4 错误知识采用，SQL 为 0/2；不因本次独立浏览器成功改写这些分数。Windows 冷启动/并发负载与具体超时原因尚未独立分离。后续先诊断执行开销，再用新版本、重新人工复核的基准验证，不能反复调这版留出。
5. FlashRank 没有综合改善，保持可选且默认关闭；模型冷启动/内存成本和工作进程额外的有界 JSON 输入限制需在启用时评估，长中文候选可能显式失败。默认生产镜像不安装 Ragas/ONNX，若启用需配置有依赖的解释器及预下载权重。

## 实际命令与复现入口

```powershell
# 两套完整回归，分别用新的 runtime 临时目录；PG 指向独立 datapilot_test
.venv/Scripts/python -m pytest backend/tests -q -p no:cacheprovider --basetemp=data/runtime/phase15-complete-pg --tb=short --show-capture=no
.venv/Scripts/python -m pytest backend/tests -q -p no:cacheprovider --basetemp=data/runtime/phase15-complete-sqlite --tb=short --show-capture=no
.venv/Scripts/python -m pytest backend/tests/test_langsmith_acceptance.py backend/tests/test_tracing.py -q
.venv/Scripts/python -m ruff check backend infra/audit-git-history.py
.venv/Scripts/python -m ruff format --check backend infra/audit-git-history.py
.venv/Scripts/python -m pip check
data/runtime/eval-venv/Scripts/python -m pip check

# frontend/：Mock/Fake 全量、真实服务专项；DATABASE_URL 与浏览器目标服务一致
npm run lint
npm run build
npm run typecheck
npm run test:e2e
npm run test:e2e -- e2e/live-rag.spec.ts

# 独立 Compose 项目，私有 live override 只在真实合成验收时加入
docker compose --env-file .env.example up --build --wait
.venv/Scripts/python infra/compose-smoke.py
docker compose --env-file .env.example exec -T backend alembic upgrade head
docker compose --env-file .env.example exec -T backend alembic check
```

这些路径是本轮已执行记录，复跑请换新输出目录，不能覆盖原证据。Compose 本轮项目 datapilot-phase15，回归端口 13100/18100/15532，原 datapilot-upgrade-e2e 保留。[评测命令](RAG_EVALUATION.md)、[LangSmith 开关与显式预览/上传](LANGSMITH_PRIVACY.md)、[启动文档](OPERATIONS.md)给出环境配置细节。现有 app .env 始终 false；真实临时容器采证后已恢复无 Key 的 Mock/Fake 与关闭追踪，再完成最终冒烟。

清理前再次从临时后端读取布尔配置：Mock / Fake、追踪 false，聊天/Embedding/LangSmith 三把 Key 均不存在；重复 upgrade head 与 check 通过。核对项目标签下只有本轮的 analytics_data / postgres_data 两个卷后，使用同一 Compose 项目执行 `down --volumes`，已删除本轮服务、迁移任务、网络和两个合成数据卷。再次查询确认本轮容器/卷为空，原 datapilot-upgrade-e2e 的后端、前端、PostgreSQL 三个容器均仍 healthy；没有清理原项目或原生数据库。

## GitHub 推送前结论

可达 26 个提交 / 410 个 blob 的提交前审计：当前三把 Key 精确命中 0、禁止路径 0，包含新增 LangSmith Key 格式检测。模式候选为 secret_assignment 48 组、credential_url 35 组、local_path 12 组，均为已复核的公开开发示例、测试固定值或历史非敏感本机路径，未擅自重写历史。审计输出改为新文件独占创建，避免覆盖阶段 9 的历史审计。

待提交文件逐一检查当前三把 Key、私有 Embedding 地址及凭据/本机路径特征，均无命中；`.env` 和运行产物继续忽略，生产依赖锁与迁移本阶段未改。最终 Ruff / 格式检查（74 个 Python 文件）和 diff 检查通过。提交后的 HEAD、干净状态和新增历史对象审计由最终交付信息记录，避免在文件中写入无法自引用的提交 SHA。

密钥安全审计通过与产品质量通过是两件事。满足凭据审计/工程回归后可供人工审核并正常推送，但真实留出质量仍有明确失败，不能宣传完整真实评测全通过。本阶段所有新增提交默认只留本地；不合并、不 Release、不部署公网服务。
