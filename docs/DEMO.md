# 五分钟求职演示与验收边界

## 启动与演示

按 README 启动原生服务，或按 OPERATIONS 运行 Docker Compose。保持 `LLM_PROVIDER=mock` 即可离线演示，无需申请 Key。工作台默认端口 3000。

1. 上传项目自带的合成数据 `data/examples/sales.csv`。核对 8 行、字段类型、缺失值和预览。
2. 问“这个数据集有多少行？”，SQL 的 COUNT 与实际结果应为 8。
3. 问“按 region 统计 revenue 总和”，检查 SQL、表格、柱状图：华东 67,000、华北 17,700、华南 11,000。各视图来自同一份 DuckDB 结果。
4. 下载 CSV，核对数值；刷新后通过分析历史恢复结果。用 `SUM` 搜索 SQL，再筛选“成功”。清空或重置筛选可恢复全部记录。
5. 问一个 Mock 不支持的问题，例如“预测下个月的销售额”。应显示明确失败，并能筛选到失败历史；不生成预测或成功图表。
6. 展示 SQL 安全回归测试中的恶意输入及“执行器从未被调用”的断言。Mock 不会生成危险 SQL，不能用聊天窗口里的拒绝回答代替 AST 安全验证证据。

## 面试中解释设计

- **为什么前后端分开？** Next.js 管理上传/提问/结果交互，FastAPI 复用 Python 数据生态，强制校验在后端；两者独立构建测试。
- **为什么两种数据库？** PostgreSQL 负责元信息和事务；DuckDB 处理上传数据的聚合。查询进程没有平台数据库连接，模型 SQL 只能访问当前 `data` 表。
- **为什么不能只用提示词？** 模型输出不可信。SQLGlot AST、真实 Schema 和函数白名单先检查；受限子进程再控制执行时间、内存和输出大小。
- **为什么先用 Mock？** 它让演示和安全测试可重复，且查询仍由真实 DuckDB 执行。真实模型的网络合同测试、凭据验收必须另外记录。
- **为什么历史搜索不调用模型？** 它是普通平台业务查询，SQLAlchemy 绑定关键词即可；搜索文本不能扩大数据访问范围。

## 已有证据与限制

[首轮 GitHub Actions](https://github.com/8xbAdx8/DataPilot/actions/runs/39051736631) 在提交 `da76f81` 验证了真实 PostgreSQL 后端 117 passed / 1 skipped（真实 LLM）、5 项 Chromium E2E、静态检查/构建，以及 Linux Compose 构建/健康/HTTP 冒烟。后续功能的验证见 DEVELOPMENT 与对应提交的 Actions，不能把旧提交的绿色结果套用到新代码。

尚无真实模型 API 验证；当前没有登录、鉴权和租户隔离，适合本机单用户求职演示。没有测量线上容量、并发吞吐或真实模型准确率，不应在简历中编造这些指标。Windows Docker 引擎问题仍未修复，Linux 容器验收已通过。

可据实描述项目：实现支持 CSV/XLSX 的自然语言分析平台，使用 SQL AST 校验和受限 DuckDB 执行真实查询，提供结果可视化、历史检索和 CSV 导出，并通过 PostgreSQL 集成测试及 Docker Compose CI 验证。
