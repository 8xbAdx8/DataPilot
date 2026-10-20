# 阶段 13：RAG 质量基线与人工标注

起点为干净的 `ae451900d34241736ab1815b32183ce6b5846592`，分支 `codex/agent-rag-upgrade`。24 个可达提交/371 个 blob 的审计未命中当前 Key 或禁止路径。按用户重试授权正常推送成功，远端完整 SHA 相同；[Actions 39445512904](https://github.com/8xbAdx8/DataPilot/actions/runs/39445512904) 的 application / compose 均 success，之后才开始本阶段。

交付分三次本地提交：阶段 13 冻结标注/测量原检索；阶段 14 引入隔离的 Ragas 评测依赖与可选 FlashRank、混合检索并完成开发校准/一次留出；阶段 15 完成脱敏 LangSmith 与统一回归。后续提交不自动推送。

题集 [rag-quality-v1.json](../backend/app/evals/rag-quality-v1.json) 有 23 条合成知识、44 条查询：16 正例、16 困难负例、12 无答案/权限案例，开发和留出各 22。销售与库存使用不同概念族，查询没有重复，分组经用户人工复核。确认原话“已复核，确认标签与分组”绑定 SHA，见独立 review receipt；没有把 AI 标签直接称为人工已审。

冻结 SHA：`de5a8ee25f3320082d94a6efcce75b253e2d30ee0caf477bddfbf4cd45dbcd48`。查询、知识和标注各自摘要由 CLI 输出，原阶段 11/12 的题目和报告不改。

先运行当前纯向量 0.20 的**开发组**，保存实际 PostgreSQL/pgvector 候选、来源、采用与执行结果；在这之前不改原检索、Embedding、Agent 提示词和阈值。留出集只在最终配置冻结后进行一次正式对照，不参与选择。

指标按人工来源 ID 计算：正例 Precision@K 的分母固定为 K（缺位置记 0）；Recall 为命中/相关来源数；MRR 为首个相关来源的倒数名次；nDCG 使用明确定义的二元相关等级 1/0。无答案 precision/recall、错误召回率单列，授权拒绝单列，不将服务错误算作正确弃答。来源被检索、被引用、被 Agent 实际选择及 SQL 数字正确分别统计，没执行下游时为 null。

下游真实模型仅对固定 9 个已标注样本执行（开发 5、留出 4），其余不是伪造成功。只发送合成 Schema/问题/业务知识，LangSmith 在基线和调参进程中强制关闭。实际费用未知，提供商报告 Token 和测量延迟照实保留。

真实基线已经完成：[逐题证据](rag-baseline-development.json)。原生产三个模块的 SHA 随报告保留；没有先改阈值或提示词。22 条中授权正例 8 条、应空知识 13 条、未授权拒绝 1 条。正例 Recall@4/MRR/nDCG@4 均 1，Precision@4 为 0.25（每题只有一个相关来源，分母仍固定为 4）。13 条无答案均误召回，no-answer recall=0、false retrieval rate=1，precision 因无空预测为 null。权限泄漏 0；检索 p50=355.9765 ms。结果证明旧阈值风险，不能据正例命中宣称质量通过。

5 条下游使用真实 DeepSeek 原生 Agent，正确知识采用 2/2、错误知识采用 0/5；3 条 SQL 数值都正确，但毛利题输出 gross_profit_difference、行数题输出 row_count，违背冻结的 metric 列名契约，**严格 SQL 成绩 1/3**。两条澄清/拒绝没有执行 SQL；保留失败不修改题目或评分。实际 Embedding HTTP 成功数、耗时及 Agent/SQL Token 在 JSON 中，原适配器没有采集 Embedding usage，因此这项和金额未知。测试数据库所有表计数与运行前相同，外层事务回滚；临时 DuckDB 已自动清理。

执行命令（PowerShell，工作目录为仓库根目录）：

```powershell
$env:PYTHONPATH='backend'
$env:RUN_LIVE_RAG_EVAL='1'
$env:EVAL_DATABASE_URL='<独立已迁移的 PostgreSQL _test 数据库连接串>'
.venv/Scripts/python -m app.rag_evaluate --live --split development --downstream --output data/runtime/phase13-baseline.json --embedding-cache data/runtime/phase13-vectors.json
.venv/Scripts/python -m pytest backend/tests/test_rag_metrics.py backend/tests/test_knowledge.py backend/tests/test_sql_guard.py -q
```

PostgreSQL 上述定向测试 80 passed（8.73 秒），含手算排名、错误不算弃答、未执行下游返回 null、来源采用与严格行键比较。SQLite 全套 322 passed / 25 skipped（112.68 秒）；原有 PostgreSQL/真实外部 opt-in 跳过项未修改。Ruff 检查/格式通过。基线测量完成不等于 RAG 质量达标；阶段 14 必须保留并比较这些缺陷。
