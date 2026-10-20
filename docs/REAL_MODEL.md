# 接入真实模型

升级后聊天与 Embedding 是独立能力。Agent 复用聊天 Key/模型配置创建专用 LangChain 适配器；DeepSeek/OpenAI 原生工具通过 `RUN_LIVE_AGENT=1` 专项验证，通用兼容端点还需 `AGENT_NATIVE_TOOLS=true`。不能从“SQL JSON 能调用”推断“原生工具能调用”。只想使用原有单轮 SQL 接口时，可显式 `AGENT_ENABLED=false`；所有模式都执行相同 SQL 安全校验。

知识索引需要另设 `EMBEDDING_PROVIDER=openai`、`EMBEDDING_MODEL=text-embedding-3-small`、`EMBEDDING_BASE_URL=https://api.openai.com/v1`、`EMBEDDING_API_KEY`、`EMBEDDING_DIMENSIONS=1536`。默认 `none` 明确未配置，不复用 DeepSeek 聊天 Key。真实接口按 OpenAI 官方 Embedding 合同实现并完成受控 HTTP 合同测试，尚未调用真实 Embedding 服务。`fake` 仅用于演示词项哈希检索，页面会显示测试向量提示。先用 `RUN_LIVE_EMBEDDING=1` 跑 `backend/tests/test_embeddings.py` 的显式专项，再重建知识索引。

新增真实专项默认关闭，参见 [固定评估](EVALUATION.md)。[阶段 9](PHASE9_INTEGRATION.md)已单独启用并通过真实 DeepSeek 原生工具、Graph/SQL/历史专项；独立 Embedding、语义 RAG、云端追踪和完整真实评估仍阻塞。下方首次单题记录属于升级前证据。常驻开发验收进程仍为 Mock/Fake，未改写用户的 `.env`，切回真实配置须停止该测试 API 后重启。

项目已有兼容 Chat Completions 的 Provider。首次可使用 DeepSeek；这里不涉及部署模型或训练模型，而是由 FastAPI 调用提供商 API 生成 SQL，再由本地安全校验和 DuckDB 执行。

## 1. 创建 API Key

登录 [DeepSeek 开放平台](https://platform.deepseek.com/)，创建 API Key，并确认 API 账户余额可用。调用会消耗提供商额度，费用以控制台为准。不要把 Key 发到聊天、截图、公开 Issue、前端或 GitHub 仓库。

## 2. 只修改本地配置

打开根目录 `.env`，保留原来的数据库等配置，只替换这些行。如果文件不存在才从 `.env.example` 复制，不覆盖已有配置：

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-flash
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=在这里填写你的真实Key
LLM_TIMEOUT_SECONDS=60
```

仓库的 `.env.example` 必须保持空 Key。`.env` 已被 Git 忽略，可用 `git check-ignore .env` 确认；不要用 `git add -f` 强行提交。进程环境变量优先于 `.env`，如果之前在终端设置过同名变量，应先检查并移除旧覆盖；不要打印密钥值。

模型名和 Base URL 于 2026-10-20 对照 [DeepSeek 官方入门](https://api-docs.deepseek.com/zh-cn/)核对。API 默认启用思考；适配器只在 `LLM_PROVIDER=deepseek` 时显式发送 `thinking.type=disabled`，避免思考内容耗尽当前 1000 Token 的 SQL JSON 输出预算。参数依据见[官方接口](https://api-docs.deepseek.com/api/create-chat-completion/)。模型更新后以官方文档和账户可用列表为准。

## 3. 重启后端

在后端终端按 Ctrl+C 停止服务，再从项目根目录执行：

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

配置在进程内缓存，编辑 `.env` 后必须重启。前端通常无需重启；刷新页面后确认提供商和模型已更新。使用 Docker 时需 `docker compose up -d --build --force-recreate backend`；单独 restart 不会重新加载容器的 env_file。当前原生服务与隔离容器验收使用不同端口，具体状态见升级验收。

## 4. 跑一次真实 API 测试

从项目根目录运行；这会实际调用一次配置的提供商并产生 Token 用量：

```powershell
$env:RUN_LIVE_LLM='1'
try {
    .\.venv\Scripts\python -m pytest backend/tests/test_live_llm.py -q -p no:cacheprovider
} finally {
    Remove-Item Env:RUN_LIVE_LLM -ErrorAction SilentlyContinue
}
```

测试创建包含 10/20/30 的合成数据，要求真实模型生成 COUNT 查询，通过 AST 校验后由 DuckDB 执行，结果必须为 3。只有显示 `1 passed` 才能记录该模型在此问题上通过；这不是整体准确率评测。不要开启 RUN_LIVE_LLM 后直接跑全部 Mock 测试并把结果当作真实模型评测。

## 5. 浏览器验收

登录工作台，上传 `data/examples/sales.csv`，问“这个数据集有多少行？”，应为 8；再问“按 region 统计 revenue 总和”，应与 DEMO 中的 67,000 / 17,700 / 11,000 一致。检查生成 SQL、实际结果和“执行详情”中的提供商、模型与 Token 来源。API 失败或 SQL 不安全时保持失败，不切回 Mock 假装成功。

只向模型发送问题及字段名/类型，原始行和预览不会发送；字段名与问题本身仍可能敏感，请先用项目合成数据。真实模型只生成 SQL，最终统计数值仍由 DuckDB 计算。

## 错误定位

- `llm_not_configured`：Key 为空，或 Provider 名不受支持。
- `llm_http_error`：检查控制台的 Key、余额、可用模型和 Base URL；当前公开错误不会回显提供商响应正文，不能只凭此错误认定是认证问题。
- `llm_unavailable` / `llm_timeout`：检查网络和超时配置；超时不会执行 SQL。
- `llm_invalid_response`：输出不满足严格 JSON 合同或被截断。
- SQL 安全拒绝：保留拒绝结果，不能通过放宽校验来完成接入。

其他提供商使用 `openai` / `openai_compatible`，须自行设置对应的模型、Base URL 和 Key，并支持当前发送的 JSON Output、temperature、max_tokens 及 Chat Completions 格式。并非所有兼容 API 或模型都支持全部参数。

## 已执行记录（2026-10-20）

用户在本机配置并授权后，真实 `deepseek-flash` 专项 **1 passed**（2.42 秒包含完整 pytest 测试过程，不能当作模型延迟指标）：合成数据 10/20/30 → 真实 API 生成 SQL → AST 安全检查 → 受限 DuckDB 执行 → 单字段结果为 3。没有使用 MockTransport、Mock Provider 或结果兜底。

已重启本项目后端，直连 8000 和前端同源代理 3000 的 `/api/config` 均显示 deepseek / deepseek-flash，`/api/health/ready` 正常。未额外调用付费模型来执行浏览器分组/图表全套；现有 CI 的浏览器与容器查询使用 Mock Provider，必须与本次真实 API 专项分开理解。Key 只保留在忽略的本地 `.env` 中，不在验证记录中保存凭据或提供商原始响应。
