# RAG24：独立 Embedding-only 协议

这里只保存合成语料、人工复核回执、授权与冻结资产，不包含真实用户业务数据。

- `development-v1.json`：24条知识、36题。原RAG22的18条知识/26题沿用已复核标签；补充6条结构化非指标知识、6条正例、4条无答案题，另经人工确认。
- `holdout-v1.json`：全新维修工坊领域，28条知识、34题，10指标/10非指标/10拒答/4安全；主题与意图分组不复用开发集。见对应`.review.json`及`docs/RAG24_HOLDOUT_REVIEW.md`。
- `authorization-v1.json`：后续用户指令明确允许新独立实验，保留历史unknown及旧护栏；硬上限80次HTTP/20000输入Token，聊天/裁判/LangSmith均0。
- A：legacy/vector/.70/none/top4；B：原registered指标证据；C：B加仅针对实际向量候选的闭合非指标事实契约。配置见`candidate-config-v1.json`，生产默认不变。
- `diagnostic-config-v1.json`：开发阶段阈值0的最近邻诊断，用于找漏召回，不是候选配置或验收得分。
- `acceptance-v1.json`：用户预设90%门槛及零安全违规条件；固定Precision@4分母4。一题一个相关源时满召回的Precision@4为0.25。
- `holdout-v1.freeze-v3.json`：唯一正式评分使用的冻结版本。保存代码/配置/标签/语料/锁文件/tokenizer哈希；文本按canonical-LF校验，兼容Git行尾。v1、v2及v2准入失败保留，均未评分；仅修复冻结准入的行尾不一致，没有改标签、标准或策略。

固定账本为`data/runtime/rag24-live/ledger.sqlite`，请求前FULL同步持久化预留；未知或未结束请求拒绝后续发送，输入SHA唯一，不能重放。真实向量一次生成后所有策略只读复用；缓存缺失直接失败，无网络回退。生产服务不调用此执行器。

源码目录运行入口为`PYTHONPATH=backend python -m app.rag24_evaluate`，真实入口还要求`RUN_LIVE_RAG24=1`、已审核授权/语料、匹配的固定本地tokenizer、真实Provider和独立测试数据库。现有真实轮次已结束，**不要删除账本、授权或全局holdout claim来重跑**。新输出路径不能绕过正式留出的一次性claim。

离线复核现有真实结果，无网络、无数据库、无新增付费请求：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python infra/verify-rag24-results.py
```

需已安装`backend/requirements.lock`和可选`backend/requirements-eval.lock`中的依赖。评测额外依赖tokenizers固定为现有锁版本0.23.3；生产镜像不要求额外安装tokenizer。复核器重新计算34题真实向量余弦候选、三种策略全部标签指标、账本与冻结资产；这叫“离线核验既有真实证据”，不新增一次正式评测。

真实分数、局限、资源清理与工程回归见`docs/PHASE24_RAG_RETRIEVAL_QUALITY.md`及`docs/rag24/`。真实Agent来源采用、公式执行和SQL结果在本轮均为NOT_RUN。
