import json
import re


def describe_result(result: dict, provider: str, preference: str = "auto") -> tuple[dict, dict]:
    rows, columns = result["rows"], result["columns"]
    chart = {"kind": "table", "reason": "结果为空、行数不足或字段不适合图表。"}
    if 2 <= len(rows) <= 50 and len(columns) == 2 and preference != "table":
        x, y = columns[0]["name"], columns[1]["name"]
        xs, ys = [row[x] for row in rows], [row[y] for row in rows]
        if (
            all(value is not None for value in xs)
            and len({str(value) for value in xs}) == len(xs)
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in ys)
        ):
            kind = "bar"
            if all(
                isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value) for value in xs
            ):
                if xs == sorted(xs):
                    kind = "line"
            if preference == "pie" and len(rows) <= 10 and min(ys) >= 0 and sum(ys) > 0:
                kind = "pie"
            chart = {
                "kind": kind,
                "x": x,
                "y": y,
                "reason": "图表逐行使用本次查询结果，不补数或重算。",
            }
    limitations = ["统计口径由 SQL 定义；本次结果不提供因果推断或未来预测。"]
    if result["truncated"]:
        limitations.append("结果已达到行数上限；图表、导出和事实仅覆盖返回的部分数据。")
    if provider == "mock":
        limitations.append("当前为确定性 Mock 模板，仅用于验证数据执行流程。")
    facts = [f"实际查询返回 {len(rows)} 行、{len(columns)} 列。"]
    if rows:
        first = {
            name: (value[:160] if isinstance(value, str) else value)
            for name, value in list(rows[0].items())[:3]
        }
        facts.append("结果首行示例：" + json.dumps(first, ensure_ascii=False, allow_nan=False))
    return chart, {"facts": facts, "hypotheses": [], "limitations": limitations}
