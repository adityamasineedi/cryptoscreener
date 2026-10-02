"""Generic row filters for screener tables."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from app.models.schemas import FreshValue

OPERATORS = frozenset(
    {"=", "!=", ">", ">=", "<", "<=", "between", "in", "contains"}
)


def _resolve_value(row: Mapping[str, Any], field: str) -> Any:
    if field not in row:
        return None
    val = row[field]
    if isinstance(val, FreshValue):
        return val.value
    if isinstance(val, Mapping) and "value" in val:
        inner = val["value"]
        if isinstance(inner, FreshValue):
            return inner.value
        return inner
    return val


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if op in {">", ">=", "<", "<="}:
        if left is None or right is None:
            return False
        try:
            lf, rf = float(left), float(right)
        except (TypeError, ValueError):
            return False
        if op == ">":
            return lf > rf
        if op == ">=":
            return lf >= rf
        if op == "<":
            return lf < rf
        return lf <= rf
    if op == "between":
        if not isinstance(right, (list, tuple)) or len(right) != 2:
            return False
        lo, hi = right[0], right[1]
        try:
            lf = float(left)
            return float(lo) <= lf <= float(hi)
        except (TypeError, ValueError):
            return False
    if op == "in":
        if not isinstance(right, (list, tuple, set, frozenset)):
            return False
        return left in right
    if op == "contains":
        if left is None or right is None:
            return False
        if isinstance(left, str):
            return str(right).lower() in left.lower()
        if isinstance(left, (list, tuple, set)):
            return right in left
        return False
    return False


def apply_filter(row: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    field = spec.get("field")
    op = spec.get("operator")
    value = spec.get("value")
    if not field or op not in OPERATORS:
        return True
    left = _resolve_value(row, str(field))
    if left is None:
        return False
    return _compare(str(op), left, value)


def apply_filters(
    rows: list[dict[str, Any]],
    filters: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not filters:
        return list(rows)
    out: list[dict[str, Any]] = []
    for row in rows:
        if all(apply_filter(row, f) for f in filters):
            out.append(row)
    return out


def parse_filter_pattern(text: str) -> dict[str, Any] | None:
    """Optional helper: field:op:value quick syntax."""
    m = re.match(r"^\s*(\w+)\s*(=|!=|>=|<=|>|<)\s*(.+)\s*$", text)
    if not m:
        return None
    field, op, raw = m.group(1), m.group(2), m.group(3)
    try:
        val: Any = float(raw)
        if val.is_integer():
            val = int(val)
    except ValueError:
        val = raw.strip().strip("'\"")
    return {"field": field, "operator": op, "value": val}
