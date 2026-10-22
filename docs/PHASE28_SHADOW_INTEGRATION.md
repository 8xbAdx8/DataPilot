# 阶段28：默认关闭的C实验接入与影子模式

起始提交 `58d7a308093c77eea5b0171fa47255ee76b0bc31`，独立本地提交，不推送。仅指标和闭合结构化事实策略C具备当前接入证据；阶段27 D因Recall和多证据充分性不足保持不可接入。没有修改生产默认 `legacy / vector / 0.70 / none`。

新增 `RAG_EXPERIMENT_MODE=off|shadow|enabled`（默认off）与 `RAG_EXPERIMENT_SYNTHETIC=false`。配置拒绝缺少合成声明或同时启用LangSmith的实验；运行时再次要求实际连接为回环PostgreSQL、专用临时库前缀或测试库。仅改配置声明无法打开普通业务数据库。没有面向真实用户流量的自动实验，也没有迁移。

`retrieval_experiment.py`复用已授权候选和指标目录，不调用Embedding、聊天或裁判。shadow深拷贝候选，计算C差异后仅返回受限元数据；旧策略来源和规划、SQL、执行结果保持原值。日志白名单仅增加计数、来源哈希、分类和耗时，不包含查询、正文、SQL、owner/dataset或工具参数。原日志规则继续限制其他字段。

enabled只替换适用来源；未知定义、冲突、证据不足、失效来源或候选异常均澄清/拒绝，不回退到更宽松策略。Agent工具结果还要满足原问题适用范围且仅能返回工具实际获得的来源。指标公式和必需AND过滤、SQLGlot/Schema、来源当前版本锁、执行授权、超时、结果限制和预算均保留。C的普通事实仍依赖人工结构化主题/属性契约，不代表自由文本蕴含正确性。

关闭开关即可恢复旧路径，无需迁移或清理业务数据。13项PG专项断言包括三题off→shadow→off结果/SQL完全相等、无附加HTTP、未知事实澄清、失效来源拒绝、候选异常失败关闭、危险SQL/多语句/未知表列拒绝、指标目录补充和必需过滤OR绕过拒绝。`docs/phase28/*.json`保存三题API差异、结果和来源快照。这些是明确的Mock/Fake确定性API证据，不是真实模型质量成绩。

发现并修复的缺陷：初版影子元数据被原日志白名单丢弃，专项日志断言失败；新增受限字段与caplog断言后通过。失败日志保留。原指标SQL安全策略没有放宽。

## 验证与运行记录

命令和最终数量/日志哈希见 `docs/phase28/engineering-v1.json`。执行完整PostgreSQL、SQLite、Ruff、pip check、前端lint/typecheck/build、真实Edge Playwright、Compose HTTP冒烟、迁移升级两次→base回滚→重升→alembic check。所有云端opt-in用例继续按原条件关闭，普通CI不依赖密钥。

标准Docker Hub构建遭网络超时，原失败保留于忽略日志 `phase28-compose-build.log`。随后明确使用既有公开ECR Python/Node镜像，重新构建当前源码，独立项目 `datapilot-phase28` 的15638/18028/13028回环端口健康启动。`infra/compose.phase28.yml`清空个人env_file、强制Mock/Fake及追踪关闭。通过真实HTTP验证上传、SQL→DuckDB实际结果、知识CRUD/引用、导出与账户会话。Compose的假向量只能证明工程集成。

Playwright执行15项通过、3项原有真实云端专项关闭。初次包装命令在打印已完成日志时遇Windows GBK无法编码箭头字符；实际Playwright日志结尾明确15passed，保留包装错误，不能将其当成测试失败或再伪造运行。迁移首次缺少原入口强制`--disposable`被安全拒绝；按该参数重新在新临时库运行后通过，不降低保护。

实验新真实HTTP、聊天、Embedding、LangSmith、裁判均0；共享账本仍14次Embedding/1076输入Token、6次聊天/5331输入/833输出，usage完整，费用未知。阶段21历史未知不变。测试用临时资源在核实项目归属后清理，账本和旧证据保留。审计回执见 `docs/phase28/audit-v1.json`。

本阶段通过范围仅为关闭默认、受控接入、影子隔离和安全工程，不授权生产默认切换。阶段29仍须新人审独立题集、冻结后一次评分及真实Agent/SQL证据；D不适用，最终可能PARTIAL，不能用此工程通过替代质量验收。
