"""Owner-scoped registered metrics: explicit names, schema applicability and SQL evidence."""

import json
import re
import unicodedata
from typing import Literal
from uuid import UUID

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select
from sqlglot import exp

from app.datasets import find_dataset
from app.embeddings import get_embedding_provider
from app.errors import AppError
from app.models import KnowledgeChunk, KnowledgeDocument
from app.sql_guard import validate_sql


class MetricFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    column: str = Field(min_length=1, max_length=160)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: str | int | float | bool | None


class MetricContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    name: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list, max_length=8)
    expression: str = Field(min_length=1, max_length=512)
    filters: list[MetricFilter] = Field(default_factory=list, max_length=8)
    required_fields: list[str] = Field(min_length=1, max_length=100)


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def mentions(question, name):
    question, name = normalize(question), normalize(name)
    if not name:
        return False
    # Latin aliases must not accidentally match part of another identifier.
    if re.fullmatch(r"[a-z0-9_ ]+", name):
        return bool(re.search(r"(?<![a-z0-9_])" + re.escape(name) + r"(?![a-z0-9_])", question))
    return name in question


def parse_contract(document):
    text = document.content.strip()
    if len(text) > 4000:
        raise ValueError("Metric contract exceeds bounded format")
    if text.startswith("{"):
        contract = MetricContract.model_validate_json(text)
    else:
        # Narrow compatibility reader for the existing explicit SQL formula format.
        # Never infer missing formula/filter/aliases from prose. Extra predicates
        # or conjunctions need a structured registration instead.
        prefix = re.escape(document.title) + r"[：:]\s*(?:仅统计|只统计)\s+"
        pattern = (
            prefix + r"(?P<column>[a-zA-Z_]\w*)\s*=\s*'(?P<value>[^']{0,80})'"
            r"\s*的[^，。]{1,20}[，,]\s*(?:计算\s+(?P<first>[^。]+)|"
            r"按\s+(?P<second>[^。]+?)\s+计算)。(?:退款订单不计入。)?"
        )
        match = re.fullmatch(pattern, text)
        if match:
            expression = match["first"] or match["second"]
            value = match["value"]
        else:
            # Existing prose registrations can also state a single aggregate
            # and equality explicitly. This grammar is independent of metric
            # names/data values; no formula is guessed from a metric's label.
            prose = (
                prefix + r"(?P<column>[a-zA-Z_]\w*)\s*=\s*"
                r"(?P<value>'[^']{1,80}'|[a-zA-Z0-9_]+)\s+的\s+"
                r"(?P<measure>[a-zA-Z_]\w*)\s*(?P<aggregate>总和|平均值|非空计数)"
                r"(?:[，,]退款不计入)?。"
            )
            match = re.fullmatch(prose, text)
            if not match:
                raise ValueError("Unstructured definition requires explicit registration")
            function = {"总和": "SUM", "平均值": "AVG", "非空计数": "COUNT"}[match["aggregate"]]
            expression = f'{function}("{match["measure"]}")'
            value = match["value"].strip("'")
        if len(re.findall(r"[a-zA-Z_]\w*\s*(?:[=<>!]+)", text)) != 1:
            raise ValueError("Unparsed legacy predicates")
        parsed = sqlglot.parse_one("SELECT " + expression + " FROM data", read="duckdb")
        columns = {c.name for c in parsed.find_all(exp.Column)} | {match["column"]}
        contract = MetricContract(
            version=1,
            name=document.title,
            expression=expression,
            filters=[MetricFilter(column=match["column"], operator="eq", value=value)],
            required_fields=sorted(columns),
        )
    if normalize(contract.name) != normalize(document.title):
        raise ValueError("Name must match registered document title")
    if any(not normalize(a) or len(a) > 160 for a in contract.aliases):
        raise ValueError("Invalid registered alias")
    if len({normalize(n) for n in [contract.name, *contract.aliases]}) != 1 + len(contract.aliases):
        raise ValueError("Duplicate normalized names")
    return contract


