# RAG22 development v1

18条合成图书馆知识、26查询，仅开发集；含两个正式登记别名压力用例，不宣称26种独立意图。与阶段18支持/物流、阶段21服务领域分开，不使用旧留出改写。

- `corpus-v1.json`：逐条业务含义、owner/scope、相关来源、预期证据状态、最终决策与理由。
- `library.csv`：4行合成数据，含NULL、returned/lost；不含用户数据。
- `contract-v1.json`：排名、拒答、引用、权限与延迟分母。
- `freeze-v1.json`：比较前冻结的上述三文件SHA-256。
- `review-v1.json`：用户“已复核 RAG22 标签”的独立回执，绑定相同SHA；corpus中的authoring状态保留，不静默改写冻结文件。

九个正例（其中三题是同一指标的正式名/登记别名）、十六个授权负例、一个越权请求。正例排名宏平均，Precision@4固定分母4；越权不进排名/拒答分母。负例内含歧义、不适用、缺定义和恶意知识，不把它们的分类统一改成not_found。所有候选按owner/dataset/space预过滤。

开发比较固定vector/0.70/none/k4，第二策略复用同一候选并加登记证据筛选，目录补充另计。当前真实服务因既有未知用量护栏阻塞；`offline`仅Fake词法向量与真实PostgreSQL/pgvector。不得将其排名指标解释成真实语义Embedding成绩或独立留出泛化结果。

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python -m app.rag22_evaluate --mode offline --output data/runtime/rag22-new-unique.json
# 输出独占；重复路径拒绝。需运行本机独立datapilot_test及pgvector。
# live模式目前仅做停止规则预检，不具备绕过未知用量或自动重试的入口。
```

新版本如需修订标签，保留v1与回执并新增版本后重新人工复核。阶段24未来再建立独立留出，本阶段没有留出成绩。
