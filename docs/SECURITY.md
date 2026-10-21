# SQL 安全与威胁边界

阶段 14 的词法路径复用同一个数据库授权 CTE，与向量路径使用相同语义门槛，禁止先全局召回再在应用层过滤。重排只接收授权候选，模型须显式预下载，请求内不能下载；独立子进程有超时与候选上限，输出 ID 必须完全属于原候选且不重复。默认 none 不引入 ONNX 启动依赖。采用 RAG 不改变任何 SQL AST/Schema 规则或 DuckDB 权限。相关故障、权限及原安全回归均保留。

## Text-to-SQL 实际链路

1. 从 PostgreSQL 读取当前数据集的真实字段画像；只把字段名/类型和问题发给 Provider，原始行和预览不进入模型请求。
2. Provider 返回严格 JSON：`sql`、`explanation`、可选图表建议。错误、超时、缺 Key 直接失败，不会隐式降级 Mock。
3. `sql_guard.py` 使用 SQLGlot DuckDB 方言解析 AST，限制为单条 SELECT 和单个 `data` 表；逐节点/函数白名单、真实字段限定、复杂度限制；剥离注释并输出规范 SQL。
4. `execution.py` 在独立子进程打开当前数据集 DuckDB，`read_only=True`。关闭文件/网络外部访问、自动安装/加载扩展、社区扩展、Python replacement scan 和持久化凭据，锁定配置。子进程清理继承环境中的凭据变量。
5. 父进程强制截止时间并终止/回收超时进程；限制为 2 个并发执行槽、128 MB DuckDB 内存、1 线程、禁止磁盘临时溢写。默认返回 1000 行 / 1 MiB，行数截断明确标记，字节超限直接失败。
6. 真实结果生成图表配置和事实总结；结果快照写本地，PostgreSQL 保存历史元信息。危险操作、不支持语法、权限或执行资源错误不重试。仅当全部结构/数据源检查通过后发生字段绑定错误，且 Provider 显式实现 repair 时，LangGraph 可修复一次，再次经过完整 AST 校验；没有修复能力则保持原拒绝。解析失败无法可靠确认意图，保守拒绝，不自动修复。校验尝试记录 SQL hash 与状态，不在业务日志写原文。

## MVP 有意限制

阶段17对显式GROUP BY新增受限HAVING：AST别名资格化、分组绑定、五类聚合参数与类型检查，至多8比较/96局部节点；歧义别名、非分组字段、无分组HAVING、嵌套聚合和额外语法拒绝。没有直接放行任意Having子树。精确子集与独立安全证据见[阶段17](PHASE17_HAVING.md)。原512节点、5秒、只读和所有权限制不变。

- 暂不支持 JOIN、CTE、UNION、子查询、窗口函数、表函数、星号 EXCLUDE/REPLACE/RENAME 和任意自定义函数。复杂需求显式返回错误。
- 允许常用单表筛选、分组、排序、COUNT/SUM/AVG/MIN/MAX、有限的日期/文本/空值函数。AST 限定后仍可能发生类型错误，由 DuckDB 返回明确失败。
- 字段名、问题、单元格文本和模型输出都是不可信内容。提示词不是授权规则；即便模型输出 DROP 或文件读取，后端也会拒绝。
- 平台业务数据库连接只由 SQLAlchemy/Alembic 使用。模型 SQL 从不送到 PostgreSQL。
- `SELECT 'DROP' FROM data` 合法，`DROP TABLE data` 非法：安全性依赖语法含义，不靠字符串匹配。
- CSV 导出与快照使用相同结果行；可能触发电子表格公式的文本加单引号转义，真实数值保留数值表示。

## 当前部署边界

当前提供管理员创建账户的基础多账户演示：scrypt 加盐密码（N=2^17、r=8、p=1）、256 bit 随机不透明会话、数据库仅存 SHA-256 令牌摘要、HttpOnly / SameSite=Strict Cookie、过期与退出撤销。密码不进入 URL、日志或前端存储，验证错误也会剔除 Pydantic 原始 input。HTTPS 部署启用 Secure；配置与账户创建见 OPERATIONS。

数据集元信息、分析创建、历史搜索、分析详情、删除和 CSV 导出均强制匹配 owner。外部用户的 UUID 与不存在的 UUID 统一 404；owner 为空的旧数据仅能由管理员通过 CLI 显式分配。分析通过数据集继承归属，不接受前端传入 owner。

写请求要求自定义 CSRF 头并检查 Origin / Fetch Metadata；SameSite 仅作纵深防御。每账户失败窗口持久化，15 分钟内 5 次错误后返回 429；不存在的账户使用相同错误和 scrypt 工作量。密码计算同时最多 2 个，繁忙返回 503。当前节流按账户而非全网/IP，仍需网关层速率与请求大小控制，不能把它当作完整的公网防滥用系统。

子进程与 API 仍使用同一系统用户，没有完整操作系统沙箱。DuckDB 内存限制不是整个 Python 进程的硬内存限额，Windows 上也未实施内核资源配额。当前没有自助找回密码、MFA、组织/角色、共享数据集或公网容量验收；生产化还需要 HTTPS、网关限制、容器资源/权限约束、隔离工作进程和持续依赖升级。

