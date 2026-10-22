# 阶段24原始证据索引

本目录的真实服务证据只涉及百炼text-embedding-v4、1536维和PostgreSQL/pgvector检索；普通工程测试中的Mock/Fake与之分别报告。没有真实聊天、LLM裁判或LangSmith调用。

| 文件/目录 | 含义 |
|---|---|
| `historical-usage-review-v1.json` | 原阶段21usage未核清，无适用账单凭据，保留unknown |
| `development-budget-plan-v1.json` | 首次发送前预算和未来留出预留，tokenizer与初版代码哈希 |
| `development-v1-embedding-ledger.json` | 首6次请求原始账本导出，原样保留 |
| `development-01` | SQLite跨线程错误，未评分，失败原样保留 |
| `development-02` | 首次实际A/B开发得分 |
| `development-03` | 增加候选C后的开发对照 |
| `development-diagnostic-01` | 阈值0最近邻诊断，展示假阳性，不是验收策略 |
| `development-04` | 增加分组及能力指标后的同配置复核 |
| `development-05` | 真正API commit、第二连接确认后的同配置复核 |
| `development-failure-analysis-v1.json` | 开发A/B逐题失败及真实相似度根因 |
| `candidate-selection-v1.json` | 留出构建之前选择C，开发通过预设门槛 |
| `holdout-v1-embedding-ledger.json` | 留出Embedding完成后的13请求原始导出 |
| `holdout-01/admission-failure.json` | 行尾哈希准入失败：0题、无临时数据库、未取得正式claim |
| `holdout-02/manifest.json` | 正式唯一留出的语料、配置、代码及预算冻结证据 |
| `holdout-02/records.json` | 34题×A/B/C逐题源、相似度、目录/向量来源、权限及引用检查 |
| `holdout-02/summary.json` | 分组指标、能力、延迟、数据库前后状态与清理、账本 |
| `holdout-02/failure-analysis.json` | A九题/B十题失败逐题保留；没有留出后调参 |
| `holdout-02/verdict.json` | 冻结评分器的检索PASS；当时工程回归仍pending，原样保留 |
| `complete-ledger-v1.json` | 完整13请求、实际usage、请求ID、耗时、输入哈希与合成案例关联 |
| `real-vector-cache-v1.npz` / `.json` | 122个真实1536维向量及输入SHA快照；仅数值/哈希，无密钥、正文或请求头 |
| `engineering-regression-v1.json` | 最终本机工程回归结果、命令日志摘要与SHA；普通浏览器为Mock/Fake |
| `resource-cleanup-v1.json` | 50份容器源码一致、无密钥、临时数据库与自建卷清理结果 |
| `precommit-audit-v1.json` | 既有可达历史与首轮63文件待提交索引的密钥/配置审计，冻结blob一致 |
| `delivery-v1.json` | 最终检索范围PASS、本机回归、安全与清理证据哈希及尚未验证边界 |

开发36题：9指标/6非指标/20无答案/1权限。独立留出34题：10指标/10非指标/10无答案/4安全。人工审核回执及所有冻结版本位于`backend/app/evals/rag24/`，不要修改既有JSON或重跑追分。

向量文件为约1MB合成文本的付费服务输出，NPZ仅允许`allow_pickle=False`读取，可离线重算基线候选及全部分数，避免重复付费或供应商漂移。运行：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python infra/verify-rag24-results.py
```

当前生产请求仍使用原默认legacy/vector/.70/none。候选C仅作为显式实验接入，要求结构化主题+属性，不是任意自然语言知识的通用可信判断。完整报告见[阶段24续验](../PHASE24_RAG_RETRIEVAL_QUALITY.md#2026-10-22-受控独立实验续验)。
