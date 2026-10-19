# SQL 安全与威胁边界

## Text-to-SQL 实际链路

1. 从 PostgreSQL 读取当前数据集的真实字段画像；只把字段名/类型和问题发给 Provider，原始行和预览不进入模型请求。
2. Provider 返回严格 JSON：`sql`、`explanation`、可选图表建议。错误、超时、缺 Key 直接失败，不会隐式降级 Mock。
3. `sql_guard.py` 使用 SQLGlot DuckDB 方言解析 AST，限制为单条 SELECT 和单个 `data` 表；逐节点/函数白名单、真实字段限定、复杂度限制；剥离注释并输出规范 SQL。
4. `execution.py` 在独立子进程打开当前数据集 DuckDB，`read_only=True`。关闭文件/网络外部访问、自动安装/加载扩展、社区扩展、Python replacement scan 和持久化凭据，锁定配置。子进程清理继承环境中的凭据变量。
5. 父进程强制截止时间并终止/回收超时进程；限制为 2 个并发执行槽、128 MB DuckDB 内存、1 线程、禁止磁盘临时溢写。默认返回 1000 行 / 1 MiB，行数截断明确标记，字节超限直接失败。
6. 真实结果生成图表配置和事实总结；结果快照写本地，PostgreSQL 保存历史元信息。安全拒绝记录原因与 SQL hash，不自动重试或修复。

## MVP 有意限制

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

子进程与 API 仍使用同一系统用户，没有完整操作系统沙箱。DuckDB 内存限制不是整个 Python 进程的硬内存限额，Windows 上也未实施内核资源配额。当前没有密码重置、MFA、组织/角色、共享数据集或公网容量验收；生产化还需要 HTTPS、网关限制、容器资源/权限约束、隔离工作进程和持续依赖升级。

实现参考：[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[CSRF 防护](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。

分析和上传增加 PostgreSQL 原子请求配额，限流存储不可用时拒绝执行。它覆盖已认证用户的重操作，不能代替所有入口的网关/IP 限流。每个响应生成新的请求 ID；业务 JSON 日志使用白名单，不包含 URL 参数、密码、Cookie、问题、SQL、数据行或异常正文。原始 Uvicorn 访问日志默认关闭，细节与日志保留见 [诊断说明](OBSERVABILITY.md)。

## 验证入口

`test_sql_guard.py` 是 AST 回归集；`test_execution.py` 验证真实执行、超时回收、行/字节限制与执行环境外部访问禁用；`test_providers.py` 使用 HTTP MockTransport 测试 API 合同；`test_analyses.py` 覆盖完整 API、失败审计和导出。`test_live_llm.py` 只有显式启用才访问真实模型。

`test_auth.py` 验证真实密码哈希、会话撤销/过期、登录节流、CSRF、所有业务路由的未登录拒绝、双用户隔离及旧数据显式分配；`e2e/auth.spec.ts` 用两个随机账户实际登录、查询、切换身份和检查导出权限。其余 API 测试在临时数据库写入真实会话行，不覆盖认证依赖。E2E 禁用网络 trace，避免登录请求中的密码进入测试附件。
