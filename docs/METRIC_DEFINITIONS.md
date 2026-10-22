# 登记指标与确定性解析（阶段21）

业务知识管理中，知识类型选择 `metric_definition`，标题必须与JSON的name相同，内容可使用下面的版本1契约。沿用既有KnowledgeDocument.content，不新增数据表、迁移或依赖。

```json
{
  "version": 1,
  "name": "已结服务净额",
  "aliases": [
    "服务实收",
    "service_net",
    "扣除人工成本后的已结服务收入"
  ],
  "expression": "SUM(fee)-SUM(labor)",
  "filters": [
    {
      "column": "state",
      "operator": "eq",
      "value": "closed"
    }
  ],
  "required_fields": [
    "fee",
    "labor",
    "state"
  ]
}
```

- name是正式名称；aliases只包含人工明确登记的别名或自然语言表达，不由模型自动扩充。NFKC、大小写及空白规范化不会做模糊语义猜测。多个同名/同别名候选一律澄清，包括同一用户的个人与数据集定义冲突。
- expression是一条有界聚合表达式，不包含FROM、WHERE、GROUP、ORDER、LIMIT、输出别名或多语句。SQLGlot和原Schema/函数/单表规则先验证它。SUM/AVG要求数值字段。
- filters最多8条，操作为eq/ne/gt/gte/lt/lte，value使用JSON标量；eq/ne的NULL被构造成IS NULL/IS NOT NULL。由AST构造字面量，值不是SQL字符串片段。
- required_fields必须覆盖表达式和过滤字段，并全部存在于当前数据集；最多100个字段。名称/别名、公式、过滤各有长度或数量限制，完整结构化正文最多4000字符，授权目录最多100条。未知JSON键不忽略。

解析过程在 `metric_resolution.py`：先核对当前数据集所有权，再用SQL按owner、dataset/个人范围、ready和Embedding空间筛选文档/索引；只读取合法范围内的指标和字段别名。没有全库搜索后再过滤。正式名称或别名匹配后，分别返回resolved、ambiguous、not_found、inapplicable或insufficient_evidence。

普通Schema计算与业务指标有区别。已有指标目录时，不匹配已登记名称、也未明确使用数值字段/字段别名或行数等原生操作的请求，会澄清。仅出现在“输出…”或AS后的列别名不构成指标授权。没有目录的普通查询沿用原Agent/SQL流程；无原生字段依据却声明指标的计划仍须澄清。这不是任意自然语言意图识别器，陌生改写需要先登记，不能宣称开放语言泛化能力已通过。

`analyses.configure_evidence` 将唯一、适用的登记来源优先放入最多4条规划上下文。它保留引用的真实document/chunk和原文excerpt，初始向量no_match并不会被改成向量命中。Agent必须明确选取这个真实chunk；忽略来源、定义缺失、歧义或字段不匹配时，Graph在plan后结束，不能进入SQL节点。模型/工具/Token预算未增加。

生成SQL之后，原AST和Schema校验先执行，再由 `AnalysisRun.evidence_guard` 验证登记聚合表达式出现在SELECT，必需过滤是WHERE的实际AND条件。OR子树中藏入过滤、丢失过滤或把SUM(fee)-SUM(labor)改成SUM(fee-labor)都会拒绝。后者在存在NULL时可能产生不同结果。本实现不证明任意代数等价，等价但不同AST的写法也可能保守拒绝；维度、别名、排序和额外用户过滤可由SQL正常选择。业务契约失败不自动修复，执行器零调用。

旧文本注册仅兼容两类明确格式：标题后写出单个等值条件及显式SQL聚合表达式，或单字段“总和/平均值/非空计数”聚合。不会从“按惯例计算”推导公式，也不会猜缺失过滤。更复杂自然语言定义应迁移为JSON契约。旧精确读取仍可返回不充分文档，但resolution_status明确标为insufficient_evidence，不能用该分类绕过分析API证据门槛。

`PlannerContext.record_tool` 的生产诊断只有工具名、输入类别/长度、结果分类和字段/引用数量；不保存原始工具参数、知识或Schema。独立合成验收入口才能通过本地observer捕获真实参数与返回值，并先核对临时owner/dataset；没有Web或环境开关可以导出用户工具正文。LangSmith继续默认关闭及白名单过滤。

本阶段未解决广义误召回或统一前端来源状态，见[本阶段报告](PHASE21_METRIC_DISCOVERY.md)。所有定义仍需业务方审阅，语法/Schema正确不证明业务定义本身正确。
