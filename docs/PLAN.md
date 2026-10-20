# 分阶段实施与验收

每阶段执行：检查 Git → 实现 → 测试/构建 → 文档 → diff/密钥检查 → commit。只提交已验证的阶段；未经用户明确授权不 push、发布或使用收费服务。

1. **骨架**：Python 3.12/FastAPI、Next.js App Router、配置、PostgreSQL、Alembic、存活与就绪检查。
2. **真实数据切片**：CSV/XLSX 格式与资源限制、DuckDB 导入、元信息持久化、字段画像与前端上传预览；测试无效输入与完整流程。
3. **安全 Text-to-SQL**：Provider 接口、deterministic Mock、兼容 API、严格 JSON、AST/Schema 校验、隔离执行与超时/结果限制、审计。
4. **分析体验**：真实结果表格与 ECharts、事实/推测/局限性、历史、CSV 导出、删除/保留策略。
5. **工程验收**：安全回归、前端关键交互、Docker Compose 全栈、CI、求职演示和架构文档。

## 验证必须分别记录

- 单元/API 自动化：包括 Mock 和真实 DuckDB 执行。
- PostgreSQL：真实连接、迁移升级/回退、业务数据持久化。
- 真实 LLM API：需要用户提供环境变量；无凭据时记为未执行。
- Docker 部署：只有实际构建、启动和健康检查成功才记为通过。

## 架构边界

- Next.js 与 FastAPI 分离：TypeScript UI 与 Python 数据生态独立构建、测试；服务端统一强制权限与资源约束。
- PostgreSQL 保存 dataset、history、audit 的元信息；不存整份上传数据或大结果。
- 每个数据集使用独立 DuckDB 文件，查询进程只打开该文件；LLM 从不接触平台数据库连接。
- 上传内容和模型 SQL 都是不可信输入。提示词用于指导格式，AST 和执行环境决定可执行权限。

## 本轮验收状态

| 阶段 | 状态 |
| --- | --- |
| 项目骨架、健康、真实 PostgreSQL 与迁移 | 通过 |
| CSV/XLSX、DuckDB、画像与前端 | 通过 |
| Mock Text-to-SQL、AST、防护执行与失败审计 | 通过 |
| 真实结果、ECharts、事实总结、历史与导出 | 通过 |
| 删除、保留清理、原生环境端到端 | 通过 |
| Docker 镜像与容器运行 | GitHub Actions Linux 实际构建/健康/真实查询冒烟通过；Windows 本机引擎问题仍在 |
| 真实 LLM API | 2026-10-20，DeepSeek deepseek-flash 单个真实 API / AST / DuckDB 行数用例通过；不代表模型准确率评测 |
| GitHub Actions | 首轮 application / compose 均通过，见运行 39051736631 |

## 已验收增强切片：历史检索

历史搜索、执行状态组合筛选、每页 20 条分页和错误重试。沿用单用户范围；后端对 PostgreSQL 元信息执行绑定参数查询，搜索不调用模型，也不执行用户输入的 SQL。验收覆盖字面通配符、同时间排序、跨数据集筛选、分页边界和真实浏览器交互。后端 PostgreSQL 135 passed / 1 skipped，前端 7 项 E2E 通过；真实模型验收继续单独保留。

## 已验收增强切片：账户与所有权

管理员 CLI 创建账户 → 浏览器登录 → HttpOnly 会话 → 上传归属 → 全部业务路由 owner 约束 → 退出撤销。包括 scrypt、失败窗口、CSRF、会话清理、旧数据安全迁移和双用户隔离测试；不开放注册，也不自动认领旧数据。具体运行与验收证据见 DEVELOPMENT，真实 LLM 仍单独验证。

## 已验收增强切片：配额与可观测性

PostgreSQL 原子配额（上传/分析）→ 429 与等待提示 → 服务端请求 ID → 脱敏 JSON 日志 → 分段耗时与可追溯 Token 用量 → 前端执行详情。验证并发配额、敏感信息不进入日志、失败阶段和旧历史兼容；不引入外部监控服务或估算账单。具体边界见 OBSERVABILITY，验证结果见 DEVELOPMENT。

## 已验收增强切片：密码生命周期与会话管理

账户安全面板 → 当前密码再确认 → 改密并全撤销 → 会话清单与单条/全部撤销 → 管理员重置 CLI → 脱敏审计。0005 保留旧 Cookie 并回填公开会话 ID，不编造旧创建时间；统一 PostgreSQL 锁序、锁后刷新身份和会话，验证旧密码登录与重置竞态。逐项 API、真实 PG 并发、浏览器与容器冒烟分开记录；真实 LLM 继续单独验收。

## 已验收切片：真实模型基础接入

DeepSeek `deepseek-flash` 显式非思考模式，使用用户本地环境配置，真实调用专项 1 passed；从合成三行数据的问题生成 SQL，经过现有 AST 与 DuckDB 验证结果。后端重启后直连及前端代理均显示真实 Provider/模型，数据库就绪。未将 Key 上传 GitHub，也未把单次通过扩写为复杂 SQL、图表或浏览器端到端的真实模型验收。后续可扩展合成问题集，再推进有限 SQL 修复。