def filter_expression(item):
    column = exp.column(item.column, quoted=True)
    if item.value is None:
        if item.operator not in {"eq", "ne"}:
            raise ValueError("NULL ordering filter is undefined")
        result = exp.Is(this=column, expression=exp.Null())
        return exp.Not(this=result) if item.operator == "ne" else result
    classes = {
        "eq": exp.EQ,
        "ne": exp.NEQ,
        "gt": exp.GT,
        "gte": exp.GTE,
        "lt": exp.LT,
        "lte": exp.LTE,
    }
    return classes[item.operator](this=column, expression=exp.convert(item.value))


def validate_contract(contract, fields):
    names = {f["name"] for f in fields}
    original = sqlglot.parse_one("SELECT " + contract.expression + " FROM data", read="duckdb")
    if len(original.expressions) != 1 or isinstance(original.expressions[0], exp.Alias):
        raise ValueError("One unaliased expression required")
    sql = validate_sql("SELECT " + contract.expression + " FROM data", fields)
    tree = sqlglot.parse_one(sql, read="duckdb")
    if len(tree.expressions) != 1 or not list(tree.find_all(exp.AggFunc)):
        raise ValueError("One aggregate expression required")
    if any(v for k, v in tree.args.items() if k not in {"expressions", "from_"}):
        raise ValueError("Expression cannot contain query clauses")
    used = {c.name for c in tree.find_all(exp.Column)} | {f.column for f in contract.filters}
    if not used <= set(contract.required_fields) or not set(contract.required_fields) <= names:
        raise ValueError("Required fields do not cover formula and filters")
    numeric = {
        f["name"]
        for f in fields
        if any(t in f["type"] for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL"))
    }
    for aggregate in tree.find_all(exp.Sum, exp.Avg):
        if any(c.name not in numeric for c in aggregate.find_all(exp.Column)):
            raise ValueError("Numeric aggregate requires numeric fields")
    for predicate in contract.filters:
        query = exp.select("*").from_("data").where(filter_expression(predicate))
        validate_sql(query.sql(dialect="duckdb"), fields)
    return tree.expressions[0].unalias()


def canonical(node):
    node = node.copy()
    if isinstance(node, exp.Alias):
        node = node.this

    def simplify(part):
        if isinstance(part, exp.Column):
            return exp.column(part.name)
        if isinstance(part, exp.Identifier):
            return exp.Identifier(this=part.name, quoted=False)
        if isinstance(part, exp.Paren):
            return part.this
        return part

    return node.transform(simplify).sql(dialect="duckdb", normalize=True)


class MetricRegistry:
    def __init__(self, session, owner_id, dataset_id, settings):
        self.dataset = find_dataset(UUID(dataset_id), session, owner_id)
        self.fields = self.dataset.profile.get("fields", [])
        self.enabled, self.items, self.field_aliases = False, [], []
        self.source = "unavailable"
        try:
            provider = get_embedding_provider(settings)
        except AppError:
            return
        self.enabled = True
        self.source = provider.source
        rows = session.execute(
            select(KnowledgeDocument, KnowledgeChunk)
            .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(
                KnowledgeDocument.owner_id == owner_id,
                or_(
                    KnowledgeDocument.dataset_id == dataset_id,
                    KnowledgeDocument.dataset_id.is_(None),
                ),
                KnowledgeDocument.status == "ready",
                KnowledgeDocument.embedding_space == provider.space,
                KnowledgeChunk.embedding_space == provider.space,
                KnowledgeChunk.chunk_index == 0,
                KnowledgeDocument.source_type.in_(["metric_definition", "field_alias"]),
            )
            .order_by(KnowledgeDocument.id)
            .limit(101)
        ).all()
        if len(rows) > 100:
            raise AppError(422, "metric_catalog_limit", "授权指标目录超过处理上限。")
        for document, chunk in rows:
            if document.source_type == "field_alias":
                try:
                    alias = json.loads(document.content)
                    if isinstance(alias, dict) and set(alias) == {"alias", "field"}:
                        if all(isinstance(alias[k], str) for k in alias):
                            self.field_aliases.append(alias)
                except (ValueError, TypeError):
                    pass
                continue
            source = {
                "chunk_id": chunk.id,
                "document_id": document.id,
                "chunk_index": 0,
                "title": document.title,
                "source_type": "metric_definition",
                "excerpt": chunk.content[:800],
                "similarity": None,
            }
            item = {"source": source, "names": [document.title], "status": "insufficient_evidence"}
            try:
                contract = parse_contract(document)
                item["contract"] = contract
                item["names"] += contract.aliases
                try:
                    validate_contract(contract, self.fields)
                    item["status"] = "resolved"
                except (AppError, ValueError, sqlglot.errors.SqlglotError):
                    item["status"] = "inapplicable"
            except (ValidationError, ValueError, TypeError, sqlglot.errors.SqlglotError):
                pass
            self.items.append(item)

    def resolve(self, text, *, question=False):
        if question:
            # Output column aliases are a display contract, not evidence that
            # the user requested a registered business metric.
            text = re.sub(r"(?:仅)?输出[^。；;\n]*", " ", text)
            text = re.sub(r"\bAS\s+[a-zA-Z_][a-zA-Z0-9_]*", " ", text, flags=re.I)
        matches = []
        for item in self.items:
            names = (
                [n for n in item["names"] if mentions(text, n)]
                if question
                else [n for n in item["names"] if normalize(text) == normalize(n)]
            )
            if names:
                matches.append((item, max(len(normalize(n)) for n in names)))
        if not matches:
            return {"status": "not_found", "items": []}
        if len(matches) != 1:
            return {"status": "ambiguous", "items": [m[0] for m in matches]}
        item = matches[0][0]
        return {"status": item["status"], "items": [item]}

    def lookup(self, name):
        if not self.enabled:
            return {
                "status": "not_configured",
                "resolution_status": "not_configured",
                "source": "unavailable",
                "citations": [],
            }
        resolved = self.resolve(name)
        # Preserve legacy exact-title read compatibility. Its classification
        # remains insufficient; API planning cannot use it to generate SQL.
        readable = resolved["status"] in {"resolved", "insufficient_evidence"}
        return {
            "status": "retrieved" if readable else "no_match",
            "resolution_status": resolved["status"],
            "source": self.source,
            "citations": [i["source"] for i in resolved["items"]] if readable else [],
        }

    def explicit_native(self, question):
        if re.search(
            r"行数|多少行|记录数|预览|count\s*(?:\(|rows)|how many rows|preview", question, re.I
        ):
            return True
        numeric = {
            f["name"]
            for f in self.fields
            if any(t in f["type"] for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL"))
        }
        if any(mentions(question, field) for field in numeric):
            return True
        return any(
            a["field"] in numeric and mentions(question, a["alias"]) for a in self.field_aliases
        )

    @staticmethod
    def summary(resolution):
        return {
            "status": resolution["status"],
            "document_ids": [i["source"]["document_id"] for i in resolution["items"][:4]],
            "chunk_ids": [i["source"]["chunk_id"] for i in resolution["items"][:4]],
        }

    @staticmethod
    def clarification(resolution):
        questions = {
            "not_found": "未找到适用于当前数据集的已登记指标，请提供正式名称或已登记别名。",
            "ambiguous": "存在多个同名或同别名指标，请明确采用哪份定义。",
            "inapplicable": "已登记指标所需字段或公式不适用于当前数据集，请确认定义。",
            "insufficient_evidence": "相关文档缺少可验证的指标公式与过滤口径，请补充结构化定义。",
        }
        return questions.get(resolution["status"], questions["insufficient_evidence"])

    def check_sql(self, sql, resolution):
        tree = sqlglot.parse_one(sql, read="duckdb")
        selected = {canonical(e) for e in tree.expressions}
        predicates = []

        def conjuncts(node):
            if isinstance(node, exp.And):
                conjuncts(node.this)
                conjuncts(node.expression)
            elif isinstance(node, exp.Paren):
                conjuncts(node.this)
            else:
                predicates.append(canonical(node))

        if tree.args.get("where"):
            conjuncts(tree.args["where"].this)
        for item in resolution["items"]:
            contract = item["contract"]
            if canonical(validate_contract(contract, self.fields)) not in selected or any(
                canonical(filter_expression(f)) not in predicates for f in contract.filters
            ):
                raise AppError(
                    422,
                    "metric_contract_violation",
                    "SQL未遵守已登记的指标公式或过滤口径，未执行查询。",
                )
