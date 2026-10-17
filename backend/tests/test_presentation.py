import pytest

from app.presentation import describe_result


@pytest.mark.parametrize(
    "rows,preference,expected",
    [
        ([{"x": "East", "y": 40}, {"x": "West", "y": 20}], "auto", "bar"),
        ([{"x": "2026-01-01", "y": 10}, {"x": "2026-02-01", "y": 20}], "auto", "line"),
        ([{"x": "2026-02-01", "y": 20}, {"x": "2026-01-01", "y": 10}], "auto", "bar"),
        ([{"x": "East", "y": 40}, {"x": "West", "y": 20}], "pie", "pie"),
        ([{"x": "East", "y": -40}, {"x": "West", "y": 20}], "pie", "bar"),
        ([{"x": "East", "y": None}, {"x": "West", "y": 20}], "auto", "table"),
        ([{"x": "East", "y": 10}, {"x": "East", "y": 20}], "auto", "table"),
        ([{"x": "East", "y": "9007199254740993"}, {"x": "West", "y": "20"}], "auto", "table"),
        ([], "auto", "table"),
    ],
)
def test_charts_respect_actual_values_order_nulls_and_precision(rows, preference, expected):
    result = {"columns": [{"name": "x"}, {"name": "y"}], "rows": rows, "truncated": False}
    chart, summary = describe_result(result, "mock", preference)
    assert chart["kind"] == expected
    assert summary["hypotheses"] == []
    if expected != "table":
        assert chart["x"] == "x" and chart["y"] == "y"
