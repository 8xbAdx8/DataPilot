"""Additional fail-closed semantics for the explicitly grouped HAVING subset."""

from sqlglot import exp

from app.errors import AppError

AGGREGATES = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)
COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def reject():
    raise AppError(422, "unsafe_sql", "HAVING 仅支持明确分组、已授权聚合和有界比较条件。")


def check_aliases(tree, fields):
    if tree.args.get("having") is None:
        return
    group = tree.args.get("group")
    if not group or not group.expressions:
        reject()
    names = {f["name"].casefold() for f in fields}
    aliases = {}
    for projection in tree.expressions:
        if isinstance(projection, exp.Alias):
            name = projection.alias.casefold()
            if name in aliases:
                reject()
            aliases[name] = projection.this
    for column in tree.args["having"].find_all(exp.Column):
        name = column.name.casefold()
        if not column.table and name in names and name in aliases:
            body = aliases[name]
            # A real field and a computed alias with the same name have dialect-
            # dependent precedence. Reject instead of guessing or replacing text.
            if not isinstance(body, exp.Column) or body.name.casefold() != name:
                reject()


def validate_having(tree, fields):
    having = tree.args.get("having")
    if having is None:
        return
    nodes = list(having.walk())
    if len(nodes) > 96 or not any(isinstance(n, AGGREGATES) for n in nodes):
        reject()
    group = tree.args.get("group")
    if not group or not group.expressions:
        reject()
    if any(isinstance(n, exp.AggFunc) for g in group.expressions for n in g.walk()):
        reject()
    grouped = {g.sql(dialect="duckdb") for g in group.expressions}
    types = {f["name"].casefold(): f["type"].upper() for f in fields}
    aliases = {p.alias.casefold(): p.this for p in tree.expressions if isinstance(p, exp.Alias)}

    def numeric(node):
        if isinstance(node, exp.Column):
            return types.get(node.name.casefold(), "").startswith(
                (
                    "INT",
                    "BIGINT",
                    "HUGEINT",
                    "SMALLINT",
                    "TINYINT",
                    "UINTEGER",
                    "UBIGINT",
                    "UHUGEINT",
                    "USMALLINT",
                    "UTINYINT",
                    "DOUBLE",
                    "FLOAT",
                    "REAL",
                    "DECIMAL",
                    "NUMERIC",
                )
            )
        if isinstance(node, exp.Null):
            return True
        if isinstance(node, exp.Literal):
            return not node.is_string
        if isinstance(node, (exp.Paren, exp.Neg)):
            return numeric(node.this)
        return isinstance(node, ARITHMETIC) and numeric(node.this) and numeric(node.expression)

    def aggregate(node):
        if not isinstance(node, AGGREGATES) or node.expressions:
            reject()
        argument = node.this
        if argument is None or any(isinstance(n, exp.AggFunc) for n in argument.walk()):
            reject()
        distinct = isinstance(argument, exp.Distinct)
        if distinct:
            if len(argument.expressions) != 1:
                reject()
            argument = argument.expressions[0]
        if isinstance(argument, exp.Star):
            if not isinstance(node, exp.Count) or distinct:
                reject()
        elif isinstance(node, (exp.Sum, exp.Avg)):
            if not numeric(argument):
                reject()
        elif not isinstance(argument, (exp.Column, exp.Literal, exp.Null)) and not numeric(
            argument
        ):
            reject()

    def scalar(node, depth=0):
        if depth > 12:
            reject()
        if isinstance(node, exp.AggFunc):
            aggregate(node)
        elif isinstance(node, (exp.Literal, exp.Null, exp.Boolean)):
            return
        elif node.sql(dialect="duckdb") in grouped:
            return
        elif isinstance(node, (exp.Paren, exp.Neg)):
            scalar(node.this, depth + 1)
        elif isinstance(node, ARITHMETIC):
            scalar(node.this, depth + 1)
            scalar(node.expression, depth + 1)
        else:
            reject()

    predicates = 0

    def predicate(node, depth=0):
        nonlocal predicates
        if depth > 12:
            reject()
        if isinstance(node, (exp.Paren, exp.Not)):
            predicate(node.this, depth + 1)
        elif isinstance(node, (exp.And, exp.Or)):
            predicate(node.this, depth + 1)
            predicate(node.expression, depth + 1)
        elif isinstance(node, COMPARISONS + (exp.Is,)):
            predicates += 1
            if predicates > 8 or (
                isinstance(node, exp.Is) and not isinstance(node.expression, exp.Null)
            ):
                reject()
            scalar(node.this)
            scalar(node.expression)
        else:
            reject()

    def binding(node, depth=0):
        if depth > 32:
            reject()
        if isinstance(node, exp.AggFunc):
            aggregate(node)
        elif node.sql(dialect="duckdb") in grouped:
            return
        elif isinstance(node, exp.Column):
            if not node.table and node.name.casefold() in aliases:
                binding(aliases[node.name.casefold()], depth + 1)
            else:
                reject()
        else:
            for child in node.iter_expressions():
                binding(child, depth + 1)

    predicate(having.this)
    for projection in tree.expressions:
        binding(projection)
    if tree.args.get("order"):
        binding(tree.args["order"])
