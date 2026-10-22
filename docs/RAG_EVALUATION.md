# 可复核的 RAG 评测

先读[人工复核记录](RAG_LABEL_REVIEW.md)和[原始基线](PHASE13_RAG_BASELINE.md)。题目 JSON 是冻结的创作快照，独立 review receipt 的 SHA 与用户确认才是有效审阅状态；修改任何标签必须升级版本并重新复核。旧阶段 11/12 分数不改。

SQL 正确性专项使用独立的 `app.sql_evaluate`，不修改本页旧 RAG 题集/评分；人工审阅 v1 与日期契约修订 v2 均保留。开发、冻结/一次留出命令以及对旧 0/2 的逐题诊断见[阶段 16](PHASE16_SQL_CORRECTNESS.md)。普通 CI 不调用真实评测；设置 `RUN_LIVE_SQL_EVAL=1` 和 `--live` 才会使用本地已配置的真实聊天/Embedding 服务。

## 安装可选评测环境

兼容性补充：langchain-community 固定 0.3.31，因为 0.4.2 移除了 Ragas 0.4.3 仍导入的 VertexAI 模块；完整版本锁已用 pip check 验证。

Python 3.12，生产 API 仍使用 `backend/requirements.lock`。独立环境通过 `backend/requirements-eval.lock` 安装，不把 Ragas 和 ONNX Runtime 加入 API 启动依赖。生产锁的 OpenAI 3.35.1 要求 jiter>=0.16，而 Instructor 1.17.0 要求 jiter<0.15，所以评测组明确约束 OpenAI 2.54.0；不要将两个锁叠加安装。

```powershell
python -m venv data/runtime/eval-venv
data/runtime/eval-venv/Scripts/python -m pip install -r backend/requirements-eval.lock
data/runtime/eval-venv/Scripts/python -m pip install --no-deps -e './backend[eval]'
$env:PYTHONPATH='backend'
data/runtime/eval-venv/Scripts/python -m app.reranking --prefetch
```

Ragas 0.4.3 使用官方 [collections API](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)。人工来源 ID 指标仍是权威；LLM judge 只补充上下文精度、召回和实际 SQL 文本的答案相关性。它不是 SQL 执行正确率，也不是人工复核。显式 `--judge-from-chat` 表示选择 `.env` 中的真实聊天模型担任有误差的评判者；模型部署修订号不可获得时如实标为 alias。

```powershell
$env:RUN_LIVE_RAGAS='1'
data/runtime/eval-venv/Scripts/python -m app.ragas_evaluate --report docs/rag-baseline-development.json --judge-from-chat --output data/runtime/ragas-new.json
```

只转换固定两条合成开发样本的真实 SQL、合成知识及人工参考定义，不发送原始结果行。关闭 Ragas 遥测与自动 LangSmith 追踪；失败项保留 null 和异常类型，不保存远端错误正文。普通 CI 不导入可选框架、不请求外部服务。

## 检索策略

阶段 14 冻结的选择是 `vector / none / 0.70`，见[真实对照与失败项](PHASE14_RAG_CALIBRATION.md)。代码和 `.env.example` 已使用新默认；已有私有 `.env` 的显式 `RAG_MIN_SIMILARITY=0.20` 会覆盖新默认，需按实际部署意图改成 0.70，或保留它作为回滚。不要改维度或将聊天 Key 复制到 Embedding 配置。

`RAG_STRATEGY=vector|hybrid`，`RAG_RERANKER=none|flashrank`；FlashRank 必须与 hybrid 配合。PostgreSQL simple 全文检索无法给中文短句提供合适的分词，本实现用有界的中文二元片段/英文标识符做参数化关键词计数。向量和词法两路都从 materialized 授权 CTE 查询，先应用 owner、dataset、ready、embedding space 和语义阈值，再取候选。以 RRF（常数 60）合并排名，不能直接相加原始余弦与词频；同一 chunk 去重，保留两路名次、融合与重排分数。

[FlashRank 官方 API](https://github.com/PrithivirajDamodaran/FlashRank) 的多语言 `ms-marco-MultiBERT-L-12` 在本机 CPU 上运行，不发送知识到重排服务。权重必须显式预下载，文件 SHA 单独保留；请求内不下载模型。每次最多 16 个候选、每段最多 800 字，独立进程超时杀死，失败不静默退回未排序知识。无权条目不进入重排。外部权重许可证需随使用保留（官方分发仓库标为 CC-BY-SA），依赖许可证以锁定包为准。

开发比较复用同一批真实向量以避免服务漂移和重复计费。报告明确区分缓存重放的数据库耗时、实际模型冷启动耗时及全新 Embedding HTTP 耗时；不能把缓存耗时说成线上延迟。相同问题/候选组合的实际重排分数可在阈值扫描中复用，不是 Fake 语义模型。

```powershell
$env:EVAL_DATABASE_URL='<独立已迁移的 PostgreSQL _test 数据库连接串>'
$env:RERANKER_PYTHON=(Resolve-Path data/runtime/eval-venv/Scripts/python.exe).Path
.venv/Scripts/python -m app.rag_calibrate --cache data/runtime/phase13-vectors.json --flashrank --output data/runtime/matrix-new.json --freeze data/runtime/freeze-new.json
```

选择规则在运行前固定：最大化 `(正例 Recall@4 + no-answer recall)/2`，其次 MRR，质量相同优先 none/vector。服务错误不能当成正确弃答，权限泄漏非零的配置不参与选择。人工采用与 SQL 得分只有实际执行下游后才有值，其余为 null。

留出组只在配置、Agent 源码（含提示词）、语料 SHA 冻结后做一次正式对照；不能拿留出成绩继续挑阈值。每次报告用独占新路径创建，以免覆盖历史。CLI 要求 `RUN_LIVE_RAG_EVAL=1` 和 `--live`，LangSmith 在质量评测中固定关闭，最终云端验收单独显式开启。
