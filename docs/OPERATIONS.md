# 启动、保留与清理

## Docker Compose

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose config --quiet
docker compose up --build --wait --wait-timeout 240
.\.venv\Scripts\python infra/compose-smoke.py # 使用本地 Python 验证 HTTP 闭环
```

浏览器打开 <http://localhost:3000>。PostgreSQL 健康后，`migrate` 先执行 Alembic，成功后启动 FastAPI；API 就绪后启动前端。命名卷 `postgres_data` 保存业务数据，`analytics_data` 保存 DuckDB 和有界结果快照。前端在构建时将 `/api` 指向 `http://backend:8000`；本地开发默认指向 `http://127.0.0.1:8000`。

`compose-smoke.py` 要求 Mock 模式，检查同源代理、健康、真实上传、分组 SQL、DuckDB 结果，并删除自己创建的测试数据集。设置真实模型后不要用它代替真实 API 专项测试。

默认上传文件上限 10 MiB；Next 代理缓冲为 11 MiB，留出 multipart 封装空间。提高后端 `MAX_UPLOAD_BYTES` 时，也需检查前端代理与容器资源限制。前端 E2E 默认使用独立的 3001 端口；已启动开发服务时，可设置 `E2E_BASE_URL=http://127.0.0.1:3000` 复用它，避免两个 Next dev 进程争用同一构建目录。

```powershell
docker compose ps
docker compose logs --tail 100 backend
docker compose down # 停止，不删除命名卷
```

不要随意执行 `down -v`，它会删除数据库与分析数据。所有发布端口仅绑定本机；当前没有鉴权。数据库默认密码只用于本地演示。修改密码时，连接 URL 中的保留字符需 URL 编码。

**本次实际状态：** 配置解析通过。Docker Desktop 在 Windows 的 `dockerInference` 套接字初始化时退出，因此尚未验证镜像构建和容器启动。应用已用原生 PostgreSQL / Python / Node 完成验证。仓库没有自动推送，GitHub Actions 也尚未运行。

## 当前机器的原生 PostgreSQL

本次已在忽略目录中准备了 PostgreSQL 17.10。如重启后需要继续本地开发：

```powershell
& './data/tooling/embedded-pg/package/native/bin/pg_ctl.exe' -D "$PWD/data/pg-local" -l "$PWD/data/pg-local/server.log" -o '-h 127.0.0.1 -p 5432' -w start
```

启动后按根 README 运行 Alembic / FastAPI / Next.js。Docker 修复后如要改用容器，先停止占用相同端口的本地服务；仅对本项目的原生数据库运行：

```powershell
& './data/tooling/embedded-pg/package/native/bin/pg_ctl.exe' -D "$PWD/data/pg-local" -w stop
```

## 保留策略

| 对象 | 保留期 | 实际行为 |
| --- | --- | --- |
| 原始上传文件 | 导入期间 | 导入后关闭上传临时文件，不保留原件 |
| DuckDB 与数据集元信息 | 创建后 30 天 | 清理命令删除过期数据集及其历史/快照 |
| 结果快照 | 分析后 7 天 | 清理后历史仍可查看，导出返回 410 |
| 分析历史元信息 | 分析后 30 天 | 删除历史，避免 PostgreSQL 持续增长 |
| 审计记录 | 90 天 | 独立保留事件、ID、原因；不复制整份问题和结果 |
| 中断的 running 请求 | 10 分钟 | 清理时标记失败，不伪造结果 |
| 无元信息的 UUID 孤立文件 | 24 小时 | 只处理应用目录中的已知格式文件，不递归删除目录 |

```powershell
.\.venv\Scripts\python -m app.maintenance         # 只输出候选数量，不修改
.\.venv\Scripts\python -m app.maintenance --apply # 操作者明确执行清理
docker compose exec backend python -m app.maintenance # 容器内预览
```

保留期在运行清理命令时生效；MVP 不会在后台悄悄创建系统定时任务。运营部署应由操作者每日调用清理命令。删除单个数据集也可通过界面确认或 `DELETE /api/datasets/{id}`，同时清理其历史和快照；仍在执行分析的数据集返回 409。文件删除与业务事务使用可恢复重命名衔接，提交失败会恢复原文件；提交后若遇到 IO 失败，孤立文件由后续清理处理。

PostgreSQL 的画像中包含至多 20 行截断预览，因此也可能包含上传文件的敏感信息。删除数据集会删除这些元信息；不要把含隐私的数据用于公开演示。
