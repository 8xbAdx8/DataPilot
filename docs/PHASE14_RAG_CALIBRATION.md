# 阶段 14：检索校准与真实框架评测

阶段 13 原始报告与阶段 11/12 结果保持不变。44 条人工复核查询，开发与留出各 22；固定选择规则、源码/提示词及模型权重 SHA 见 [冻结清单](rag-freeze-v1.json)。留出只运行一次原始基线与一次选定方案，未据结果继续调参。

| 组/策略 | Recall@4 | MRR | nDCG@4 | 错误召回率 | 错误知识采用 | 严格 SQL | 检索 p50 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
|baseline-development|1.000|1.000|1.000|1.000|0.000|0.333|356.0|
|development-hybrid|0.875|0.875|0.875|0.154|0.000|0.333|1935.8|
|development-flashrank|0.750|0.688|0.704|0.154|0.000|0.333|2171.8|
|development-selected|0.875|0.875|0.875|0.154|0.000|0.333|1956.8|
|holdout-baseline|1.000|1.000|1.000|1.000|0.250|0.000|1999.8|
|holdout-selected|0.875|0.875|0.875|0.462|0.250|0.000|1860.4|

全部组的跨权限返回泄漏为 0；各组明确拒绝 1 个未授权数据集。Precision@4、no-answer precision/recall、正确采用、引用命中等完整指标见 [汇总 JSON](rag-comparison-summary.json) 和逐题原始报告。正例只有一个相关 chunk，Precision@4 分母仍为 4。不同时间的新鲜 HTTP 延迟受提供商/主机负载影响，不能据这组 p50 宣称线上加速。

## 选择与局限

20 个开发组合的 [真实向量缓存 + PostgreSQL + 实际 ONNX 矩阵](rag-development-matrix.json) 使用 4 个语义阈值与 3 个重排阈值。原 0.20 模式重放逐条来源 ID 与阶段 13 实际报告完全相同。RRF 混合排序未提升本题集质量；FlashRank 的最佳开发组合仍未胜过 vector/0.70，且有额外耗时/资源需求。因此生产默认改为 vector/0.70、reranker=none；可通过配置回滚 0.20。没有改 Agent 提示词、SQLGlot 或 DuckDB 安全策略。

阈值减少误召回也引入漏召回：开发正例 8→7，误召回 13→2；留出正例 8→7，误召回 13→6。留出错误知识采用仍是 1/4（攻击题引用了另一个无关但有权的库存定义，仍请求澄清且零执行），并未改善采用质量。不能把检索改善等同于完整 RAG 问答通过。

**正式留出下游验收未通过**：基线两道 SQL 一道列名不符合 metric 契约，另一道 query_timeout；选定方案两道都 query_timeout。超时仍按原 5 秒（含 Windows spawn 启动）执行并杀死子进程，没有放宽限制或改分数。评测同时运行主机回归，主机竞争/主模块冷启动可能相关，但未独立证明具体原因，不能把失败抹掉。保留原始一次结果，不重跑留出来制造通过。后续应先独立诊断执行冷启动，再创建新版本题集及人工复核；本版严格 SQL 留出分数保持 0/2。

开发三种新鲜真实 Agent 运行各 5 个案例：SQL 数值正确但两道输出别名违约，严格分数仍 1/3；hybrid 的缺口径题出现 agent_failed，其余状态与轨迹全部保留。不能据有限样本声称泛化准确率。

阶段 13 两个纯澄清案例的 raw SQL usage 携带前一题 Provider 状态，这是遥测缺陷；本汇总依据真实节点确定零 SQL 调用后计入 0，不修改原报告。新工具每题创建新的 Provider，并在显式零调用时单独标明 not_called。Embedding 现在采集供应商可用 usage；历史未采集项仍是 null。

## 新框架的实际结果

[Ragas 实际报告](ragas-development-real.json)：DeepSeek deepseek-flash 为显式选择的 judge，独立百炼 text-embedding-v4/1536 用于答案相关性。两题 ContextPrecision≈1、ContextRecall=1，SQL 文本 AnswerRelevancy=0.609566/0.484123。ContextPrecision 是相关条目的平均精度，不等于本项目固定分母 P@4，也不能揭示全部无答案误召回。24 个真实 HTTP 成功，实际 Token 在报告中；金额未知，模型 alias 的不可变部署版本不可获取。

首次安装与启动失败均记录：生产 OpenAI 3.35.1 与 Instructor 的 jiter 约束冲突；隔离评测使用 OpenAI 2.54.0；langchain-community 0.4.2 删除 Ragas 仍导入的 VertexAI 模块，固定 0.3.31 后导入成功；第一次 judge 工厂不接受 dimensions，构造阶段失败、未发送请求，改用显式 1536 维适配层后真实运行成功。这些修正没有换用 Fake。独立 eval 锁与生产锁均 pip check 通过。

FlashRank 0.2.10 / ms-marco-MultiBERT-L-12 实际执行；模型文件 169530273 bytes，Windows 独立进程 16 段合成文本峰值 Working Set 328454144 bytes、加载+重排 753.839 ms。完整检索启动进程另计约 1.4–3.0 秒。矩阵对相同查询/候选缓存实际重排分数，报告的缓存 p50 不当作线上冷启动。重排失败/超时拒绝提供知识，不静默旁路；未作为默认。

## 验证与运行

运行及依赖说明见 [RAG_EVALUATION](RAG_EVALUATION.md)。PostgreSQL 完整回归 353 passed / 6 skipped，检索/Embedding/指标定向 59 passed / 1 live opt-in skipped；SQLite 前一轮完整 328 passed / 27 skipped，最终全量结果归入阶段 15。真实服务题的失败不改为跳过。前端 lint/build/typecheck 通过。没有数据模型变更，无新增迁移。

本阶段只提交本地，未自动推送；工程单测通过与真实留出质量失败分开记录。费用均未知，Token 可用值与各类调用次数见汇总。LangSmith 云端另在阶段 15 验收。
