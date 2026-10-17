# 分阶段实施与验收

每阶段执行：检查 Git → 实现 → 测试/构建 → 文档 → diff/密钥检查 → commit。只提交已验证的阶段；不自动 push、发布或使用收费服务。

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
