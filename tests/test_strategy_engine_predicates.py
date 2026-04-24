"""Tests for strategy_engine.predicates — parse_predicate + evaluate_predicate."""
from __future__ import annotations

import pytest

from strategy_engine.predicates import (
    PredicateError,
    UnsupportedExpression,
    evaluate_predicate,
    parse_predicate,
)


# ================================================================== #
# parse_predicate
# ================================================================== #
@pytest.mark.parametrize("expr,op,lhs,rhs", [
    ("x.y == 5", "==", "x.y", 5),
    ("a != 1.5", "!=", "a", 1.5),
    ("foo.bar > 0.6", ">", "foo.bar", 0.6),
    ("foo.bar >= 0.6", ">=", "foo.bar", 0.6),
    ("a < 0", "<", "a", 0),
    ("a <= -3.14", "<=", "a", -3.14),
    ("flag == true", "==", "flag", True),
    ("flag == false", "==", "flag", False),
    ('name == "BTC"', "==", "name", "BTC"),
    ("name == 'ETH'", "==", "name", "ETH"),
])
def test_parse_compare_ops(expr, op, lhs, rhs):
    got = parse_predicate(expr)
    assert got == (op, lhs, rhs)


def test_parse_in_list():
    op, lhs, rhs = parse_predicate('regime in ["BULL_TRENDING", "BEAR"]')
    assert op == "in"
    assert lhs == "regime"
    assert rhs == ["BULL_TRENDING", "BEAR"]


def test_parse_in_empty_list():
    op, lhs, rhs = parse_predicate("x in []")
    assert (op, lhs, rhs) == ("in", "x", [])


def test_parse_in_numbers():
    _, _, rhs = parse_predicate("x in [1, 2, 3]")
    assert rhs == [1, 2, 3]


def test_parse_empty_raises():
    with pytest.raises(PredicateError, match="empty"):
        parse_predicate("   ")


def test_parse_no_operator_raises():
    with pytest.raises(UnsupportedExpression):
        parse_predicate("just_a_field")


def test_parse_compound_logic_unsupported():
    """`and`, `or`, `not` are by design not supported — use blocks.
    Accepts either UnsupportedExpression or PredicateError (parser splits on
    op which leaves a non-field-path lhs)."""
    with pytest.raises(PredicateError):
        parse_predicate("a > 1 and b < 2")


def test_parse_invalid_field_path_raises():
    with pytest.raises(PredicateError):
        parse_predicate("1+1 == 2")


def test_parse_unparseable_literal_raises():
    """Bare identifier on rhs (cross-field comparison) not supported."""
    with pytest.raises(PredicateError, match="cannot parse literal"):
        parse_predicate("a == b")


# ================================================================== #
# evaluate_predicate
# ================================================================== #
def test_evaluate_compare_passes():
    ctx = {"fused": {"ensemble_score": 0.7}}
    assert evaluate_predicate("fused.ensemble_score >= 0.6", ctx) is True


def test_evaluate_compare_fails():
    ctx = {"fused": {"ensemble_score": 0.4}}
    assert evaluate_predicate("fused.ensemble_score >= 0.6", ctx) is False


def test_evaluate_strict_inequality():
    ctx = {"x": 0.6}
    assert evaluate_predicate("x > 0.6", ctx) is False
    assert evaluate_predicate("x >= 0.6", ctx) is True


def test_evaluate_string_eq():
    ctx = {"regime": "BULL_TRENDING"}
    assert evaluate_predicate('regime == "BULL_TRENDING"', ctx) is True
    assert evaluate_predicate('regime == "BEAR"', ctx) is False


def test_evaluate_bool():
    ctx = {"flag": True}
    assert evaluate_predicate("flag == true", ctx) is True
    assert evaluate_predicate("flag == false", ctx) is False


def test_evaluate_nested_path():
    ctx = {"a": {"b": {"c": 42}}}
    assert evaluate_predicate("a.b.c == 42", ctx) is True


def test_evaluate_in_membership():
    ctx = {"regime": "BULL_TRENDING"}
    assert evaluate_predicate('regime in ["BULL_TRENDING", "BEAR"]', ctx) is True
    assert evaluate_predicate('regime in ["SIDEWAYS"]', ctx) is False


def test_evaluate_missing_field_returns_false():
    """Missing field → False (defensive). Strategy authors should not
    write predicates depending on optional fields and expect them to default."""
    ctx = {"a": {"b": 1}}
    assert evaluate_predicate("a.b.c == 1", ctx) is False
    assert evaluate_predicate("nonexistent == 1", ctx) is False


def test_evaluate_type_mismatch_returns_false():
    """Comparing string to int → False (no exception)."""
    ctx = {"x": "abc"}
    assert evaluate_predicate("x > 5", ctx) is False


def test_evaluate_attribute_access_on_dataclass():
    """Last-ditch attr access — supports dataclass contexts as well as dicts."""
    from dataclasses import dataclass
    @dataclass
    class Fused:
        ensemble_score: float = 0.7
    ctx = {"fused": Fused()}
    assert evaluate_predicate("fused.ensemble_score >= 0.6", ctx) is True
