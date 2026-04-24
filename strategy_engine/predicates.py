"""Minimal predicate language — parse + evaluate.

Why minimal: we want users hand-writing strategy YAML, but we also want
deterministic behaviour and easy security audit. So this parser supports
exactly:
  - Atom literals: true / false / numbers / quoted strings
  - Field paths: a.b.c (looked up in context dict)
  - Comparison: == != < <= > >=
  - Membership: x in [literal1, literal2, ...]

Everything else (and / or / not / arithmetic / function calls) is by
DESIGN unsupported. The strategy DSL block structure (all_of/any_of/
none_of) handles compound logic.

No eval() ever. The parser is a small hand-written one (< 100 lines).
"""
from __future__ import annotations

import re
from typing import Any


class PredicateError(ValueError):
    """Generic predicate failure (parse or eval)."""


class UnsupportedExpression(PredicateError):
    """Tried to use a syntax we deliberately don't support."""


_OPS = ["==", "!=", "<=", ">=", "<", ">"]   # order matters: longest first
_TOKEN_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_TOKEN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_TOKEN_STRING = re.compile(r'^"([^"]*)"$|^\'([^\']*)\'$')


# ================================================================== #
# Public API
# ================================================================== #
def parse_predicate(expr: str) -> tuple[str, str, Any]:
    """Parse 'lhs op rhs' or 'lhs in [rhs1, rhs2, ...]'.

    Returns (op, lhs_field_path, rhs_value).
      op is one of: '==' '!=' '<' '<=' '>' '>=' 'in'
      lhs is the dotted field path (still a string — not yet resolved)
      rhs is the parsed literal (number / bool / string / list of literals)

    Raises:
        PredicateError: malformed expression
        UnsupportedExpression: syntax we don't support
    """
    s = expr.strip()
    if not s:
        raise PredicateError("empty predicate")

    # Membership: handle BEFORE comparison since `in` is a keyword
    if " in " in s:
        return _parse_in(s)

    for op in _OPS:
        # Use whitespace boundary to avoid matching inside identifiers
        # ("a==b" is rare but should work; we still need 'op' between tokens)
        if op in s:
            lhs, rhs = s.split(op, 1)
            lhs, rhs = lhs.strip(), rhs.strip()
            if not lhs or not rhs:
                raise PredicateError(f"malformed comparison: {expr!r}")
            if not _TOKEN_FIELD.match(lhs):
                raise PredicateError(f"lhs must be a field path: {lhs!r} in {expr!r}")
            return (op, lhs, _parse_literal(rhs))

    raise UnsupportedExpression(
        f"no supported operator found in {expr!r} — "
        f"use one of {_OPS} or 'in [...]'"
    )


def evaluate_predicate(expr: str, context: dict[str, Any]) -> bool:
    """Parse + evaluate against context. False on missing field (defensive)."""
    op, lhs_path, rhs = parse_predicate(expr)
    try:
        lhs_val = _resolve_path(lhs_path, context)
    except KeyError:
        # Missing field treated as "predicate doesn't apply" → False rather
        # than raising. Strategy authors shouldn't write predicates that
        # depend on optional fields and expect them to default to anything.
        return False

    return _compare(op, lhs_val, rhs)


# ================================================================== #
# Helpers
# ================================================================== #
def _parse_in(s: str) -> tuple[str, str, list]:
    lhs, rest = s.split(" in ", 1)
    lhs = lhs.strip()
    rest = rest.strip()
    if not lhs or not rest:
        raise PredicateError(f"malformed 'in' expression: {s!r}")
    if not (rest.startswith("[") and rest.endswith("]")):
        raise PredicateError(f"'in' rhs must be [list]: {s!r}")
    body = rest[1:-1].strip()
    if not body:
        return ("in", lhs, [])
    items = [_parse_literal(x.strip()) for x in body.split(",")]
    if not _TOKEN_FIELD.match(lhs):
        raise PredicateError(f"'in' lhs must be a field path: {lhs!r}")
    return ("in", lhs, items)


def _parse_literal(s: str) -> Any:
    """Parse a single rhs literal: number / bool / string."""
    s = s.strip()
    if s == "true":
        return True
    if s == "false":
        return False
    if s == "null" or s == "none":
        return None
    if _TOKEN_NUMBER.match(s):
        return float(s) if "." in s else int(s)
    m = _TOKEN_STRING.match(s)
    if m:
        return m.group(1) if m.group(1) is not None else m.group(2)
    # Bare identifier rhs? We don't support cross-field comparison.
    raise PredicateError(
        f"cannot parse literal {s!r} (use a number, bool, or quoted string)"
    )


def _resolve_path(path: str, ctx: dict[str, Any]) -> Any:
    """Walk dotted path. Raises KeyError on miss."""
    current: Any = ctx
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(path)
            current = current[part]
        else:
            # Last-ditch: try attribute access (for dataclasses)
            if not hasattr(current, part):
                raise KeyError(path)
            current = getattr(current, part)
    return current


def _compare(op: str, lhs: Any, rhs: Any) -> bool:
    if op == "==":
        return lhs == rhs
    if op == "!=":
        return lhs != rhs
    if op == "in":
        return lhs in rhs
    # Numeric / ordered comparisons — coerce floats for safety
    try:
        if op == "<":
            return lhs < rhs
        if op == "<=":
            return lhs <= rhs
        if op == ">":
            return lhs > rhs
        if op == ">=":
            return lhs >= rhs
    except TypeError:
        # Comparing incompatible types → False (predicate doesn't apply)
        return False
    raise UnsupportedExpression(f"unknown op {op!r}")


__all__ = [
    "PredicateError",
    "UnsupportedExpression",
    "parse_predicate",
    "evaluate_predicate",
]
