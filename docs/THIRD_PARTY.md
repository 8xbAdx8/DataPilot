# 第三方代码与工具

- LangGraph、LangChain、langchain-openai、LangSmith SDK 使用各自 PyPI 官方发行版，具体版本锁于 `backend/requirements.lock`，许可证以安装包元数据为准；没有复制其实现源码。
- pgvector 0.8.7 来自 [官方源码](https://github.com/pgvector/pgvector)，固定 commit `f37c13f68b57d2c3472b2214fbcff699d6d34876`。Docker 多阶段构建保留其 LICENSE；本机以 VS2022 和官方 EDB PG17.10 头文件/导入库编译，仅安装在忽略的工具目录。
- Docker Hub 无法访问时，本机尝试 Docker 官方在 ECR Public 的同名镜像（[AWS 官方说明](https://aws.amazon.com/blogs/containers/docker-official-images-now-available-on-amazon-elastic-container-registry-public/)），不切换到来源不明的第三方镜像。默认 Dockerfile 仍使用 Docker Official Images 原标签。

- `frontend/src/components/ui/button.tsx`、`card.tsx` 来自 shadcn/ui 官方 `new-york` registry（MIT），保留项目 [许可](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md)。其余业务界面为本项目实现。
- 本机 Docker 启动受阻时，为验证使用了 `@embedded-postgres/windows-x64@17.10.0-beta.17`（[源码与打包工具](https://github.com/leinelissen/embedded-postgres)）分发的 PostgreSQL 17.10 原生进程。下载包和数据库位于被 Git 忽略的 `data/tooling/`、`data/pg-local/`。不是 PostgreSQL 的替代模拟器，不属于应用运行时依赖。
- 查询沙箱设置参考 [DuckDB 安全指南](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)；AST 语义验证参考 [SQLGlot qualify](https://sqlglot.com/sqlglot/optimizer/qualify.html)。
