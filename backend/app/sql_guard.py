import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from app.errors import AppError

# Deliberately small MVP grammar: one SELECT over the uploaded table, no joins/CTEs/subqueries.
STRUCTURAL_NODES = {
    "select",
    "from",
    "table",
    "tablealias",
    "identifier",
    "column",
    "star",
    "alias",
    "where",
    "group",
    "order",
    "ordered",
    "limit",
    "offset",
    "literal",
    "boolean",
    "null",
    "paren",
    "and",
    "or",
    "not",
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "between",
    "is",
    "add",
    "sub",
    "mul",
    "div",
    "mod",
    "neg",
    "distinct",
    "datatype",
    "datatypeparam",
    "var",
    "like",
    "ilike",
    "escape",
}
SAFE_FUNCTIONS = {
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "ROUND",
    "ABS",
    "COALESCE",
    "NULLIF",
    "CAST",
    "TRY_CAST",
    "LOWER",
    "UPPER",
    "LENGTH",
    "TRIM",
    "EXTRACT",
    "DATE_TRUNC",
    "TIMESTAMP_TRUNC",
    "TIME_TO_STR",
    "CASE",
    "IF",
}


def unsafe(message: str):
    return AppError(422, "unsafe_sql", message)


def validate_sql(sql: str, fields: list[dict]) -> str:
    if not sql.strip() or len(sql) > 8192:
        raise unsafe("SQL 为空或超过长度限制。")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except (ParseError, RecursionError) as exc:
        raise unsafe("SQL 语法无效。") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise unsafe("只允许单条只读 SELECT 查询。")
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > 512:
        raise unsafe("SQL 复杂度超过限制。")
    if any(tree.args.get(key) for key in ("into", "locks", "with_", "joins")):
        raise unsafe("MVP 不允许 INTO、锁、CTE、子查询或 JOIN。")
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1:
        raise unsafe("只能查询当前数据集的 data 表。")
    table = tables[0]
    if (
        not isinstance(table.this, exp.Identifier)
        or table.name.casefold() != "data"
        or table.db
        or table.catalog
    ):
        raise unsafe("表或数据源未经授权。")
    for node in nodes:
        node.pop_comments()  # Emit canonical SQL without retaining untrusted SQL comments.
        if isinstance(node, exp.Star) and any(node.args.values()):
            raise unsafe("MVP 不支持星号的 EXCLUDE、REPLACE 或 RENAME 修饰。")
        if isinstance(node, exp.Func):
            # Anonymous/user-defined functions are never admitted, even if their text looks safe.
            if isinstance(node, exp.Anonymous) or node.sql_name() not in SAFE_FUNCTIONS:
                raise unsafe("SQL 使用了未经授权的函数。")
        elif node.key not in STRUCTURAL_NODES:
            raise unsafe("SQL 使用了当前不支持的语法。")
        if isinstance(node, exp.Select) and node is not tree:
            raise unsafe("MVP 暂不支持子查询。")
    try:
        qualified = qualify(
            tree,
            dialect="duckdb",
            schema={"data": {f["name"]: f["type"] for f in fields}},
            validate_qualify_columns=True,
            quote_identifiers=True,
        )
    except (OptimizeError, ValueError, TypeError, RecursionError) as exc:
        error = unsafe("SQL 引用了未知或有歧义的字段。")
        # Only schema binding after every structural/authorization check is recoverable.
        # Parse failures cannot reliably establish the intended grammar, so stay terminal.
        error.repairable = isinstance(exc, OptimizeError)
        raise error from exc
    # Bind-time validation is backed by real DuckDB; schema scope cannot be expanded by the model.
    return qualified.sql(dialect="duckdb")
