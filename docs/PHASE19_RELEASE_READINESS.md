# 阶段19：整体验收与发布准备

验收日期：2026-10-21。进入阶段HEAD=`ddba77b80eedfeca7059ed651539d13c0f61a3f7`，开始时工作区干净。功能分支codex/agent-rag-upgrade。仅本地提交，未推送、合并、打Tag或发布。远端只读核对仍为`f6aaea83a1fe640c7d89a67a18eb10660222d9fb`。

## 结论

**工程回归通过；真实五案例4/5，业务指标自动发现未通过，不能标记完整真实质量验收通过。** 独立精确指标名工具诊断1/1通过，验证了真实定义采用、SQL、DuckDB、浏览器图表、引用、历史与CSV。诊断不替代失败、不重算原分数。六次分析均有真实LangSmith服务器回读与审计；原阶段11–16历史结果和阶段18冻结留出未修改或重跑。

## 真实五案例

| case | SQL/结果/行为 | 契约 | 聊天请求 | SQL/Agent Token | 实测浏览器耗时ms |
|---|---|---|---:|---:|---:|
| aggregate | SUM(revenue)=60，total_revenue列 | 通过 | 3 | 515/2827 | 9552 |
| having | GROUP BY region HAVING SUM(revenue)>25；East=40 | 通过 | 3 | 575/2750 | 7677 |
| metric | 自动发现未得到净贡献额定义，clarification，无SQL执行 | 失败（保留） | 3 | 0/4066 | 9938 |
| missing | 未登记续约留存率，no_match/clarification，无SQL执行 | 通过 | 3 | 0/4313 | 10084 |
| danger | DROP/COPY请求被澄清拒绝，Graph未进入execute | 通过 | 1 | 0/1274 | 7471 |

逐题自然语言、实际SQL、行结果、计划、工具、来源与验证分支见[原始五案例](phase19-live-cases.json)，不是人工填的模型输出。业务问题的预期为region/net_contribution两列、East=6/West=12；实际Agent调用inspect_dataset_schema、search_business_knowledge、get_metric_definition后仍未取得定义，三次模型调用内返回澄清。未生成SQL，未消耗额外工具预算或重试追分。

失败定位：初始向量召回状态为no_match，实际采用来源为空。现有安全追踪只保存工具名而非参数，不能凭这些证据断言是哪一个工具参数错误，也不能把问题归咎于SQL计算。证据支持分类C（来源未命中）与规划未恢复口径；精确根因需要新的开发迭代，不能改本版留出或降低生产阈值追分。

### 独立诊断，不并入4/5

新请求明确要求get_metric_definition按已登记的“净贡献额”名称查找，而不是给模型固定SQL。真实模型实际调用inspect_dataset_schema和get_metric_definition，读取允许的持久化知识并引用chunk；Graph按prepare→retrieve→plan→generate→validate→execute→check_result运行。SQLGlot/Schema批准后DuckDB返回East=6、West=12；真实浏览器断言ECharts柱图、持久化document内容/ID、刷新历史恢复和下载CSV中的两行。截图仅留本地忽略目录，已人工查看。

真实结果见[诊断记录](phase19-exact-name-diagnostic.json)。初始向量检索status仍为no_match，而后精确指标工具补入真实定义、rag.used=true；这是两条检索路径，不能将本案例描述成向量语义命中。UI当前沿用初始检索状态文本，与随后工具引用并列，容易让读者困惑，记录为后续观测/文案问题，本阶段未改生产行为。

### 成本与边界

- DeepSeek deepseek-flash：初始13次+诊断3次=16次真实聊天请求（Agent13次、SQL3次），修复0次。完整可用聊天Token=20,120。SQL输入1664/输出185/合计1849；Agent输入16671/输出1600/合计18271。没有未知usage被当作0后继续付费。
- 运行前估算25–45k、90k停止线，五案例各最多3Agent+2SQL。脚本在下一分析前预留70k（64k Agent保守上界+本合成小Schema的SQL余量），未知usage即停止；诊断前16,320+70,000<=90,000。结束20,120，不再追加模型调用。生产模型/工具/Token预算未增加。
- text-embedding-v4 1536真实索引ready，引用核对通过；阶段19服务没有完整Embedding出站Token计量，Embedding总Token与账单费用未知。阶段18独立HTTP计量见该阶段报告，不能拿它当阶段19用量。
- 实际费用未知，没有用固定单价计算账单。六次并非统计泛化测试；原五案例留在4/5，不将另加诊断汇成5/5。

