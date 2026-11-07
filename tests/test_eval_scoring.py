"""
test_eval_scoring.py - tests for the result comparison in eval/run_eval.py.

The scorer decides what counts as "correct", so it must itself be correct:
lenient on harmless differences, strict on real ones.
"""

import pandas as pd

from eval.run_eval import results_match, values_equal


def test_column_names_do_not_matter():
    reference = pd.DataFrame({"x": ["a", "b"], "y": [1, 2]})
    predicted = pd.DataFrame({"name": ["a", "b"], "total_revenue": [1.0, 2.0]})
    assert results_match(reference, predicted)


def test_extra_columns_and_column_order_are_allowed():
    reference = pd.DataFrame({"name": ["a", "b"], "spent": [10.0, 20.0]})
    predicted = pd.DataFrame({"spent": [10.0, 20.0], "city": ["X", "Y"], "name": ["a", "b"]})
    assert results_match(reference, predicted)


def test_row_order_ignored_unless_it_matters():
    reference = pd.DataFrame({"name": ["a", "b"], "v": [1, 2]})
    predicted = pd.DataFrame({"name": ["b", "a"], "v": [2, 1]})
    assert results_match(reference, predicted, order_matters=False)
    assert not results_match(reference, predicted, order_matters=True)


def test_rounding_tolerance():
    assert values_equal(10.28, 10.285)
    assert not values_equal(10.28, 10.3)
    assert results_match(pd.DataFrame({"v": [1234.5]}), pd.DataFrame({"v": [1234.499]}))


def test_values_must_line_up_in_the_same_rows():
    """Each column matches on its own, but the pairs are swapped -> wrong."""
    reference = pd.DataFrame({"name": ["a", "b"], "v": [1, 2]})
    predicted = pd.DataFrame({"name": ["a", "b"], "v": [2, 1]})
    assert not results_match(reference, predicted)


def test_different_row_count_is_wrong():
    reference = pd.DataFrame({"name": ["a", "b", "c"]})
    predicted = pd.DataFrame({"name": ["a", "b"]})
    assert not results_match(reference, predicted)


def test_missing_reference_column_is_wrong():
    reference = pd.DataFrame({"name": ["a"], "v": [5]})
    predicted = pd.DataFrame({"name": ["a"]})
    assert not results_match(reference, predicted)


def test_null_values_compare_equal():
    reference = pd.DataFrame({"month": ["2026-01", "2026-02"], "change": [None, 5.0]})
    predicted = pd.DataFrame({"month": ["2026-01", "2026-02"], "change": [float("nan"), 5.0]})
    assert results_match(reference, predicted)


def test_wrong_values_are_wrong():
    assert not results_match(pd.DataFrame({"n": [3200]}), pd.DataFrame({"n": [2825]}))
