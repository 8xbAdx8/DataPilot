# 阶段17：受限 HAVING 与安全证据

起点为干净的 `f6aaea83a1fe640c7d89a67a18eb10660222d9fb`，功能分支 codex/agent-rag-upgrade。远端读取首次连接重置，恢复后实际 ls-remote 与本地起点相同。阶段13–16已推送，Actions 39598210685成功属于起点，不能充当本阶段CI。本阶段只本地提交。

## 先保护证据再实现

[基线清单](phase17-baseline.json)在生产代码修改前保存39个阶段11–16报告/题集/原始结果的Git对象、Git字节SHA和工作副本SHA，及原始安全配置。旧0/2与阶段16的7/8、12/13均保留。

阶段16 h03原问题按team合计hours、保留SUM(hours)>=3、total_hours降序/team升序。真实模型输出与预期语义一致；实际nodes止于validate、attempt=0 unsafe_sql、无execute、rows=null。原STRUCTURAL_NODES没有having，for-loop在Having节点直接拒绝，未进入qualify；不是Schema未知列、工具耗尽或DuckDB超时。原SQL/计划/节点/断言均在清单中完整摘录。没有再次运行该模型留出。

本机Python3.12.14、Node24.15.0、SQLGlot30.23.0；原生PG17.10/pgvector0.8.7、迁移0006。初查原生PG与Docker未启动，首次PG连接超时；仅按OPERATIONS启动已有data/pg-local，未初始化/重置，随后ready。Docker Desktop后台启动后引擎29.6.2可读。聊天、Embedding、LangSmith凭据均存在；LangSmith默认false。未输出Key、连接串或私有地址。

## 支持范围

`sql_guard.py`只增加Having结构节点，再调用新的`having_guard.py`。原单SELECT/单data表、Schema资格化、函数白名单、512 AST节点、SQL长度和外部访问限制继续生效；别名展开后再次限制512节点。

- 必须有显式非空GROUP BY；不支持无分组HAVING或聚合GROUP BY。
- 支持COUNT/SUM/AVG/MIN/MAX及单参数DISTINCT。COUNT(*)有效，COUNT多参数和嵌套聚合拒绝。
- SUM/AVG入参为Schema数值字段、数值/NULL和简单算术；其它聚合可用字段/字面量/NULL。不在该子集中放行CASE/CAST嵌套参数。
- HAVING比较支持=、!=、>、>=、<、<=、IS NULL/IS NOT NULL，AND/OR/NOT/括号及聚合间的简单算术。字符串可比较MIN/MAX或分组键，保留NULL三值语义。
- HAVING中须有允许的聚合；非聚合列只能是实际GROUP BY表达式。SELECT/ORDER BY也核对分组绑定，避免非分组字段混入。
- 至多8个比较、96个HAVING节点、12层条件/标量递归；没有提高原512节点上限。
- 先检查重复/冲突别名，再由SQLGlot AST qualify展开合法别名。聚合别名与真实字段同名且用于HAVING时保守拒绝；不作字符串替换。
- HAVING中的IN/BETWEEN、ROUND等额外标量包装暂不支持；JOIN、子查询、窗口、CTE、UNION、表函数、文件函数、DDL/DML仍拒绝。

数值检查防止SUM(VARCHAR)，但不声称静态证明所有DuckDB类型运算。运行期绑定/计算错误仍显式失败。DuckDB仍为只读隔离子进程，含启动5秒、双槽、128MB、单线程、行/字节上限不改。未增加模型工具、修复次数或预算。

## 独立确定性验证

新test_having.py使用独立七行A/B/C/D合成记录：A金额10/20，B为NULL/5，C为NULL/NULL，D为-2；手工可核算的结果常量在测试中，不依赖模型。

|检查|证据|
|---|---|
|真实分组/聚合/NULL/排序|12个DuckDB结果契约案例；COUNT(*)>1得到A/B/C各2，SUM>10得到A=30；SUM(amount*units)>=20得到A=50；全NULL组C被COUNT(amount)=0与IS NULL正确选中|
|拒绝语法/语义|22个AST拒绝案例，包括未知列、错误类型、嵌套聚合、别名歧义、未分组列、子查询、多语句、外部函数及9条件超界|
|API/安全|真实Graph/guard/DuckDB成功案例；两种危险/错误HAVING在执行器入口的fail断言前被拒绝；另一用户GET/分析均404，模型与执行器零调用|
|原回归先运行|修改前SQL/执行器/Graph/评测器86 passed，11.33秒|
|修改后SQLite定向|124 passed，26.30秒|
|修改后PostgreSQL定向|同一124 passed，24.04秒|
|静态检查|backend Ruff check/format通过，78个Python文件；初次新增SQL字符串排版的lint错误已修正，未关闭规则|

旧test_sql_evaluate.py的h03历史拒绝断言保留，显式固定到阶段16的无having节点白名单上下文；其它参考SQL/行值/断言不改。新38项测试使用当前策略与独立数据，不能把旧断言改成成功或将新测试分数回填旧7/8。现有超时、文件系统防御和拒绝后零执行测试继续通过。

本阶段未调用真实模型/Embedding/云追踪，Token消耗0；这是确定性能力与安全验收。真实自然语言HAVING链路另归阶段19，不由受控Proposal冒充真实模型。

## 命令、改动与后续

```powershell
.venv/Scripts/python -m pytest backend/tests/test_sql_guard.py backend/tests/test_execution.py backend/tests/test_analysis_graph.py backend/tests/test_sql_evaluate.py -q
.venv/Scripts/python -m pytest backend/tests/test_having.py backend/tests/test_sql_guard.py backend/tests/test_execution.py backend/tests/test_analysis_graph.py backend/tests/test_sql_evaluate.py -q
# 第二条同样在显式TEST_DATABASE_URL/TEST_POSTGRES_URL指向独立_test库后运行
.venv/Scripts/python -m ruff check backend
.venv/Scripts/python -m ruff format --check backend
```

生产仅sql_guard.py与新增having_guard.py；新增test_having.py、历史边界测试的显式上下文，以及本报告/基线清单/SQL子集文档。没有依赖或迁移变更。完整回归与发布准备留到阶段19；阶段18先建立新人工复核RAG版本，旧题集只读。阶段17提交SHA在最终交付和Git log给出，提交前核对冻结历史、密钥与diff，不推送。
