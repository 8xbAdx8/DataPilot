# 阶段16标签修订 v2 人工复核

用户已确认“确认 h04 日期契约修订”，有效确认绑定在 sql-correctness-v2.review.json。下文保留提交复核时的修订理由。

v1 JSON 与 review receipt 保留不变。尚未运行任何阶段16真实模型评测。

唯一修订 h04：导入器真实 Schema 为 work_date TIMESTAMP，v1参考SELECT work_date返回带T00:00:00的值，但原标签为YYYY-MM-DD。这是参考SQL/序列化契约不一致，不是模型错误。v2保留原期望日期值和排序，明确要求DATE显示：

> 取work_date最新的2条记录，仅输出entry_id、work_date；work_date转换为DATE并用YYYY-MM-DD显示，按work_date降序，平局entry_id升序。

```sql
SELECT entry_id,CAST(work_date AS DATE) AS work_date FROM data ORDER BY work_date DESC,entry_id ASC LIMIT 2
```

列顺序entry_id、work_date；行依次E7/2026-07-04、E6/2026-07-03。其余29题、知识、意图分组、数据不变，沿用已确认内容。h03的HAVING仍保留，作为当前MVP明确不支持的合法问题，不能扩展安全白名单来提高得分。

请确认这一处修订。完整新版本见 [v2 JSON](../backend/app/evals/sql-correctness-v2.json)，旧版见 [v1](../backend/app/evals/sql-correctness-v1.json)。
