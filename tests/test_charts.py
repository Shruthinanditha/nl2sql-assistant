"""
test_charts.py - tests for the automatic chart choice in src/charts.py.
No database or LLM needed: we build small DataFrames by hand.
"""

import pandas as pd

from src.charts import build_figure, choose_chart, format_number, pretty_label


def test_single_number_is_a_metric():
    spec = choose_chart(pd.DataFrame({"order_count": [3200]}))
    assert spec.kind == "metric"
    assert spec.y == "order_count"


def test_month_and_number_is_a_line():
    df = pd.DataFrame({"month": ["2026-01", "2026-02", "2026-03"], "revenue": [10.0, 12.5, 9.0]})
    spec = choose_chart(df)
    assert (spec.kind, spec.x, spec.y) == ("line", "month", "revenue")
    figure = build_figure(df, spec)
    assert figure.data[0].type == "scatter"
    assert figure.data[0].line.width == 2


def test_daily_dates_are_a_line():
    df = pd.DataFrame({"order_date": ["2026-11-01", "2026-11-02"], "orders": [5, 7]})
    assert choose_chart(df).kind == "line"


def test_category_and_number_is_a_bar():
    df = pd.DataFrame({"status": ["completed", "cancelled", "pending"], "n": [2825, 329, 46]})
    spec = choose_chart(df)
    assert (spec.kind, spec.x, spec.y) == ("bar", "status", "n")
    figure = build_figure(df, spec)
    assert figure.data[0].type == "bar"
    assert figure.data[0].orientation == "v"  # short labels, few bars


def test_long_labels_make_horizontal_bars_in_sql_order():
    df = pd.DataFrame({"product": ["Noise Cancelling Headphones", "Wireless Earbuds"], "revenue": [9.0, 5.0]})
    figure = build_figure(df, choose_chart(df))
    assert figure.data[0].orientation == "h"
    assert figure.layout.yaxis.autorange == "reversed"  # first SQL row on top


def test_id_columns_are_not_plotted():
    df = pd.DataFrame({"customer_id": [1, 2], "name": ["A", "B"]})
    assert choose_chart(df).kind == "table"


def test_too_many_categories_fall_back_to_table():
    df = pd.DataFrame({"name": [f"c{i}" for i in range(40)], "value": range(40)})
    assert choose_chart(df).kind == "table"


def test_empty_or_text_only_results_are_tables():
    assert choose_chart(pd.DataFrame()).kind == "table"
    assert choose_chart(None).kind == "table"
    assert choose_chart(pd.DataFrame({"name": ["a", "b"]})).kind == "table"


def test_null_category_is_labelled_unknown():
    df = pd.DataFrame({"city": ["Chennai", None], "customers": [34, 5]})
    figure = build_figure(df, choose_chart(df))
    assert "(unknown)" in list(figure.data[0].x)


def test_label_and_number_formatting():
    assert pretty_label("total_revenue") == "Total revenue"
    assert format_number(2825) == "2,825"
    assert format_number(36791.5) == "36,791.50"