## LangSmith真实证据

六条服务运行均从服务器读取完整子树，检查Graph节点、真实工具/model调用、实际时段、SQL验证状态、调用计数，以及使用每trace UUID加盐计算的采用来源哈希。输入/输出/serialized为空，extra仅metadata，server ls_run_depth与实际层级一致。候选只有哈希、rank/量化数值，没有正文、SQL、结果行、用户ID、Schema、环境、私有地址或凭据。

最终[云端回读回执](phase19-cloud-readback-v2.json)逐条以analysis_id联结浏览器/本地完成日志，并以唯一耗时、状态、调用数和实际起止窗口验证关联，避免只凭本地UUID编造URL。契约判定在本地回执关联，**没有把失败写成云端成功分数**，最终验收工具不修改云端执行记录。

- analysis `7aff21a8-5558-42af-87ab-19e4af19530a`：[真实运行](https://smith.langchain.com/o/7117ff9a-d5cf-4d63-baf2-64eac6e4ea95/projects/p/cdbe7c38-c22e-4901-8b41-e7378fb652db/r/ec835bad-2829-40b4-b666-09ebb56f3a23?poll=true)，本地契约=True。
- analysis `7da15caa-792e-4f60-aaba-ebc5ce1402a3`：[真实运行](https://smith.langchain.com/o/7117ff9a-d5cf-4d63-baf2-64eac6e4ea95/projects/p/cdbe7c38-c22e-4901-8b41-e7378fb652db/r/54a0b726-5be3-4157-a15f-7b649f6c81f7?poll=true)，本地契约=True。
- analysis `608ff759-197a-45a2-806d-ce64aa226754`：[真实运行](https://smith.langchain.com/o/7117ff9a-d5cf-4d63-baf2-64eac6e4ea95/projects/p/cdbe7c38-c22e-4901-8b41-e7378fb652db/r/ce5f0d97-4f00-4585-9a3d-1db45ec4b8a2?poll=true)，本地契约=False。
- analysis `e13891b7-ac67-490c-8721-a7181d94aa0b`：[真实运行](https://smith.langchain.com/o/7117ff9a-d5cf-4d63-baf2-64eac6e4ea95/projects/p/cdbe7c38-c22e-4901-8b41-e7378fb652db/r/7eb4aa2e-8f67-4688-aa3a-74805cc821da?poll=true)，本地契约=True。
- analysis `c03f4893-2e94-431d-92e1-1b847fd9393c`：[真实运行](https://smith.langchain.com/o/7117ff9a-d5cf-4d63-baf2-64eac6e4ea95/projects/p/cdbe7c38-c22e-4901-8b41-e7378fb652db/r/b4351b74-fa13-4336-80ba-6e1c1b9d2f76?poll=true)，本地契约=True。
- analysis `91d7e1d3-25a9-4093-a532-6b8e5ad599cc`：[真实运行](https://smith.langchain.com/o/7117ff9a-d5cf-4d63-baf2-64eac6e4ea95/projects/p/cdbe7c38-c22e-4901-8b41-e7378fb652db/r/5527b880-f0ce-4f04-8bae-e23ff8d10fdd?poll=true)，本地契约=True。

早期云端验收脚本先因结构化日志不含workflow报KeyError，改用实际analysis_id联结浏览器证据；批量回读遇限流；尝试SDK元数据更新后未观察到契约回读，失败回执保留，随后改为每条间隔15秒的只读核验。源码检查发现SDK update_run会缓冲且默认改end_time，因此最终工具移除更新；回读逐条核对原执行耗时未被改写。完整尝试记录见[回读尝试](phase19-cloud-attempts.json)。没有为这些尝试重跑模型，也没有放宽隐私白名单。SDK旧read_run/get_run_url有2027年移除警告，依赖当前锁版本可用，后续需独立迁移。

## 工程回归与部署证据

| 类别 | 实际结果 |
|---|---|
| PostgreSQL全量主体回归 | 415 passed / 6既有条件跳过，253.07s |
| SQLite全量主体回归 | 394 passed / 27既有条件跳过，181.62s |
| 最终新增回读工具与隐私/评测定向回归 | 23 passed，包含2项新增工具测试；主体全量在新增工具前运行 |
| 前端lint / TypeScript / 生产构建 | 通过；新增两个专项后lint/typecheck再通过，生产页面无改动 |
| 普通Playwright生产容器浏览器 | 13 passed / 3显式真实专项默认关闭跳过，1.2min |
| 真实浏览器五案例专项 | 1测试失败：内部4/5契约，原始失败保留 |
| 真实精确口径独立诊断 | 1 passed，18.4s；分析服务耗时8580ms |
| Docker Compose | 本机Docker29.6.2构建/启动/健康/冒烟通过；非CI历史结果 |
| 迁移 | 临时独立PG库upgrade两次、check、downgrade base、reupgrade head、check通过，head0006 |
| 依赖/代码 | 主运行环境与独立eval-venv pip check通过；Ruff检查/格式通过；无依赖变更 |

本机Python3.12.14、Node24.15.0、PG17.10/vector0.8.7；独立Linux容器也确实执行查询。Compose项目datapilot-phase19使用15532/18100/13100，构建公开ECR基础镜像；没有重置原服务数据库。真实测试仅目标独立后端短暂加载本地env并显式开追踪，结束恢复Mock/Fake、cloud=false，确认三类Key均不在恢复后的容器中；私人.env从未改写。

合成CSV浏览器上传3行/4列；另独立XLSX上传2行/2列、画像与匿名分析401通过，6个analysis均有analysis_started和完成/澄清审计。[API/审计记录](phase19-api-audit.json)。测试账号只在独立测试库；数据集在finally删除；清理前核对项目标签和恰好两个新卷，随后只清理本阶段栈，原容器/卷和本机PG数据保留。

### 主要实际命令

```powershell
# 真实URL只从本地Settings读取并在子进程环境设置，不放命令行/日志
.venv/Scripts/python data/runtime/phase19-tests.py postgres
.venv/Scripts/python data/runtime/phase19-tests.py sqlite
.venv/Scripts/python -m ruff check backend infra/check-migrations.py
.venv/Scripts/python -m ruff format --check backend infra/check-migrations.py
.venv/Scripts/python -m pip check
data/runtime/eval-venv/Scripts/python -m pip check
.venv/Scripts/python infra/check-migrations.py --disposable --host 127.0.0.1
. data/runtime/phase19-env.ps1
docker compose --env-file .env.example up -d --build
.venv/Scripts/python infra/compose-smoke.py
# frontend目录，E2E_BASE_URL指向独立13100且DATABASE_URL指向同一15532容器库
npm run lint
npm run typecheck
npm run build
npm run test:e2e
# 仅真实专项进程：RUN_PHASE19_LIVE=1；诊断另用RUN_PHASE19_DIAGNOSTIC=1
npm run test:e2e -- e2e/phase19-live.spec.ts
npm run test:e2e -- e2e/phase19-diagnostic.spec.ts
# RUN_LIVE_LANGSMITH=1：只读取已有运行，绝不自动再发模型请求
.venv/Scripts/python -m app.phase19_cloud
.venv/Scripts/python infra/audit-git-history.py --details-output data/runtime/phase19-history-audit.json
```

完整命令输出留忽略目录data/runtime/phase19-*.log。临时编排文件包含本机端口/目标服务路径不提交；稳定复现入口为infra/compose.test.yml、已提交浏览器专项和check-migrations.py。没有删除旧测试、调整跳过条件或关闭旧检查。

## 发布前审计与遗留

提交前全部可达Git历史30提交/480 blobs：当前3个Key精确匹配0、禁入路径0；模式候选48 assignment/35 credentialURL/12 localpath与已逐项复核基线一致，均为公开示例、合成测试值或历史非敏感本机路径。新增文件另扫描当前Key、私人Embedding地址/主机，零匹配。规则扫描不能证明绝对无秘密。无新依赖、迁移或默认开关变更。

阶段17基线保护的39份旧评测文件逐字节一致；阶段18题集回执、冻结源码/模型/配置哈希仍一致。原严格SQL 0/2、阶段16严格7/8唯一h03失败原样保留；阶段17新增HAVING能力不是改写旧分数。

安全推送源码条件满足，但此次没有推送；远程CI仍仅属于旧f6aaea8，不能当作阶段17–19的CI。完整质量验收未通过，禁止描述为可发布生产。后续应在新开发版本解决自动口径发现、无关/注入误召回、初始检索与精确工具状态文案；FlashRank本次明显负收益继续关闭。若再次正式验收需新独立留出，不重跑本次留出追分。

阶段提交：17=`963be80825b679b32cdfd6d48c135e052f7945da`；18=`ddba77b80eedfeca7059ed651539d13c0f61a3f7`；阶段19提交SHA由交付时git log输出，报告不自引用尚未创建的提交。