实现参考：[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[CSRF 防护](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。

分析和上传增加 PostgreSQL 原子请求配额，限流存储不可用时拒绝执行。它覆盖已认证用户的重操作，不能代替所有入口的网关/IP 限流。每个响应生成新的请求 ID；业务 JSON 日志使用白名单，不包含 URL 参数、密码、Cookie、问题、SQL、数据行或异常正文。原始 Uvicorn 访问日志默认关闭，细节与日志保留见 [诊断说明](OBSERVABILITY.md)。

## 验证入口

`test_sql_guard.py` 是 AST 回归集；`test_execution.py` 验证真实执行、超时回收、行/字节限制与执行环境外部访问禁用；`test_providers.py` 使用 HTTP MockTransport 测试 API 合同；`test_analyses.py` 覆盖完整 API、失败审计和导出。`test_live_llm.py` 只有显式启用才访问真实模型。

`test_auth.py` 验证真实密码哈希、会话撤销/过期、登录节流、CSRF、所有业务路由的未登录拒绝、双用户隔离及旧数据显式分配；`e2e/auth.spec.ts` 用两个随机账户实际登录、查询、切换身份和检查导出权限。其余 API 测试在临时数据库写入真实会话行，不覆盖认证依赖。E2E 禁用网络 trace，避免登录请求中的密码进入测试附件。

## 密码生命周期与并发边界

改密、撤销单个/全部会话必须再次验证当前密码，错误与登录共享持久化失败窗口。改密与管理员 CLI 重置在同一事务中更新哈希、撤销全部目标会话并写审计。未知/其他用户的会话 ID 均返回相同 404；会话清单只返回独立 UUID，不暴露 token hash。管理员重置只能在后端运行环境执行，没有公开恢复接口。

`auth.lock_attempt` 使用 savepoint 处理首次锁行的并发创建，不提前提交业务状态；所有凭据操作统一按失败窗口 → User → AuthSession 取锁，保留清理也先处理失败窗口。`populate_existing=True` 刷新 SQLAlchemy identity map，锁后再次查询当前会话及过期时间，避免重置后仍用缓存的旧密码或旧会话授权。登录只轮换本次登录用户的原会话，避免无序跨账户锁。撤销不取消已经接纳的数据请求。

`test_account_security.py` 覆盖密码/会话 API、边界、CSRF、脱敏和提交故障回滚；`test_auth_concurrency.py` 用真实 PostgreSQL 独立连接与行锁队列，验证登录/重置两种顺序、缓存刷新及并发改密。SQLite 无法证明这些锁保证。`e2e/account-security.spec.ts` 验证双浏览器会话、改密、旧密码拒绝、逐条/全部/当前撤销；除禁用 trace，还通过 Playwright 的 `PLAYWRIGHT_NO_COPY_PROMPT` 关闭会记录密码输入值的自动失败 ARIA 附件。

## 云端追踪与公开仓库审计

LangSmith 应用开关默认关闭；启用时也只发送新构造的白名单元数据，SDK 再隐藏 inputs/outputs 并剔除运行环境。没有上传原始行、知识正文、Schema、问题、SQL、凭据或原始来源 ID。阶段 15 已实际回读成功与受控拒绝记录，检查每个节点的空正文和元数据；受控 DROP 提案被 SQLGlot 拒绝且零执行，不代表真实模型生成过危险 SQL。详见[配置和隐私](LANGSMITH_PRIVACY.md)。

`infra/audit-git-history.py` 检查全部可达历史对象中的当前配置 Key、凭据特征及禁止路径，只保存位置，不输出命中的值；模式候选仍需人工复核。输出独占创建以保留历史证据。未命中不能证明所有未知格式的秘密都不存在；若发现真实凭据曾入历史，应先轮换并报告相关提交，再由用户决定历史清理，不能只删当前文件。

阶段 16 的轻量查询 worker 使用当前解释器 `-I` 和固定服务端脚本，无 shell；忽略 PYTHONPATH/用户 site 路径，父进程先移除凭据环境，worker 再清理一次且不读取 .env。私有请求最多 64KiB，SQL/行数/字节参数校验后才执行；stdout 只返回受限 JSON，stderr 不外泄。调用入口仍必须先经 SQLGlot。没有扩大 SQL AST 白名单：HAVING 仍拒绝，新留出题 h03 的能力失败不能通过放宽策略消除。原行数、字节、并发、内存和超时规则保留。


阶段21的登记指标仍是不可信数据：严格Pydantic结构、Schema字段和原SQLGlot先验证定义，过滤通过AST字面量构造。原AST安全规则通过后才运行附加业务证据门槛；公式/过滤违约不得执行或自动修复，未知/歧义/不适用定义澄清。别名不是权限，目录查询先owner/dataset/ready/space过滤。此门槛证明的是有限AST契约一致性，不证明业务定义真值或任意代数等价，普通无目录查询仍沿用既有Agent/SQL边界。生产工具诊断不存原始参数/正文，LangSmith规则和默认关闭不变。
