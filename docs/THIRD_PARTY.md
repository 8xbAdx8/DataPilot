# 第三方代码与工具

- `frontend/src/components/ui/button.tsx`、`card.tsx` 来自 shadcn/ui 官方 `new-york` registry（MIT），保留项目 [许可](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md)。其余业务界面为本项目实现。
- 本机 Docker 启动受阻时，为验证使用了 `@embedded-postgres/windows-x64@17.10.0-beta.17`（[源码与打包工具](https://github.com/leinelissen/embedded-postgres)）分发的 PostgreSQL 17.10 原生进程。下载包和数据库位于被 Git 忽略的 `data/tooling/`、`data/pg-local/`。不是 PostgreSQL 的替代模拟器，不属于应用运行时依赖。
- 查询沙箱设置参考 [DuckDB 安全指南](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)；AST 语义验证参考 [SQLGlot qualify](https://sqlglot.com/sqlglot/optimizer/qualify.html)。
